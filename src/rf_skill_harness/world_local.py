"""Installed, container-free GNU Radio World launcher."""
import argparse
import json
import os
from pathlib import Path
import sys

from . import __version__
from .catalog import discover_recordings


def assets_path(override=None):
    return Path(override or os.environ.get('RF_WORLD_ASSETS') or
                Path(__file__).with_name('world_assets')).expanduser().resolve()


def assets_ready(path):
    return all((path / name).is_file() for name in
               ('index.html', 'blocks.json', 'runner/build/runner.wasm', 'upstream-lock.json'))


def doctor(assets=None, native_python=None):
    from .native_python import find_native_python
    path = assets_path(assets)
    try:
        native = {'available': True, **find_native_python(native_python)}
    except ValueError as error:
        native = {'available': False, 'detail': str(error)}
    return {'version': __version__, 'python': sys.executable, 'assets': str(path),
            'browser_ready': assets_ready(path), 'native': native}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=__version__)
    subs = parser.add_subparsers(dest='command', required=True)
    inspect = subs.add_parser('doctor', help='Check packaged browser assets and optional native GNU Radio')
    serve = subs.add_parser('serve', help='Serve the installed browser workbench on localhost')
    for sub in (inspect, serve):
        sub.add_argument('--assets', type=Path, help='Developer override for a built GNU Radio World directory')
        sub.add_argument('--native-python', help='Optional GNU Radio interpreter, e.g. /usr/bin/python3')
    serve.add_argument('--workspace', type=Path, required=True)
    serve.add_argument('--recordings', type=Path, action='append', default=[])
    serve.add_argument('--name', default='my-rf-skill')
    serve.add_argument('--transmit', action='store_true')
    serve.add_argument('--port', type=int, default=8090)
    args = parser.parse_args(argv)
    if args.command == 'doctor':
        result = doctor(args.assets, args.native_python)
        print(json.dumps(result, indent=2))
        return 0 if result['browser_ready'] else 1
    assets = assets_path(args.assets)
    if not assets_ready(assets):
        parser.error('Browser assets are missing. Install the wheel from the GitHub release. '
            'A source checkout needs scripts/build_world.py and --assets BUILT_DIRECTORY; '
            'see docs/install.md.')
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535')
    workspace = args.workspace.expanduser().resolve()
    if workspace.exists() and any(workspace.iterdir()) and not (workspace / 'skill.grc').is_file():
        parser.error('--workspace must be new/empty or an existing GNU Radio World skill')
    try:
        captures = discover_recordings(args.recordings)
    except ValueError as error:
        parser.error(str(error))
    os.environ['RF_HARNESS_CAPTURE_CATALOG'] = json.dumps({k: str(v) for k, v in captures.items()})
    if args.native_python:
        os.environ['RF_WORLD_NATIVE_PYTHON'] = args.native_python
    arguments = ['--workspace', str(workspace), '--assets', str(assets), '--name', args.name,
                 '--bind', '127.0.0.1', '--port', str(args.port)]
    if args.transmit:
        arguments.append('--transmit')
    from .world_server import main as serve_main
    try:
        serve_main(arguments)
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        parser.error(str(error))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
