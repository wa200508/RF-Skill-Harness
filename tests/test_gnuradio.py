import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import numpy as np
import pytest

from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo
from rf_skill_harness.grc import prepare_project

pytest.importorskip("gnuradio.gr")
from gnuradio import blocks, gr
import pmt
from rf_skill_harness.gr_blocks import ArchiveSource
from rf_skill_harness.runner import run


@pytest.mark.parametrize("kind", ["direction-finding", "radar"])
@pytest.mark.parametrize("chunk", [257, 8192])
def test_native_vector_source_preserves_every_sample_and_exact_acquisition_tags(tmp_path, kind, chunk):
    root = write_demo(tmp_path / "iq", kind=kind)
    archive = Archive(root)
    source = ArchiveSource(str(root), len(archive.channels), start_window=1, stop_window=4)
    sink = blocks.vector_sink_c(len(archive.channels))
    flow = gr.top_block()
    flow.connect(source, sink)
    flow.run(max_noutput_items=chunk)
    expected = list(archive.windows(start_window=1, stop_window=4))
    actual = np.asarray(sink.data(), np.complex64).reshape(-1, len(archive.channels))
    assert actual.tobytes() == np.concatenate([w.samples for w in expected]).tobytes()
    tags = sink.tags()
    times = [t for t in tags if pmt.symbol_to_string(t.key) == "rf_timestamp_ns"]
    assert [pmt.to_uint64(t.value) for t in times] == [w.timestamp_ns for w in expected]
    assert [t.offset for t in times] == [i * len(expected[0].samples) for i in range(3)]
    config = next(t for t in tags if pmt.symbol_to_string(t.key) == "rf_context")
    assert json.loads(pmt.symbol_to_string(config.value)) == archive.context()
    boundaries = [json.loads(pmt.symbol_to_string(t.value)) for t in tags if pmt.symbol_to_string(t.key) == "rf_window"]
    assert [b["sample_start"] for b in boundaries] == [w.sample_start for w in expected]
    rates = [pmt.to_double(t.value) for t in tags if pmt.symbol_to_string(t.key) == "rx_rate"]
    assert rates == [archive.rate] * 3


@pytest.mark.parametrize("kind", ["direction-finding", "radar"])
@pytest.mark.parametrize("speed", [0., 10.])
def test_real_flow_runs_insert_hook_once_per_window_with_original_metadata(tmp_path, kind, speed):
    root = write_demo(tmp_path / "iq", kind=kind)
    plugin = tmp_path / "my processor.py"
    plugin.write_text('''from hashlib import sha256
def process_window(iq, metadata, context):
 assert not iq.flags.writeable
 return {'hashes':[sha256(iq[:,i].tobytes()).hexdigest() for i in range(iq.shape[1])],
         'voltage_reference':context['voltage_reference'],
         'radar_acquisition':context['receiver_config'].get('acquisition')}
''')
    output = tmp_path / "results"
    result = run(root, output, processor=str(plugin), start_window=1, stop_window=4, speed=speed)
    assert result["status"] == "complete" and result["windows"] == 3
    rows = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    original = [json.loads(line) for line in (root / "windows.jsonl").read_text().splitlines()][1:4]
    for row, recorded in zip(rows, original):
        assert row["timestamp_ns"] == recorded["timestamp_ns"]
        assert row["sample_start"] == recorded["sample_start"]
        assert row["measurements"]["hashes"] == [c["sha256"] for c in recorded["channels"]]
    context = json.loads((output / "context.json").read_text())
    assert context["receiver_config"] == Archive(root).config
    assert json.loads((output / "run.json").read_text())["status"] == "complete"


def test_bad_hook_fails_promptly_instead_of_hanging_the_flowgraph(tmp_path):
    root = write_demo(tmp_path / "iq")
    plugin = tmp_path / "bad.py"
    plugin.write_text("def process_window(iq, metadata, context):\n raise RuntimeError('intentional DSP failure')\n")
    result = subprocess.run([sys.executable, "-m", "rf_skill_harness", "run", str(root),
        "--processor", str(plugin), "--output", str(tmp_path / "results")], text=True, capture_output=True, timeout=15)
    assert result.returncode != 0 and "intentional DSP failure" in result.stderr
    assert json.loads((tmp_path / "results/run.json").read_text())["status"] == "failed"


def test_corrupt_iq_reports_failure_and_never_processes_the_bad_window(tmp_path):
    root = write_demo(tmp_path / "iq")
    archive = Archive(root)
    n = archive.manifest["samples_per_channel"] // archive.manifest["windows"]
    path = root / (archive.channels[0] + ".sigmf-data")
    with path.open("r+b") as stream:
        stream.seek(n * 8 + 8)
        stream.write(b"corrupt!")
    with pytest.raises(RuntimeError, match="SHA-256"):
        run(root, tmp_path / "results")
    summary = json.loads((tmp_path / "results/run.json").read_text())
    assert summary["status"] == "failed" and summary["windows"] <= 1


def compile_graph(project, filename):
    generated = project / "generated"
    generated.mkdir(exist_ok=True)
    result = subprocess.run(["grcc", "-o", str(generated), str(project / filename)],
                            cwd=project, text=True, capture_output=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return generated


@pytest.mark.parametrize("kind", ["direction-finding", "radar"])
def test_companion_graph_compiles_and_runs_the_same_developer_hook(tmp_path, kind):
    root = write_demo(tmp_path / "relocated archive" / "iq", kind=kind)
    project = tmp_path / "editable project"
    prepare_project(root, project)
    generated = compile_graph(project, "harness.grc")
    result = subprocess.run([sys.executable, str(generated / "rf_skill_replay.py"),
                            "--playback-speed", "100", "--output-dir", str(tmp_path / "results")],
                            cwd=project, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads((tmp_path / "results/run.json").read_text())
    assert summary["status"] == "complete" and summary["windows"] == 6


@pytest.mark.parametrize("kind", ["direction-finding", "radar"])
def test_companion_qt_viewer_opens_with_all_antenna_channels(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    root = write_demo(tmp_path / "iq", kind=kind)
    project = tmp_path / "project"
    prepare_project(root, project)
    generated = compile_graph(project, "viewer.grc")
    from PyQt5 import Qt
    application = Qt.QApplication.instance() or Qt.QApplication([])
    monkeypatch.syspath_prepend(str(generated))
    spec = importlib.util.spec_from_file_location("generated_viewer", generated / "rf_skill_viewer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    flow = module.rf_skill_viewer(recording_dir=str(root), processor=str(project / "processor.py"), playback_speed=100)
    assert flow._view_channel_combo_box.count() == (1 if kind == "radar" else 8)
    assert flow.display_center_hz == (0 if kind == "radar" else 915e6)
    flow.show()
    application.processEvents()
    try:
        flow.run()
        assert flow.run_state.windows == 6 and flow.run_state.error is None
    finally:
        flow.close()
        application.processEvents()
