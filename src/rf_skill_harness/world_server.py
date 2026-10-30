"""Local GNU Radio World web server plus isolated native skill tools.

Only HTTP is served. GNU Radio World executes DSP and renders plots in the user's
browser; no X server, GTK/Qt window, VNC or host graphics integration is started.
"""
import argparse
from collections import OrderedDict
from functools import partial
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote
from uuid import uuid4

from .world import init_world


def browser_json(value):
    """Preserve acquisition nanoseconds beyond JavaScript's safe integer range."""
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53-1:
        return str(value)
    if isinstance(value, dict):
        return {k: browser_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [browser_json(v) for v in value]
    return value


class Workspace:
    def __init__(self, root, captures):
        self.root = Path(root).resolve()
        self.captures = dict(captures)
        from .sigmf_inspector import InspectionStore
        self.inspections = InspectionStore(self.root)
        self.window_info = {}
        self.windows = OrderedDict()
        self.lock = threading.Lock()
        self.children_lock = threading.Lock()
        self.children = set()
        self.stopping = False

    def files(self):
        spec = json.loads((self.root / 'skill.json').read_text())
        names = [n for n in spec['files'] if n not in ('skill_io.py', 'grc_processor.py')]
        names += [str(p.relative_to(self.root)) for p in (self.root / 'config').rglob('*.json')]
        return {n: self.local(n).read_text() for n in names}

    def local(self, name):
        path = self.root / name
        if path.is_symlink() or not path.resolve().is_relative_to(self.root):
            raise ValueError('File must be inside the skill project')
        return path

    def save(self, graph):
        from .world import native_graph
        spec = json.loads(self.local('skill.json').read_text())
        native_graph(graph, spec['world'])  # Validate endpoints before overwriting.
        text = json.dumps(graph, indent=2, allow_nan=False) + '\n'
        path = self.local('skill.grc')
        temporary = path.with_suffix('.grc.tmp')
        temporary.write_text(text)
        temporary.replace(path)

    def select(self, capture, index, graph):
        import numpy as np
        from .archive import Archive
        if capture not in self.captures:
            raise ValueError('Select a mounted test capture')
        archive = Archive(self.captures[capture])
        declared = int(next(b for b in graph['blocks'] if b['name'] == 'channel_count')['parameters']['value'])
        if declared != len(archive.channels):
            raise ValueError(f'Skill expects {declared} channels; capture has {len(archive.channels)}. Redesign explicitly or select a compatible capture.')
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < archive.manifest['windows']:
            raise ValueError('Acquisition window out of range')
        window = next(archive.windows(start_window=index, stop_window=index+1))
        archive.unchanged()
        payload = np.asarray(window.samples, dtype='<c8', order='C').tobytes()
        token = uuid4().hex
        self.windows[token] = payload
        self.window_info[token] = (window.metadata(), archive.context())
        while len(self.windows) > 2:
            removed, _ = self.windows.popitem(last=False)
            self.window_info.pop(removed, None)
        return {'url': '/rf/iq/' + token, 'bytes': len(payload), 'metadata': window.metadata(),
                'context': archive.context(), 'windows': archive.manifest['windows']}

    def native(self, command, values):
        from .native_python import native_command
        arguments = [command, str(self.root)]
        if command == 'preview':
            capture = values['capture']
            if capture not in self.captures:
                raise ValueError('Unknown capture')
            arguments += ['--recording', str(self.captures[capture]), '--window', str(values['window'])]
        else:
            directory = self.root / 'exports' / ('skill-' + uuid4().hex[:12])
            arguments += ['--output', str(directory)]
            for name in values.get('keep_plots', []):
                arguments += ['--keep-plot', name]
        # Fresh interpreter resets algorithm helpers/native state for every test.
        command_line = native_command(arguments)
        with self.children_lock:
            if self.stopping:
                raise ValueError('Server is stopping')
            process = subprocess.Popen(command_line, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=(os.name == 'posix'))
            self.children.add(process)
        try:
            output, errors = process.communicate()
        finally:
            with self.children_lock:
                self.children.discard(process)
        if process.returncode:
            raise ValueError(errors[-18000:] or output[-18000:] or 'Native action stopped')
        # GNU Radio's compiler also writes text logs on stdout; final JSON is
        # emitted as a dedicated document starting at the left margin.
        offset = output.rfind('\n{')
        return json.loads(output[offset+1:] if offset >= 0 else output)


    def close(self):
        """Stop only this server's native jobs, including compiler descendants."""
        with self.children_lock:
            self.stopping = True
            children = list(self.children)
        for process in children:
            if process.poll() is None:
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except ProcessLookupError:
                    pass
        for process in children:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, state, **kwargs):
        self.state = state
        super().__init__(*args, **kwargs)

    def end_headers(self):
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        super().end_headers()

    def json(self, value, status=200, *, head=False):
        data = json.dumps(browser_json(value), allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if not head:
            self.wfile.write(data)

    def allowed(self):
        # DNS rebinding/CSRF guard for a deliberately loopback-only service.
        host = self.headers.get('Host', '')
        name = urlsplit('http://' + host).hostname
        return name in ('localhost', '127.0.0.1', '::1')

    def do_HEAD(self):
        self.get(head=True)

    def do_GET(self):
        self.get()

    def get(self, head=False):
        if not self.allowed():
            return self.json({'error': 'Use localhost or an SSH loopback tunnel'}, 403, head=head)
        path = unquote(urlsplit(self.path).path)
        try:
            if path == '/health.json':
                return self.json({'status': 'ready', 'interface': 'gnuradio-world',
                    'instance': os.environ.get('RF_COMPANION_INSTANCE', '')}, head=head)
            if path == '/rf/session':
                spec = json.loads(self.state.local('skill.json').read_text())
                from .companion import read_graph
                return self.json({'graph': read_graph(self.state.local('skill.grc')), 'roles': spec['world'],
                    'captures': [{'id': k, 'label': k} for k in self.state.captures]}, head=head)
            if path == '/rf/files':
                return self.json(self.state.files(), head=head)
            if path.startswith('/rf/iq/'):
                token = path.rsplit('/', 1)[1]
                with self.state.lock:
                    data = self.state.windows.get(token)
                if data is None:
                    return self.json({'error': 'Window expired; select it again'}, 404, head=head)
                start, end = 0, len(data)-1
                requested = self.headers.get('Range')
                if requested:
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
                    if not match or int(match[1]) >= len(data):
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{len(data)}')
                        self.end_headers()
                        return
                    start = int(match[1]); end = min(int(match[2]) if match[2] else end, end)
                    if end < start:
                        return self.json({'error': 'Invalid byte range'}, 416, head=head)
                self.send_response(206 if requested else 200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end-start+1))
                if requested:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{len(data)}')
                self.end_headers()
                if not head:
                    self.wfile.write(memoryview(data)[start:end+1])
                return
            if path.startswith('/rf/'):
                return self.json({'error': 'Unknown RF endpoint'}, 404, head=head)
            # Only compiled application artifacts are in this document root.
            static = (Path(self.directory) / path.lstrip('/')).resolve()
            if not static.is_relative_to(Path(self.directory).resolve()) or any(p.startswith('.') for p in Path(path).parts if p not in ('/',)):
                return self.json({'error': 'Forbidden'}, 403, head=head)
            return super().do_HEAD() if head else super().do_GET()
        except (ValueError, OSError, KeyError, StopIteration) as error:
            return self.json({'error': str(error)}, 400, head=head)

    def do_POST(self):
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if not self.allowed() or (origin and origin != 'http://' + host) or self.headers.get('Content-Type') != 'application/json':
            return self.json({'error': 'Same-origin JSON requests required'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4*1024*1024:
                raise ValueError('Invalid request size')
            values = json.loads(self.rfile.read(length))
            with self.state.lock:
                if self.path == '/rf/save':
                    self.state.save(values['graph']); result = {'saved': 'skill.grc'}
                elif self.path in ('/rf/inspection/prepare', '/rf/inspection/raw'):
                    token = values['input_token']
                    metadata, context = self.state.window_info[token]
                    options = values.get('tap', {})
                    ticket = self.state.inspections.prepare(metadata, context, **options)
                    result = {'id': ticket}
                    if self.path.endswith('/raw'):
                        result = self.state.inspections.save(ticket, self.state.windows[token])
                elif self.path == '/rf/select':
                    result = self.state.select(values['capture'], values['window'], values['graph'])
                elif self.path == '/rf/file':
                    name = values['name']
                    if name not in self.state.files():
                        raise ValueError('Only declared editable project files can be changed')
                    text = values['text']
                    if name.endswith('.json'):
                        json.loads(text)
                    self.state.local(name).write_text(text)
                    result = {'saved': name}
                elif self.path in ('/rf/export', '/rf/native'):
                    result = self.state.native('export' if self.path.endswith('export') else 'preview', values)
                else:
                    return self.json({'error': 'Unknown RF action'}, 404)
            self.json(result)
        except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
            self.json({'error': str(error)}, 400)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--name', default='my-rf-skill')
    parser.add_argument('--transmit', action='store_true')
    parser.add_argument('--bind', default='0.0.0.0', help='Container listener; publish on host loopback')
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--assets', type=Path, default=Path('/opt/gnuradio-world'))
    args = parser.parse_args(argv)
    if not (args.assets / 'runner/build/runner.wasm').is_file():
        parser.error('Built GNU Radio World assets are missing')
    if not (args.workspace / 'skill.json').is_file():
        init_world(args.workspace, args.name, transmit=args.transmit)
    spec = json.loads((args.workspace / 'skill.json').read_text())
    if 'world' not in spec:
        parser.error('Choose a NEW workspace for GNU Radio World; existing Companion projects remain unchanged')
    catalog = json.loads(os.environ.get('RF_HARNESS_CAPTURE_CATALOG', '{}'))
    # Arbitrary host UID/rootless mappings still need writable compiler caches.
    runtime_home = tempfile.TemporaryDirectory(prefix='rf-world-home-')
    os.environ.update(HOME=runtime_home.name, XDG_CACHE_HOME=runtime_home.name + '/.cache',
        XDG_CONFIG_HOME=runtime_home.name + '/.config', XDG_DATA_HOME=runtime_home.name + '/.local/share')
    state = Workspace(args.workspace, catalog)
    from .sigmf_inspector import integrated_handler
    handler, viewer = integrated_handler(state)
    httpd = ThreadingHTTPServer((args.bind, args.port), partial(handler, state=state, directory=str(args.assets)))
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    print(f'GNU Radio World ready: http://127.0.0.1:{httpd.server_port}/', flush=True)
    print('Ctrl+C stops the server; saved skill files remain in ' + str(args.workspace.resolve()), flush=True)
    try:
        httpd.serve_forever()
    finally:
        state.close()
        for executor in (viewer._batch_executor, viewer._export_executor):
            executor.shutdown(wait=False, cancel_futures=True)
        httpd.server_close()
        runtime_home.cleanup()


if __name__ == '__main__':
    main()
