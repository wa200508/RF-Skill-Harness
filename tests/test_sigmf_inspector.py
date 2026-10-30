import json
from pathlib import Path
import numpy as np
import pytest

from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo
from rf_skill_harness.sigmf_inspector import InspectionStore, create_viewer
from rf_skill_harness.world import init_world
from rf_skill_harness.world_server import Workspace


def test_exact_coherent_channels_metadata_and_no_source_mutation(tmp_path):
    pytest.importorskip('sigmf_viewer')
    from sigmf_viewer import open_recording, read_window
    capture = write_demo(tmp_path / 'collect', kind='direction-finding', windows=1)
    archive = Archive(capture); window = next(archive.windows())
    source = {p.name: p.read_bytes() for p in capture.glob('*') if p.is_file()}
    root = tmp_path / 'skill'; root.mkdir()
    store = InspectionStore(root)
    token = store.prepare(window.metadata(), archive.context(), channels=8)
    result = store.save(token, window.samples.astype('<c8').tobytes())
    recording = open_recording(result['metadata'])
    inspected = read_window(recording, start_sample=0, sample_count=len(window.samples))
    np.testing.assert_array_equal(inspected.samples.T, window.samples)
    assert recording.channel_labels == tuple(archive.channels)
    global_meta = recording.metadata['global']
    assert global_meta['rf_harness:parent_timestamp_ns'] == str(window.timestamp_ns)
    assert global_meta['rf_harness:units'] == 'volts'
    assert source == {p.name: p.read_bytes() for p in capture.glob('*') if p.is_file()}


def test_real_stream_rate_and_invalid_uploads(tmp_path):
    capture = write_demo(tmp_path / 'collect', kind='radar', windows=1)
    archive = Archive(capture); window = next(archive.windows())
    store = InspectionStore(tmp_path)
    token = store.prepare(window.metadata(), archive.context(), block='filter', sample_rate=1e6, datatype='rf32_le')
    with pytest.raises(ValueError, match='complete samples'):
        store.save(token, b'bad')
    result = store.save(token, np.arange(64, dtype='<f4').tobytes())
    meta = json.loads(Path(result['metadata']).read_text())
    assert meta['global']['core:sample_rate'] == 1e6
    assert meta['global']['rf_harness:parent_sample_rate_hz'] == 10e6
    assert result['samples'] == 64
    for rate in (float('nan'), -1, True):
        with pytest.raises(ValueError): store.prepare(window.metadata(), archive.context(), sample_rate=rate)


def test_actual_sigmf_workspace_and_original_annotation_boundary(tmp_path):
    pytest.importorskip('sigmf_viewer')
    capture = write_demo(tmp_path / 'collect', kind='radar', windows=1)
    root = tmp_path / 'skill'; init_world(root)
    state = Workspace(root, {'receiver': capture})
    app, handler = create_viewer(state)
    assert app.registry.get('capture-0').annotator is None
    assert app.registry.get('inspections').annotator is not None
    assert app.registry.get('capture-0').view.__module__ == 'sigmf_viewer.view'
    assert app.list_workspaces()[0]['id'] == 'inspections'
    app._batch_executor.shutdown(); app._export_executor.shutdown()


def test_new_inspections_refresh_upstream_catalog(tmp_path):
    pytest.importorskip('sigmf_viewer')
    capture = write_demo(tmp_path / 'capture', kind='radar', windows=1)
    root = tmp_path / 'skill'; init_world(root)
    state = Workspace(root, {})
    app, _ = create_viewer(state)
    catalog = app.registry.get('inspections')
    assert catalog.discover_items() == []
    archive = Archive(capture); window = next(archive.windows())
    for count in (1, 2):
        token = state.inspections.prepare(window.metadata(), archive.context())
        state.inspections.save(token, window.samples.astype('<c8').tobytes())
        assert len(catalog.discover_items()) == count
    app._batch_executor.shutdown(); app._export_executor.shutdown()
