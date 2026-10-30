"""SigMF inspection artifacts and Brian Day's actual SigMF Viewer application.

Inspection is development instrumentation. No viewer, output path, replay ticket
or captured I/Q is inserted into a skill's deployment design.
"""
from dataclasses import replace
from hashlib import sha256
import json
import math
from pathlib import Path
from uuid import uuid4

import numpy as np

MAX_BYTES = 256 * 1024 * 1024


class InspectionStore:
    def __init__(self, workspace):
        self.root = Path(workspace) / 'inspections'
        self.root.mkdir(exist_ok=True)
        self.pending = {}
        self.on_saved = lambda: None

    def prepare(self, metadata, context, *, block='receive', port=0, channels=1,
                sample_rate=None, datatype='cf32_le', units='volts'):
        rate = context['sample_rate_hz'] if sample_rate is None else sample_rate
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not math.isfinite(rate) or rate <= 0:
            raise ValueError('Inspection sample rate must be positive and finite')
        if type(channels) is not int or not 1 <= channels <= 256:
            raise ValueError('Inspection channel count must be in 1..256')
        if datatype not in ('cf32_le', 'rf32_le'):
            raise ValueError('Inspect complex64 or float32 stream outputs')
        if not isinstance(block, str) or not block.isidentifier() or type(port) is not int or port < 0:
            raise ValueError('Choose a valid stream block and port')
        token = uuid4().hex
        self.pending[token] = {'metadata': dict(metadata), 'context': context,
            'block': block, 'port': port, 'channels': channels, 'rate': rate,
            'datatype': datatype, 'units': units}
        # Bound abandoned selections without removing saved inspection artifacts.
        while len(self.pending) > 16:
            self.pending.pop(next(iter(self.pending)))
        return token

    def save(self, token, payload):
        value = self.pending[token]
        width = 8 if value['datatype'] == 'cf32_le' else 4
        if not payload or len(payload) > MAX_BYTES or len(payload) % (width * value['channels']):
            raise ValueError('Inspection must contain complete samples and channels (maximum 256 MiB)')
        samples = np.frombuffer(payload, '<c8' if width == 8 else '<f4')
        if not np.isfinite(samples).all():
            raise ValueError('Nonfinite inspection samples')
        directory = self.root / token
        directory.mkdir()
        name = 'signal'
        context, meta = value['context'], value['metadata']
        channels = context['channels'] if len(context['channels']) == value['channels'] else [f'channel-{i}' for i in range(value['channels'])]
        doc = {'global': {'core:datatype': value['datatype'], 'core:version': '1.2.0',
            'core:sample_rate': value['rate'], 'core:num_channels': value['channels'],
            'core:description': f"{value['block']}:{value['port']} — window {meta['index']}",
            'sigmf_viewer:channel_names': channels, 'rf_harness:units': value['units'],
            'rf_harness:parent_timestamp_ns': str(meta['timestamp_ns']),
            'rf_harness:parent_sample_start': meta['sample_start'],
            'rf_harness:parent_sample_rate_hz': context['sample_rate_hz'],
            'rf_harness:window': meta, 'rf_harness:receiver_config': context['receiver_config'],
            'rf_harness:tap': {'block': value['block'], 'port': value['port']},
            'core:sha512': __import__('hashlib').sha512(payload).hexdigest()},
            'captures': [{'core:sample_start': 0, 'core:frequency': context['carrier_hz']}],
            'annotations': []}
        (directory / (name + '.sigmf-data')).write_bytes(payload)
        temporary = directory / 'metadata.tmp'
        temporary.write_text(json.dumps(doc, indent=2, allow_nan=False) + '\n')
        temporary.replace(directory / (name + '.sigmf-meta'))
        del self.pending[token]
        self.on_saved()
        return {'id': token, 'samples': len(payload) // (width * value['channels']),
                'sha256': sha256(payload).hexdigest(),
                'metadata': str(directory / (name + '.sigmf-meta')),
                'url': f'/workspace/inspections/item/{token}::signal'}


def create_viewer(state):
    """Use the upstream workspace and HTTP handler; do not reimplement its plots."""
    from sigmf_viewer.workspace import create_workspace
    from sigvue.web.application import create_app, _make_handler
    app = create_app(title='SigMF Viewer · RF Skill Harness', reload_workspaces=False)
    for identifier, name, root, writable in [
            ('inspections', 'Signal inspections', state.inspections.root, True),
            *[(f'capture-{i}', label, path, False) for i, (label, path) in enumerate(state.captures.items())]]:
        workspace = create_workspace({'data_root': str(root),
            'output_root': str(state.inspections.root / 'batch-results' / identifier),
            'default_window_seconds': .004, 'minimum_window_seconds': .000001,
            'window_step_seconds': .001, 'overview_bins': 300})
        workspace.metadata = replace(workspace.metadata, identifier=identifier, display_name=name)
        if not writable:
            workspace.annotator = None
        app.register_workspace(workspace)
    def refresh_inspections():
        workspace = app.registry.get('inspections')
        with workspace._cache_lock:
            workspace._resource_cache = None
    state.inspections.on_saved = refresh_inspections
    return app, _make_handler(app)


def integrated_handler(state):
    from .world_server import Handler
    app, UpstreamHandler = create_viewer(state)
    prefixes = ('/workspace/', '/workspaces', '/results/', '/exports/', '/batches', '/batch-files/', '/batch-browser/', '/workspace-factories')

    class IntegratedHandler(Handler, UpstreamHandler):
        def viewer_request(self):
            path = self.path.split('?', 1)[0]
            return path == '/sigmf/' or path == '/assets/plotly.min.js' or path.startswith(prefixes)

        def _write_html(self, payload):
            payload = payload.replace('</head>', '<style>#workspace-add{display:none!important}</style></head>')
            return UpstreamHandler._write_html(self, payload)

        def do_GET(self):
            if not self.allowed():
                return self.json({'error': 'Use localhost'}, 403)
            if self.path.startswith('/workspace-setup') or self.path.startswith('/workspace-factories'):
                return self.json({'factories': []})
            if self.viewer_request():
                original = self.path
                if self.path == '/sigmf/': self.path = '/'
                try:
                    return UpstreamHandler.do_GET(self)
                finally:
                    self.path = original
            return Handler.do_GET(self)

        def do_POST(self):
            host, origin = self.headers.get('Host', ''), self.headers.get('Origin')
            if not self.allowed() or (origin and origin != 'http://' + host):
                return self.json({'error': 'Same-origin requests required'}, 403)
            if self.viewer_request():
                # Workspace wizard and source annotations must not expose/write
                # arbitrary host files. Only configured captures are registered.
                if self.path == '/workspaces' or self.path.startswith('/workspace-factories'):
                    return self.json({'error': 'Add captures with --recordings'}, 403)
                if self.headers.get('Content-Type') != 'application/json':
                    return self.json({'error': 'JSON required'}, 403)
                return UpstreamHandler.do_POST(self)
            if self.path.startswith('/rf/inspection/') and self.path.endswith('/data'):
                if self.headers.get('Content-Type') != 'application/octet-stream':
                    return self.json({'error': 'Binary stream payload required'}, 400)
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= MAX_BYTES: raise ValueError('Inspection limit is 256 MiB')
                    payload = self.rfile.read(length)
                    if len(payload) != length: raise ValueError('Truncated inspection upload')
                    with state.lock:
                        result = state.inspections.save(self.path.split('/')[3], payload)
                    return self.json(result)
                except (KeyError, ValueError, OSError) as error:
                    return self.json({'error': str(error)}, 400)
            return Handler.do_POST(self)

    return IntegratedHandler, app
