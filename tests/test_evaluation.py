from hashlib import sha256
import json

import numpy as np
import pytest

from rf_skill_harness.demo import write_demo
from rf_skill_harness.evaluation import design_fingerprint, evaluate_project
from rf_skill_harness.project import init_project
from rf_skill_harness.exporter import export_project


def test_same_skill_handles_independent_collects_with_fresh_state_and_unchanged_export(tmp_path):
    pytest.importorskip('gnuradio')
    project = tmp_path / 'project'
    init_project(project)
    (project / 'counter.py').write_text('calls = 0\n')
    spec = json.loads((project / 'skill.json').read_text())
    spec['files'].append('counter.py')
    (project / 'skill.json').write_text(json.dumps(spec))
    (project / 'processor.py').write_text('''import numpy as np
import counter
def process_window(iq, metadata, context):
    counter.calls += 1
    return {'calls': counter.calls, 'rms': float(np.sqrt(np.mean(abs(iq)**2)))}
''')
    runtime = json.loads((project / 'config/runtime.json').read_text())
    runtime['volts_per_count'] = {'rx0': .001}
    (project / 'config/runtime.json').write_text(json.dumps(runtime))
    before = design_fingerprint(project)
    export_project(project, tmp_path / 'export-before')
    first = write_demo(tmp_path / 'collect-a', kind='radar', windows=2)
    second = write_demo(tmp_path / 'collect-b', kind='radar', windows=3)
    # Different actual voltage samples, hashes and collect lengths; same sensor.
    data = second / 'radar_rx0.sigmf-data'
    values = np.frombuffer(data.read_bytes(), '<c8').copy() * 2
    data.write_bytes(values.astype('<c8').tobytes())
    rows = [json.loads(v) for v in (second / 'windows.jsonl').read_text().splitlines()]
    for row in rows:
        a, b = row['sample_start'], row['sample_start'] + row['samples']
        row['channels'][0]['sha256'] = sha256(values[a:b].astype('<c8').tobytes()).hexdigest()
    (second / 'windows.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    result = evaluate_project(project, [first, second], tmp_path / 'evaluation')
    assert result['status'] == 'complete' and result['design_sha256'] == before
    a = [json.loads(v) for v in (tmp_path / 'evaluation/capture-000/results.jsonl').read_text().splitlines()]
    b = [json.loads(v) for v in (tmp_path / 'evaluation/capture-001/results.jsonl').read_text().splitlines()]
    assert [r['measurements']['calls'] for r in a] == [1, 2]
    assert [r['measurements']['calls'] for r in b] == [1, 2, 3]
    assert b[0]['measurements']['rms'] == pytest.approx(2 * a[0]['measurements']['rms'])
    assert design_fingerprint(project) == before
    export_project(project, tmp_path / 'export-after')
    for relative in ('skill.json', 'config/receiver.json', 'config/runtime.json', 'export-manifest.json', 'design/processor.py'):
        assert (tmp_path / 'export-before' / relative).read_bytes() == (tmp_path / 'export-after' / relative).read_bytes()
