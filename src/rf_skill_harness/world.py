"""Dataset-independent GNU Radio World design and native deployment adapter."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile

from .companion import init_companion, starter_graph, export_companion, preview

RX_TOKEN = 'RF_RECEIVE'


def init_world(output, name='my-rf-skill', *, transmit=False):
    result = init_companion(output, name, transmit=transmit)
    root = Path(result['project'])
    io_path = root / 'skill_io.py'
    io_path.write_text(io_path.read_text().replace(
        '        module = importlib.import_module(graph_module)\n',
        '        module = importlib.import_module(graph_module)\n'
        '        for name, loaded in list(sys.modules.items()):\n'
        '            if name.startswith(graph_module + "_") and hasattr(loaded, "RF_WINDOW"):\n'
        '                loaded.RF_WINDOW, loaded.RF_CONTEXT = metadata, context\n'))
    graph = starter_graph(transmit)
    for block in graph['blocks']:
        if block['id'] == 'parameter':
            block['id'] = 'variable'
            block['parameters'] = {'value': block['parameters']['value']}
        if block['id'] == 'rfskill_rx':
            block.update(id='blocks_file_source', parameters={'type': 'complex', 'vlen': 'channel_count',
                'file': RX_TOKEN, 'repeat': 'False', 'offset': '0', 'length': '0', 'begin_tag': 'pmt.PMT_NIL'})
        if block['id'] in ('rfskill_measurements', 'rfskill_tx'):
            block.update(id='blocks_null_sink', parameters={'type': 'complex', 'vlen': 'channel_count' if block['name'] == 'measurements' else '1'})
    for block in graph['blocks']:
        if block['id'] == 'epy_block':
            block['parameters']['_io_cache'] = json.dumps({'cls': 'blk', 'label': 'Custom Python DSP',
                'doc': 'Replace this gain with your streaming DSP.', 'params': [['channels', '1'], ['gain', '1.0']],
                'callbacks': ['gain'], 'sinks': [['0', 'complex', 1]], 'sources': [['0', 'complex', 1]],
                'msg_ports_in': [], 'msg_ports_out': [], 'block_type': 'sync'})
    graph['options']['parameters']['title'] = 'RF Skill — GNU Radio World'
    graph['options']['parameters']['run_command'] = ''
    (root / 'skill.grc').write_text(json.dumps(graph, indent=2) + '\n')
    spec_path = root / 'skill.json'
    spec = json.loads(spec_path.read_text())
    spec['world'] = {'profile': 'finite-window-v1', 'rx': 'receive', 'measurements': 'measurements',
                     'tx': 'transmit' if transmit else None}
    spec_path.write_text(json.dumps(spec, indent=2) + '\n')
    return result


def native_graph(graph, roles):
    """Replace only declared acquisition endpoints, retaining the edited DSP."""
    graph = deepcopy(graph)
    blocks = {b['name']: b for b in graph['blocks']}
    expected = [(roles['rx'], 'blocks_file_source', 'rfskill_rx'),
                (roles['measurements'], 'blocks_null_sink', 'rfskill_measurements')]
    if roles.get('tx'):
        expected.append((roles['tx'], 'blocks_null_sink', 'rfskill_tx'))
    for name, browser_id, native_id in expected:
        block = blocks.get(name)
        if not block or block['id'] != browser_id or block.get('states', {}).get('state', 'enabled') != 'enabled':
            raise ValueError('Missing or changed RF endpoint: ' + name)
        if native_id == 'rfskill_rx' and block['parameters'].get('file') != RX_TOKEN:
            raise ValueError('RF receive input must use the dataset-independent RF_RECEIVE placeholder')
        block.update(id=native_id, parameters=({'channels': 'channel_count'} if native_id == 'rfskill_rx'
            else {'channels': 'channel_count', 'processor': 'processor.py:process_window'} if native_id == 'rfskill_measurements' else {}))
    required = {'channel_count': 'intx', 'samp_rate': 'eng_float', 'carrier_hz': 'eng_float', 'tx_samples': 'intx'}
    for name, dtype in required.items():
        block = blocks.get(name)
        if not block or block['id'] != 'variable':
            raise ValueError('Keep the RF configuration variable: ' + name)
        block['id'] = 'parameter'
        block['parameters']['type'] = dtype
    graph['blocks'] = [b for b in graph['blocks'] if b['id'] not in ('gui_layout', 'wasm_gui_layout')]
    for block in graph['blocks']:
        if block['id'] == 'epy_block':
            # Same context names in browser replay and the exported native host.
            block['parameters']['_source_code'] = ('from skill_io import session as _rf_session\n'
                'try:\n    RF_WINDOW = _rf_session().metadata\n    RF_CONTEXT = _rf_session().context\n'
                'except RuntimeError:\n    RF_WINDOW = {}\n    RF_CONTEXT = {}\n' + block['parameters']['_source_code'])
        if block['id'].startswith('wasm_') or block['id'].startswith('js_'):
            raise ValueError('Browser-only block has no native deployment implementation: ' + block['name'])
    # World owns GUI layout; native GRC uses each Qt sink's gui_hint.
    graph['blocks'] = [b for b in graph['blocks'] if b['id'] != 'gui_layout']
    return graph


def staged_native(workspace, directory):
    from .companion import read_graph
    root = Path(workspace).resolve()
    spec = json.loads((root / 'skill.json').read_text())
    graph = native_graph(read_graph(root / 'skill.grc'), spec['world'])
    staged = Path(directory) / 'project'
    staged.mkdir()
    for name in spec['files']:
        source = root / name
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError('Only project-local source files can be deployed')
        target = staged / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copytree(root / 'config', staged / 'config')
    (staged / 'skill.json').write_text(json.dumps(spec, indent=2) + '\n')
    (staged / 'skill.grc').write_text(json.dumps(graph, indent=2) + '\n')
    return staged


def export_world(workspace, output, *, keep_visualizations=()):
    with tempfile.TemporaryDirectory(prefix='rf-world-export-') as temporary:
        return export_companion(staged_native(workspace, temporary), output,
                                keep_visualizations=keep_visualizations)


def preview_world(workspace, recording, *, index=0):
    with tempfile.TemporaryDirectory(prefix='rf-world-preview-') as temporary:
        return preview(staged_native(workspace, temporary), recording, index=index, plots=False)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['preview', 'export'])
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--recording', type=Path)
    parser.add_argument('--window', type=int, default=0)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--keep-plot', action='append', default=[])
    args = parser.parse_args()
    if args.command == 'preview':
        result = preview_world(args.workspace, args.recording, index=args.window)
    else:
        result = export_world(args.workspace, args.output, keep_visualizations=args.keep_plot)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
