import json
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
import shutil
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest

from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo
from rf_skill_harness.world import init_world, native_graph, export_world, preview_world
from rf_skill_harness.world_server import Workspace, Handler, browser_json


def test_browser_capture_binding_ranges_metadata_and_export_barrier(tmp_path):
    root = tmp_path / 'skill'
    init_world(root, transmit=True)
    first = write_demo(tmp_path / 'capture-a', kind='radar', windows=2)
    second = write_demo(tmp_path / 'capture-b', kind='radar', windows=3)
    original = (root / 'skill.grc').read_bytes()
    graph = json.loads(original)
    state = Workspace(root, {'a': first, 'b': second})
    static = tmp_path / 'web'; static.mkdir(); (static / 'index.html').write_text('browser')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, state=state, directory=str(static)))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    def post(path, data):
        return json.load(urlopen(Request(url + path, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})))
    try:
        for capture, index in [('a', 1), ('b', 2)]:
            selected = post('/rf/select', {'capture': capture, 'window': index, 'graph': graph})
            archive = Archive(state.captures[capture])
            expected = next(archive.windows(start_window=index, stop_window=index+1))
            assert selected['metadata']['timestamp_ns'] == str(expected.timestamp_ns)
            response = urlopen(Request(url + selected['url'], headers={'Range': 'bytes=5-103'}))
            assert response.status == 206
            assert response.headers['Content-Range'] == f'bytes 5-103/{expected.samples.nbytes}'
            assert response.headers['Cross-Origin-Embedder-Policy'] == 'require-corp'
            assert response.read() == expected.samples.astype('<c8').tobytes()[5:104]
        assert (root / 'skill.grc').read_bytes() == original
        with pytest.raises(HTTPError) as denied:
            urlopen(Request(url + '/rf/save', data=b'{}', headers={'Content-Type': 'application/json', 'Origin': 'http://evil.test'}))
        assert denied.value.code == 403
        with pytest.raises(HTTPError):
            post('/rf/file', {'name': '../outside.py', 'text': 'no'})
        post('/rf/save', {'graph': graph})
        assert str(first) not in (root / 'skill.grc').read_text()
    finally:
        server.shutdown(); server.server_close(); thread.join()
    graph['blocks'].append({'id': 'wasm_test', 'name': 'unsupported', 'parameters': {}})
    with pytest.raises(ValueError, match='Browser-only'):
        native_graph(graph, json.loads((root / 'skill.json').read_text())['world'])


def test_browser_json_preserves_nanoseconds():
    assert browser_json({'stamp': 123456789000000007, 'n': 40, 'yes': True}) == {'stamp': '123456789000000007', 'n': 40, 'yes': True}


@pytest.mark.skipif(not shutil.which('grcc'), reason='Native GNU Radio compiler required')
def test_world_edited_gain_metadata_and_capture_free_native_tx_rx_export(tmp_path):
    root = tmp_path / 'design'; init_world(root, transmit=True)
    path = root / 'skill.grc'
    graph = json.loads(path.read_text())
    dsp = next(b for b in graph['blocks'] if b['name'] == 'custom_dsp')
    dsp['parameters']['gain'] = '2.0'
    dsp['parameters']['_source_code'] = dsp['parameters']['_source_code'].replace(
        'output_items[0][:] =', "assert isinstance(RF_WINDOW['timestamp_ns'], int)\n        assert RF_CONTEXT['units'] == 'volts'\n        output_items[0][:] =")
    path.write_text(json.dumps(graph))
    original = path.read_bytes()
    for i in (1, 2):
        capture = write_demo(tmp_path / f'capture-{i}', kind='radar', windows=i+1)
        result = preview_world(root, capture, index=i)
        data = next(Archive(capture).windows(start_window=i, stop_window=i+1)).samples
        assert result['measurements']['rms_volts']['radar_rx0'] == pytest.approx(np.sqrt(np.mean(abs(data*2)**2)), rel=1e-5)
        assert result['tx_samples'] == 256
        assert result['metadata']['timestamp_ns'] == 123456789000000007+i*4000000
    assert path.read_bytes() == original
    config = root / 'config/runtime.json'; value = json.loads(config.read_text())
    value['volts_per_count'] = {'rx0': .001}; value['transmit']['tx_power_w'] = 1
    config.write_text(json.dumps(value))
    target = tmp_path / 'export'
    export_world(root, target)
    assert (target / 'export-manifest.json').is_file()
    assert not list(target.rglob('*.sigmf-data'))
    for file in target.rglob('*'):
        if file.is_file():
            assert 'capture-1' not in file.read_text()
    source = (target / 'source/skill.grc').read_text()
    assert 'RF_RECEIVE' not in source and 'blocks_file_source' not in source
    assert 'rfskill_tx' in source and 'rfskill_rx' in source
    assert 'qtgui' not in (target / 'design/skill_flowgraph.py').read_text()
