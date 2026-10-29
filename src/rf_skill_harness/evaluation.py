"""Evaluate one unchanged skill against independent, replaceable test captures."""
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from .catalog import discover_recordings
from .paths import new_directory


def design_fingerprint(workspace):
    root = Path(workspace).resolve()
    spec = json.loads((root / 'skill.json').read_text())
    names = {'skill.json', *spec['files']}
    names.update(str(p.relative_to(root)) for p in (root / 'config').rglob('*') if p.is_file())
    result = {}
    for name in sorted(names):
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError('Design files must be inside the skill project: ' + name)
        result[name] = sha256(path.read_bytes()).hexdigest()
    return result


def project_processor(workspace):
    root = Path(workspace).resolve()
    spec = json.loads((root / 'skill.json').read_text())
    filename, separator, function = spec['processor'].partition(':')
    path = (root / filename).resolve()
    if (not separator or not function.isidentifier() or filename not in spec['files']
            or not path.is_relative_to(root) or not path.is_file()):
        raise ValueError('The processor must be a declared project Python file:function')
    return str(path) + ':' + function


def evaluate_project(workspace, recordings, output, *, start_window=0, stop_window=None):
    project = Path(workspace).resolve()
    fingerprint = design_fingerprint(project)
    processor = project_processor(project)
    captures = discover_recordings(recordings)
    if not captures:
        raise ValueError('No receiver captures found')
    root = new_directory(output)
    summary = {'schema_version': 1, 'design_sha256': fingerprint, 'captures': [], 'status': 'running'}
    try:
        for index, (label, path) in enumerate(captures.items()):
            row = {'label': label, 'recording': str(path), 'results': f'capture-{index:03d}'}
            summary['captures'].append(row)
            # Process isolation also resets state in imported DSP helpers and
            # native GNU Radio modules, not just the top-level Python callable.
            arguments = [sys.executable, '-m', 'rf_skill_harness', 'run', str(path),
                '--processor', processor, '--output', str(root / row['results']),
                '--start-window', str(start_window)]
            if stop_window is not None:
                arguments += ['--stop-window', str(stop_window)]
            row['log'] = f'capture-{index:03d}.log'
            with (root / row['log']).open('w') as log:
                completed = subprocess.run(arguments, stdout=log, stderr=subprocess.STDOUT)
            status = root / row['results'] / 'run.json'
            if status.is_file():
                row.update(json.loads(status.read_text()))
            if completed.returncode or row.get('status') != 'complete':
                raise RuntimeError(f"Collect {label} failed; see {root / row['log']}")
            if design_fingerprint(project) != fingerprint:
                raise RuntimeError('The skill design changed during evaluation')
        summary['status'] = 'complete'
    except BaseException as error:
        summary.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        (root / 'evaluation.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    return summary
