import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from rf_skill_harness.project import init_project
from rf_skill_harness.exporter import export_project


def configured(tmp_path, channels=('rx0',)):
    project = tmp_path / 'project'
    init_project(project, 'test-rf-skill')
    receiver = json.loads((project / 'config/receiver.json').read_text())
    receiver['sites'][0]['channels'] = list(channels)
    (project / 'config/receiver.json').write_text(json.dumps(receiver))
    config = json.loads((project / 'config/runtime.json').read_text())
    config.update(window_samples=4, receive_event_samples=4,
        stream_channels={str(i + 1): n for i, n in enumerate(channels)},
        volts_per_count={n: .001 for n in channels})
    (project / 'config/runtime.json').write_text(json.dumps(config))
    return project


def load_engine(bundle):
    spec = importlib.util.spec_from_file_location('exported_engine', bundle / 'runtime/engine.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = json.loads((bundle / 'config/runtime.json').read_text())
    config.update(skill_root=str(bundle), receiver_config=str(bundle / 'config/receiver.json'), results_dir=str(bundle / 'results'))
    return module.Engine(json.dumps(config))


def metadata(stream=1, count=4, time=123_456_789_000_000_007):
    return json.dumps({'stream_ids': [stream], 'seconds': time // 10**9,
        'fractional_fs': time % 10**9 * 10**6,
        'events': [{'duration_fs': count * 500_000_000, 'start_fs': 0,
                    'sample_rate_hz': 2_000_000, 'carrier_hz': 915_000_000}]})


def test_project_exists_without_any_recording_and_executes_its_notebook(tmp_path):
    project = tmp_path / 'project'
    init_project(project)
    assert 'recording_dir' not in json.loads((project / 'project.json').read_text())
    assert json.loads((project / 'recordings.json').read_text()) == {}
    nbformat = pytest.importorskip('nbformat')
    nbclient = pytest.importorskip('nbclient')
    notebook = nbformat.read(project / 'analysis.ipynb', as_version=4)
    nbformat.validate(notebook)
    nbclient.NotebookClient(notebook, timeout=60, kernel_name='python3').execute(cwd=str(project))
    assert not (project / 'exports').exists()


def test_one_skill_project_switches_captures_without_changing_the_design(tmp_path, monkeypatch):
    nbformat = pytest.importorskip('nbformat')
    nbclient = pytest.importorskip('nbclient')
    from rf_skill_harness.demo import write_demo
    project = tmp_path / 'project'
    init_project(project)
    captures = {kind: str(write_demo(tmp_path / kind, kind=kind))
                for kind in ('direction-finding', 'radar')}
    monkeypatch.setenv('RF_HARNESS_CAPTURE_CATALOG', json.dumps(captures))
    original = (project / 'skill.json').read_bytes()
    notebook = nbformat.read(project / 'analysis.ipynb', as_version=4)
    notebook.cells.append(nbformat.v4.new_code_cell('''
process_selected()
assert len(current['context']['channels']) == 8
assert current['window'].samples.shape[1] == 8
capture.value = 'radar'
process_selected()
assert len(current['context']['channels']) == 1
assert current['window'].samples.shape == (40000, 1)
save_experiment()
assert list(Path('experiments').glob('run-*/results.jsonl'))
catalog['unavailable'] = '/no-such-capture'
capture.options = list(catalog)
capture.value = 'unavailable'
assert current == {}
assert process_button.disabled and save_button.disabled
'''))
    nbclient.NotebookClient(notebook, timeout=60, kernel_name='python3').execute(cwd=str(project))
    assert (project / 'skill.json').read_bytes() == original
    assert json.loads((project / 'config/receiver.json').read_text())['carrier_hz'] == 915_000_000


def test_native_export_is_standalone_and_does_not_copy_test_data(tmp_path):
    project = configured(tmp_path)
    (project / 'secret.sigmf-data').write_bytes(b'iq-not-for-release')
    (project / 'analysis.ipynb').write_text('not-a-design-file')
    bundle = tmp_path / 'bundle'
    export_project(project, bundle)
    assert (bundle / 'design/processor.py').read_bytes() == (project / 'processor.py').read_bytes()
    assert not list(bundle.rglob('*.sigmf-data')) and not list(bundle.rglob('*.ipynb'))
    assert not (bundle / 'recordings.json').exists()
    assert json.loads((bundle / 'export-manifest.json').read_text())['validation']['certified'] is False
    # A fresh process with only the exported source path can import the adapter.
    code = "import sys; sys.path.insert(0, sys.argv[1]); import engine; assert 'rf_skill_harness' not in sys.modules"
    subprocess.run([sys.executable, '-I', '-c', code, str(bundle / 'runtime')], check=True)
    # The exported source distribution carries runnable contract tests itself.
    subprocess.run([sys.executable, '-m', 'pytest', str(bundle / 'tests'), '-q',
                    '-p', 'no:cacheprovider'], check=True, timeout=30)


def test_native_phase_reset_cannot_be_hidden_inside_a_processing_window(tmp_path):
    bundle = tmp_path / 'bundle'
    export_project(configured(tmp_path), bundle)
    engine = load_engine(bundle)
    first = json.loads(metadata(count=2))
    first.update(job_interval_id=1, phase_coherence_with_prior=False)
    second = json.loads(metadata(count=2, time=123_456_789_000_001_007))
    second.update(job_interval_id=2, phase_coherence_with_prior=False)
    engine.consume(np.zeros((2,2), '<i2').tobytes(), json.dumps(first))
    with pytest.raises(ValueError, match='phase discontinuity'):
        engine.consume(np.zeros((2,2), '<i2').tobytes(), json.dumps(second))


@pytest.mark.parametrize('reason', ['calibration', 'path', 'simulator', 'capture', 'tx'])
def test_export_rejects_undefined_or_nonportable_designs_before_creating_output(tmp_path, reason):
    project = configured(tmp_path)
    spec = json.loads((project / 'skill.json').read_text())
    if reason == 'calibration':
        config = json.loads((project / 'config/runtime.json').read_text())
        config['volts_per_count']['rx0'] = None
        (project / 'config/runtime.json').write_text(json.dumps(config))
    elif reason == 'path':
        spec['files'].append('../outside.py')
    elif reason == 'simulator':
        (project / 'processor.py').write_text('import airsim_rf\n')
    elif reason == 'capture':
        (project / 'raw.sigmf-data').write_bytes(b'123')
        spec['files'].append('raw.sigmf-data')
    else:
        spec['capabilities'] = ['receive', 'transmit']
    (project / 'skill.json').write_text(json.dumps(spec))
    with pytest.raises(ValueError):
        export_project(project, tmp_path / 'bundle')
    assert not (tmp_path / 'bundle').exists()


def test_native_callbacks_reassemble_coherent_windows_and_preserve_time_and_scaling(tmp_path):
    project = configured(tmp_path, ('antenna0', 'antenna1'))
    (project / 'processor.py').write_text('''def process_window(iq, metadata, context):
    assert not iq.flags.writeable
    return {'real': iq.real.tolist(), 'imag': iq.imag.tolist(), 'channels': context['channels']}
''')
    (project / 'report.py').write_text("def signal_reports(measurements, metadata, context): return [{'Parametrics': {'Frequency': {'FrequencyAverage': context['carrier_hz']}}}]\n")
    bundle = tmp_path / 'bundle'
    export_project(project, bundle)
    engine = load_engine(bundle)
    a = np.array([[1, 2], [3, 4]], dtype='<i2').tobytes()
    b = np.array([[5, 6], [7, 8]], dtype='<i2').tobytes()
    assert json.loads(engine.consume(a, metadata(1, 2))) == []
    assert json.loads(engine.consume(b, metadata(1, 2, 123_456_789_000_001_007))) == []
    values = np.array([[10, -10], [20, -20], [30, -30], [40, -40]], dtype='<i2').tobytes()
    reports = json.loads(engine.consume(values, metadata(2)))
    assert reports[0]['timestamp_ns'] == 123_456_789_000_000_007
    result = json.loads((bundle / 'results/000000000000.json').read_text())
    assert result['measurements']['channels'] == ['antenna0', 'antenna1']
    np.testing.assert_allclose(np.array(result['measurements']['real'])[:, 0], [.001, .003, .005, .007], rtol=1e-6)
    assert result['metadata']['clipped_component_fraction'] == {'antenna0': None, 'antenna1': None}
    assert result['metadata']['sample_start'] == 0


@pytest.mark.parametrize('bad', ['ambiguous', 'gap', 'duration', 'carrier', 'epoch'])
def test_native_adapter_rejects_ambiguous_or_discontinuous_acquisition(tmp_path, bad):
    project = configured(tmp_path)
    bundle = tmp_path / 'bundle'
    export_project(project, bundle)
    engine = load_engine(bundle)
    values = np.ones((4, 2), '<i2').tobytes()
    native = json.loads(metadata())
    if bad == 'ambiguous': native['stream_ids'] = [1, 2]
    elif bad == 'gap':
        engine.consume(values, metadata())
        native = json.loads(metadata(time=123_456_789_000_003_007))
    elif bad == 'duration': native['events'][0]['duration_fs'] += 1
    elif bad == 'carrier': native['events'][0]['carrier_hz'] = 1
    else: native['events'] = []
    with pytest.raises(ValueError):
        engine.consume(values, json.dumps(native))
