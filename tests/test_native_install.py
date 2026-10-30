import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from rf_skill_harness.native_python import BOOTSTRAP, find_native_python
from rf_skill_harness.world_local import assets_path, assets_ready, doctor


def test_native_interpreter_does_not_import_server_site_packages(tmp_path):
    # A sentinel NumPy next to our installed package must not shadow the native
    # interpreter's own dependencies when loading only rf_skill_harness.
    site = tmp_path / 'server-site'
    package = site / 'rf_skill_harness'; package.mkdir(parents=True)
    (site / 'json.py').write_text("raise RuntimeError('wrong site-packages leaked')")
    (package / '__init__.py').write_text('')
    (package / 'world.py').write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n')
    result = subprocess.run([sys.executable, '-s', '-c', BOOTSTRAP, str(package), 'export', 'skill'],
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == ['export', 'skill']


def test_doctor_allows_browser_without_gnuradio(tmp_path):
    for name in ('index.html', 'blocks.json', 'runner/build/runner.wasm', 'upstream-lock.json'):
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture')
    with patch('rf_skill_harness.native_python.shutil.which', return_value=None):
        result = doctor(tmp_path)
    assert result['browser_ready'] and not result['native']['available']
    assert 'grcc' in result['native']['detail']
    assert assets_path(tmp_path) == tmp_path


def test_missing_native_tools_report_actionable_error():
    with patch('rf_skill_harness.native_python.shutil.which', return_value=None):
        with pytest.raises(ValueError, match='sudo apt-get install gnuradio'):
            find_native_python()


def test_explicit_native_interpreter_is_not_silently_replaced():
    with patch('rf_skill_harness.native_python.shutil.which', return_value='/usr/bin/grcc'), \
         patch('rf_skill_harness.native_python.probe_native', return_value=(None, 'no bindings')) as probe:
        with pytest.raises(ValueError, match='no bindings'):
            find_native_python('/deliberate/python')
    probe.assert_called_once_with('/deliberate/python')


def test_shutdown_stops_owned_native_processing(tmp_path):
    import threading
    import time
    from rf_skill_harness.world_server import Workspace
    state = Workspace(tmp_path, {'capture': tmp_path})
    errors = []
    def run():
        try:
            state.native('preview', {'capture': 'capture', 'window': 0})
        except ValueError as error:
            errors.append(str(error))
    with patch('rf_skill_harness.native_python.native_command',
               return_value=[sys.executable, '-c', 'import time; time.sleep(60)']):
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with state.children_lock:
                children = list(state.children)
            if children:
                break
            time.sleep(.01)
        assert children
        state.close()
        thread.join(timeout=5)
    assert not thread.is_alive() and errors
    assert all(child.poll() is not None for child in children)
