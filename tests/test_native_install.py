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
