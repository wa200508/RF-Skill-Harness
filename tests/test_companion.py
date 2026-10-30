import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

pytest.importorskip('gnuradio')
from rf_skill_harness.archive import Archive
from rf_skill_harness.companion import (init_companion, preview, read_graph,
    deployment_graph, export_companion, evaluate_companion, visualization_blocks)
from rf_skill_harness.demo import write_demo


def configure(root):
    p = root / 'config/runtime.json'
    config = json.loads(p.read_text())
    config['volts_per_count'] = {'rx0': .001}
    if 'transmit' in config:
        config['transmit']['tx_power_w'] = 1
    p.write_text(json.dumps(config))


def engine(bundle):
    for name in list(sys.modules):
        if name in ('skill_io', 'skill_flowgraph') or name.startswith('skill_flowgraph_'):
            sys.modules.pop(name, None)
    description = importlib.util.spec_from_file_location('native_engine', bundle / 'runtime/engine.py')
    module = importlib.util.module_from_spec(description)
    description.loader.exec_module(module)
    config = json.loads((bundle / 'config/runtime.json').read_text())
    config.update(skill_root=str(bundle), receiver_config=str(bundle / 'config/receiver.json'), results_dir=None)
    return module.Engine(json.dumps(config))


def test_one_edited_flowgraph_runs_two_sensor_collects_without_dataset_paths(tmp_path):
    root = tmp_path / 'design'
    init_companion(root, transmit=True)
    path = root / 'skill.grc'
    graph = read_graph(path)
    next(b for b in graph['blocks'] if b['name'] == 'custom_dsp')['parameters']['gain'] = '2.0'
    path.write_text(json.dumps(graph))
    original = path.read_bytes()
    for index in (1, 2):
        capture = write_demo(tmp_path / f'collect-{index}', kind='radar', windows=index)
        result = preview(root, capture, index=index - 1, plots=False)
        window = next(Archive(capture).windows(start_window=index-1, stop_window=index))
        expected = np.sqrt(np.mean(abs(window.samples * 2)**2, axis=0))
        np.testing.assert_allclose(list(result['measurements']['rms_volts'].values()), expected, rtol=1e-6)
        assert result['tx_samples'] == 256
    assert path.read_bytes() == original
    assert str(tmp_path) not in path.read_text()


def test_export_prunes_plot_branches_and_keeps_actual_custom_dsp_and_native_tx(tmp_path):
    root = tmp_path / 'design'
    init_companion(root, transmit=True)
    configure(root)
    graph = read_graph(root / 'skill.grc')
    code = next(b for b in graph['blocks'] if b['name'] == 'custom_dsp')['parameters']
    # An actual custom code edit, rather than just a top-level hook export.
    code['_source_code'] = code['_source_code'].replace('* self.gain', '* self.gain * 3')
    (root / 'skill.grc').write_text(json.dumps(graph))
    (root / 'companion-session.json').write_text('{"recording":"/private/test-capture"}')
    bundle = tmp_path / 'bundle'
    export_companion(root, bundle)
    compiled = (bundle / 'design/skill_flowgraph.py').read_text()
    assert 'qtgui' not in compiled and 'PyQt5' not in compiled
    assert 'blocks.selector' not in compiled
    assert '* self.gain * 3' in (bundle / 'design/skill_flowgraph_custom_dsp.py').read_text()
    assert not (bundle / 'companion-session.json').exists()
    assert all('/private/test-capture' not in p.read_text() for p in bundle.rglob('*') if p.is_file())
    instance = engine(bundle)
    x = np.full((4096, 1), .01 + .02j, np.complex64)
    metadata = {'samples': len(x), 'timestamp_ns': 123456789000000007, 'sample_start': 0, 'index': 0}
    result = instance.process(x, metadata, instance.context)
    assert result['rms_volts']['rx0'] == pytest.approx(abs(.03+.06j), rel=1e-5)
    raw = np.frombuffer(instance.transmit_iq(), '<i2').reshape(-1, 2)
    t = np.arange(256) / 2e6
    expected = np.rint(np.column_stack((.25*np.cos(2*np.pi*100e3*t), .25*np.sin(2*np.pi*100e3*t))) * 32767)
    np.testing.assert_allclose(raw, expected, atol=1)
    assert instance.config['transmit']['duration_fs'] < instance.config['transmit']['rx_start_fs']
    subprocess.run([sys.executable, '-m', 'pytest', str(bundle / 'tests'), '-q', '-p', 'no:cacheprovider'],
                   check=True, timeout=30)


@pytest.mark.parametrize('channels', [1, 8])
def test_keep_one_visualization_and_export_without_any_input_capture(tmp_path, monkeypatch, channels):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    root = tmp_path / 'design'
    init_companion(root)
    configure(root)
    if channels == 8:
        graph = read_graph(root / 'skill.grc')
        next(b for b in graph['blocks'] if b['name'] == 'channel_count')['parameters']['value'] = '8'
        code = next(b for b in graph['blocks'] if b['name'] == 'custom_dsp')['parameters']
        code['_source_code'] = code['_source_code'].replace('channels=1', 'channels=8')
        (root / 'skill.grc').write_text(json.dumps(graph))
        path = root / 'config/receiver.json'
        receiver = json.loads(path.read_text())
        receiver['sites'][0]['channels'] = [f'rx{i}' for i in range(channels)]
        path.write_text(json.dumps(receiver))
        path = root / 'config/runtime.json'
        config = json.loads(path.read_text())
        config.update(stream_channels={str(i+1): f'rx{i}' for i in range(channels)},
            volts_per_count={f'rx{i}': .001 for i in range(channels)})
        path.write_text(json.dumps(config))
    bundle = tmp_path / 'bundle'
    result = export_companion(root, bundle, keep_visualizations=['rx_spectrum'])
    assert result['retained_visualizations'] == ['rx_spectrum']
    code = (bundle / 'design/skill_flowgraph.py').read_text()
    assert 'qtgui.freq_sink_c' in code and 'qtgui.time_sink_c' not in code
    instance = engine(bundle)
    data = np.ones((4096, channels), np.complex64) * .01
    result = instance.process(data, {'timestamp_ns': 10, 'samples': len(data)}, instance.context)
    assert result['rms_volts']['rx0'] == pytest.approx(.01)
    app.processEvents()


def test_companion_evaluation_keeps_skill_unchanged_and_uses_both_collects(tmp_path):
    root = tmp_path / 'design'
    init_companion(root)
    captures = [write_demo(tmp_path / 'first', kind='radar', windows=1),
                write_demo(tmp_path / 'second', kind='radar', windows=2)]
    graph = (root / 'skill.grc').read_bytes()
    result = evaluate_companion(root, captures, tmp_path / 'results')
    assert result['status'] == 'complete'
    assert [c['windows'] for c in result['captures']] == [1, 2]
    assert (root / 'skill.grc').read_bytes() == graph
    assert not (root / 'skill_flowgraph.py').exists()


def test_tools_panel_selects_collects_and_individual_export_plots(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    from PyQt5 import QtWidgets, QtCore
    from rf_skill_harness.companion_gui import SkillPanel
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    root = tmp_path / 'design'
    init_companion(root, transmit=True)
    captures = {kind: str(write_demo(tmp_path / kind, kind=kind)) for kind in ('radar', 'direction-finding')}
    (root / 'recordings.json').write_text(json.dumps(captures))
    panel = SkillPanel(root, start_editor=False)
    panel.show(); app.processEvents()
    assert panel.capture.count() == 2 and panel.plots.count() == 3
    assert panel.selected_plots() == []
    panel.plots.item(0).setCheckState(QtCore.Qt.Checked)
    assert panel.selected_plots() == ['rx_spectrum']
    panel.refresh_plots()
    assert panel.selected_plots() == ['rx_spectrum']
    panel.capture.setCurrentIndex(1)
    assert '8 coherent channels' in panel.capture_info.text()
    assert json.loads((root / 'companion-session.json').read_text())['recording'] == captures['direction-finding']
    panel.close()


def test_export_refuses_replacing_rf_input_with_a_dataset_file_source(tmp_path):
    root = tmp_path / 'design'
    init_companion(root)
    graph = read_graph(root / 'skill.grc')
    next(b for b in graph['blocks'] if b['name'] == 'receive')['id'] = 'blocks_file_source'
    with pytest.raises(ValueError, match='RF Receive Endpoint'):
        deployment_graph(graph)
