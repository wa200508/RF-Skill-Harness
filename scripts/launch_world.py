#!/usr/bin/env python3
"""Stdlib-only host launcher for the GNU Radio World browser workbench."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rf_skill_harness.catalog import discover_recordings

DEFAULT_IMAGE = 'ghcr.io/wa200508/airsim-rf:rf-skill-gnuradio-world'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--recordings', type=Path, action='append', default=[])
    parser.add_argument('--name', default='my-rf-skill')
    parser.add_argument('--transmit', action='store_true', help='Include TX in a NEW project')
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--image', default=os.environ.get('RF_WORLD_IMAGE', DEFAULT_IMAGE))
    parser.add_argument('--no-pull', action='store_true')
    parser.add_argument('--build', action='store_true', help='Build this checkout instead of pulling the public image')
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('--port must be between 1024 and 65535, including rootless Docker')
    workspace = args.workspace.expanduser().resolve()
    if workspace.exists() and any(workspace.iterdir()) and not (workspace / 'skill.grc').is_file():
        parser.error('--workspace must be new/empty or an existing GNU Radio World project')
    captures = discover_recordings(args.recordings)
    if args.build:
        source = Path(__file__).resolve().parents[1]
        if not (source / 'Dockerfile.world').is_file():
            parser.error('--build requires a source checkout')
        build = ['docker', 'build', '-f', str(source / 'Dockerfile.world'), '-t', args.image]
        certificate = os.environ.get('CODEX_PROXY_CERT')
        if certificate and Path(certificate).is_file():
            build += ['--secret', 'id=proxy_ca,src=' + certificate]
        subprocess.run([*build, str(source)], check=True)
    elif not args.no_pull:
        subprocess.run(['docker', 'pull', args.image], check=True)
    subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image], check=True)
    info = subprocess.check_output(['docker', 'info', '--format', '{{json .SecurityOptions}}'], text=True)
    uid = '0:0' if 'rootless' in info.lower() else f'{os.getuid()}:{os.getgid()}'
    workspace.mkdir(parents=True, exist_ok=True)
    instance = str(uuid4())
    options = ['docker', 'run', '--rm', '--init', '--user', uid, '--shm-size', '256m',
        '--label', 'org.rf-skill-harness.companion.instance=' + instance,
        '--publish', f'127.0.0.1:{args.port}:8090',
        '--mount', f'type=bind,src={workspace},dst=/workspace', '--workdir', '/workspace']
    catalog = {}
    for label, path in captures.items():
        mount = '/recordings/' + sha256(str(path).encode()).hexdigest()[:16]
        options += ['--mount', f'type=bind,src={path},dst={mount},readonly']
        catalog[label] = mount
    options += ['-e', 'RF_HARNESS_CAPTURE_CATALOG=' + json.dumps(catalog),
                '-e', 'RF_COMPANION_INSTANCE=' + instance]
    arguments = ['--workspace', '/workspace', '--name', args.name]
    if args.transmit:
        arguments.append('--transmit')
    url = f'http://127.0.0.1:{args.port}/'
    print('Starting GNU Radio World HTTP service…', flush=True)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        with tempfile.TemporaryDirectory(prefix='rf-companion-launch-') as temporary:
            cidfile = Path(temporary) / 'container.id'
            process = None
            try:
                process = subprocess.Popen(options + ['--cidfile', str(cidfile), args.image] + arguments)
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f'GNU Radio World exited during startup ({process.returncode}); see server output above')
                    try:
                        with urlopen(f'http://127.0.0.1:{args.port}/health.json', timeout=1) as response:
                            health = json.load(response)
                        if health.get('status') == 'ready' and health.get('instance') == instance:
                            break
                    except (OSError, ValueError):
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError('GNU Radio World did not become ready within 90 seconds; stopping this launch')
                    time.sleep(.2)
                print('GNU Radio World ready: browser editor and WebAssembly DSP', flush=True)
                print('Open ' + url + '\nCtrl+C stops the service; project files remain on the host.', flush=True)
                code = process.wait()
                if code:
                    raise RuntimeError(f'GNU Radio World server exited ({code})')
            finally:
                if cidfile.is_file() and cidfile.read_text().strip():
                    container = cidfile.read_text().strip()
                    subprocess.run(['docker', 'stop', '--time', '5', container],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run(['docker', 'rm', '--force', container],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if process is not None:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.terminate(); process.wait(timeout=5)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
