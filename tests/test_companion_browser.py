import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import pytest


@pytest.mark.parametrize('rootless', [False, True])
@pytest.mark.parametrize('standalone', [False, True])
def test_browser_launcher_uses_selected_python_loopback_readonly_captures_and_cleans_up(tmp_path, rootless, standalone):
    root = Path(__file__).resolve().parents[1]
    tools = tmp_path / 'bin'; tools.mkdir()
    log = tmp_path / 'docker.jsonl'
    pid = tmp_path / 'run.pid'
    docker = tools / 'docker'
    docker.write_text('''#!/usr/bin/env python3
import json,os,signal,sys,time
from pathlib import Path
from http.server import BaseHTTPRequestHandler,HTTPServer
''' + f"log=Path({str(log)!r}); pid=Path({str(pid)!r})\n" + '''
with log.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:3]==['image','inspect']: print('a'*40)
if sys.argv[1]=='info':
 print(''' + repr(json.dumps(['name=rootless'] if rootless else [])) + ''')
if sys.argv[1]=='run':
 Path(sys.argv[sys.argv.index('--cidfile')+1]).write_text('own-browser-container')
 pid.write_text(str(os.getpid()))
 port=int(sys.argv[sys.argv.index('--publish')+1].split(':')[1])
 instance=next(v.split('=',1)[1] for v in sys.argv if v.startswith('RF_COMPANION_INSTANCE='))
 class Handler(BaseHTTPRequestHandler):
  def log_message(self,*args): pass
  def do_GET(self):
   self.send_response(200); self.end_headers()
   self.wfile.write(json.dumps({'status':'ready','instance':instance,'graphics':{'renderer':'llvmpipe fixture'}}).encode())
 HTTPServer(('127.0.0.1',port),Handler).serve_forever()
if sys.argv[1]=='stop' and pid.exists():
 try: os.kill(int(pid.read_text()),signal.SIGTERM)
 except ProcessLookupError: pass
''')
    docker.chmod(0o755)
    selected = tools / 'chosen-python'; marker = tmp_path / 'python-used'
    selected.write_text(f"#!/bin/sh\nprintf selected > '{marker}'\nexec '{sys.executable}' \"$@\"\n")
    selected.chmod(0o755)
    script = root / 'scripts/start_companion.sh'
    if standalone:
        script_copy = tools / 'start_companion.sh'
        script_copy.write_bytes(script.read_bytes()); script = script_copy
        curl = tools / 'curl'
        curl.write_text('#!/usr/bin/env python3\nimport sys,shutil\nfrom pathlib import Path\n'
            f'root=Path({str(root)!r})\n'
            "url=sys.argv[2]\nassert '/'+('a'*40)+'/' in url\n"
            "name=url.split('/'+('a'*40)+'/',1)[1]\n"
            "shutil.copyfile(root/name,sys.argv[sys.argv.index('-o')+1])\n")
        curl.chmod(0o755)
    from rf_skill_harness.demo import write_demo
    capture = write_demo(tmp_path / 'mission' / 'iq', kind='radar', windows=1)
    (capture.parent / 'truth.json').write_text('{"not_for_skill":true}')
    workspace = tmp_path / 'skill with spaces'
    with socket.socket() as available:
        available.bind(('127.0.0.1', 0)); port = available.getsockname()[1]
    env = {**os.environ, 'PATH': str(tools) + os.pathsep + os.environ['PATH'],
           'RF_HARNESS_PYTHON': str(selected), 'XDG_CACHE_HOME': str(tmp_path / 'cache')}
    process = subprocess.Popen(['bash', str(script),
        '--workspace', str(workspace), '--recordings', str(capture.parent),
        '--port', str(port), '--transmit'], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            if pid.exists():
                time.sleep(.4); break
            if process.poll() is not None: break
            time.sleep(.02)
        process.send_signal(signal.SIGTERM)
        output, errors = process.communicate(timeout=10)
    finally:
        if process.poll() is None:
            process.kill(); process.wait()
    assert process.returncode == 130, errors
    assert marker.read_text() == 'selected'
    assert f'http://127.0.0.1:{port}/vnc.html' in output
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    run = next(c for c in calls if c[0] == 'run')
    assert run[run.index('--user')+1] == ('0:0' if rootless else f'{os.getuid()}:{os.getgid()}')
    assert run[run.index('--publish')+1] == f'127.0.0.1:{port}:6080'
    assert '--privileged' not in run and '--gpus' not in run and '--device' not in run
    assert not any('/tmp/.X11-unix' in value for value in run)
    assert f'type=bind,src={workspace},dst=/workspace' in run
    assert any(v.startswith(f'type=bind,src={capture},dst=/recordings/') and v.endswith(',readonly') for v in run)
    assert not any(v.startswith(f'type=bind,src={capture.parent},') for v in run)
    assert '--transmit' in run
    assert ['stop', '--time', '5', 'own-browser-container'] in calls
    assert ['rm', '--force', 'own-browser-container'] in calls
