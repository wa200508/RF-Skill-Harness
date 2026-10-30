"""Run native GNU Radio with its own interpreter and ABI-matched NumPy.

The browser server may be installed by pip/uv under a different Python version.
Load only this package from its installed location into the selected interpreter;
never add the server's entire site-packages (and NumPy) to its import path.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

BOOTSTRAP = '''import importlib.util, pathlib, runpy, sys
root = pathlib.Path(sys.argv.pop(1))
spec = importlib.util.spec_from_file_location('rf_skill_harness', root / '__init__.py', submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules['rf_skill_harness'] = module
spec.loader.exec_module(module)
runpy.run_module('rf_skill_harness.world', run_name='__main__')
'''
PROBE = '''import json, numpy, yaml
from gnuradio import gr, blocks
blocks.null_sink(gr.sizeof_gr_complex)
print(json.dumps({'python': __import__('sys').executable, 'gnuradio': gr.version(), 'numpy': numpy.__version__}))
'''


def probe_native(python):
    try:
        result = subprocess.run([str(python), '-s', '-c', PROBE], text=True,
                                capture_output=True, timeout=20)
        if result.returncode:
            return None, result.stderr.strip()[-1800:]
        return json.loads(result.stdout.strip().splitlines()[-1]), None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired) as error:
        return None, str(error)


def find_native_python(explicit=None):
    if not shutil.which('grcc'):
        raise ValueError('Native testing/export needs GNU Radio and grcc. On Ubuntu 24.04: '
            'sudo apt-get install gnuradio python3-numpy python3-yaml. '
            'Browser editing/replay/plots work without this installation.')
    requested = explicit or os.environ.get('RF_WORLD_NATIVE_PYTHON')
    candidates = [requested] if requested else list(dict.fromkeys([sys.executable, '/usr/bin/python3']))
    errors = []
    for candidate in candidates:
        info, error = probe_native(candidate)
        if info:
            return info
        errors.append(f'{candidate}: {error}')
    raise ValueError('No usable native GNU Radio Python. Use --native-python PATH '
        '(or RF_WORLD_NATIVE_PYTHON) for the interpreter containing GNU Radio, NumPy and PyYAML.\n'
        + '\n'.join(errors))


def native_command(arguments, python=None):
    info = find_native_python(python)
    return [info['python'], '-s', '-c', BOOTSTRAP, str(Path(__file__).parent), *arguments]
