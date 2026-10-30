#!/usr/bin/env python3
"""Install a release wheel in a fresh venv and qualify it without Docker."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import venv

from qualify_world import qualify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-python', default='/usr/bin/python3')
    parser.add_argument('--chromium')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rf-native-install-') as temporary:
        root = Path(temporary)
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / 'bin/python'
        subprocess.run([str(python), '-m', 'pip', 'install', str(args.wheel.resolve())], check=True)
        command = [str(python), '-m', 'rf_skill_harness.world_local']
        # Keep the checkout and developer PYTHONPATH out of the installed test.
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        report = json.loads(subprocess.check_output([*command, 'doctor', '--native-python', args.native_python],
            cwd=root, env=env, text=True))
        assert report['browser_ready'] and report['native']['available'], report
        assert Path(report['assets']).is_relative_to(environment)
        (args.output / 'installed-doctor.json').write_text(json.dumps(report, indent=2))
        for label in ('a', 'b'):
            subprocess.run([str(python), '-m', 'rf_skill_harness', 'demo', '--kind', 'radar',
                '--output', str(root / 'captures' / label)], cwd=root, env=env, check=True)
        with socket.socket() as available:
            available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
        url = f'http://127.0.0.1:{port}/'
        with (args.output / 'server.log').open('w') as log:
            process = subprocess.Popen([*command, 'serve', '--workspace', str(root / 'skill'),
                '--recordings', str(root / 'captures'), '--port', str(port), '--transmit',
                '--native-python', args.native_python], cwd=root, env=env,
                stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 60
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Installed server stopped; inspect server.log')
                    try:
                        with urlopen(url + 'health.json', timeout=1) as response:
                            if json.load(response)['status'] == 'ready': break
                    except (OSError, ValueError): pass
                    if time.monotonic() > deadline:
                        raise RuntimeError('Installed server did not start')
                    time.sleep(.2)
                asyncio.run(qualify(url, args.output, args.chromium))
                exports = list((root / 'skill/exports').iterdir())
                assert len(exports) == 1 and not list(exports[0].rglob('*.sigmf-data'))
            finally:
                process.send_signal(signal.SIGTERM)
                process.wait(timeout=20)
            assert process.returncode == 0
        # Installed wheel still works after that project/server has closed.
        assert (root / 'skill/skill.grc').is_file()
        assert Path(report['assets']).is_dir()
    print('Installed release: browser DSP, native test, independent export and cleanup passed without Docker.')


if __name__ == '__main__':
    main()
