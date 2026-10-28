import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

import numpy as np
import pytest

from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo
from rf_skill_harness.grc import prepare_project
from rf_skill_harness.results import load_processor
from rf_skill_harness.workbench import open_project, run_window


@pytest.mark.parametrize("kind", ["direction-finding", "radar"])
def test_direct_processing_preserves_selected_window_bytes_and_all_receiver_metadata(tmp_path, kind):
    archive = Archive(write_demo(tmp_path / "iq", kind=kind))
    hook = tmp_path / "processor.py"
    hook.write_text("""from hashlib import sha256
def process_window(iq, metadata, context):
    assert not iq.flags.writeable
    return {'sha256': sha256(iq.tobytes()).hexdigest(), 'metadata': metadata, 'context': context}
""")
    window, metadata, result = run_window(archive, 2, str(hook))
    expected = next(archive.windows(start_window=2, stop_window=3))
    assert window.samples.tobytes() == expected.samples.tobytes()
    assert metadata == expected.metadata() == result["metadata"]
    assert result["context"] == archive.context()
    assert window.samples.shape[1] == len(archive.channels)


def test_fast_same_size_processor_edits_never_use_stale_bytecode(tmp_path):
    archive = Archive(write_demo(tmp_path / "iq"))
    hook = tmp_path / "processor.py"
    hook.write_text("def process_window(iq, metadata, context): return {'version': 1}\n")
    py_compile.compile(str(hook), doraise=True)
    timestamp = hook.stat().st_mtime_ns
    assert run_window(archive, 0, str(hook))[2] == {"version": 1}
    hook.write_text(hook.read_text().replace("1}", "2}"))
    os.utime(hook, ns=(timestamp, timestamp))
    assert run_window(archive, 0, str(hook))[2] == {"version": 2}


def test_corruption_is_rejected_before_loading_or_executing_developer_code(tmp_path):
    archive = Archive(write_demo(tmp_path / "iq"))
    payload = archive.path / (archive.channels[0] + ".sigmf-data")
    with payload.open("r+b") as stream:
        stream.write(b"broken")
    marker = tmp_path / "processor-loaded"
    hook = tmp_path / "processor.py"
    hook.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(ValueError, match="SHA-256"):
        run_window(archive, 0, str(hook))
    assert not marker.exists()


@pytest.mark.parametrize("body", ["return []", "return {'bad': float('nan')}", "raise RuntimeError('DSP failed')"])
def test_notebook_processing_has_the_same_failure_contract_as_batch(tmp_path, body):
    archive = Archive(write_demo(tmp_path / "iq"))
    hook = tmp_path / "processor.py"
    hook.write_text(f"def process_window(iq, metadata, context): {body}\n")
    with pytest.raises((ValueError, RuntimeError)):
        run_window(archive, 0, str(hook))


def test_project_paths_work_on_host_and_with_container_override(tmp_path, monkeypatch):
    capture = write_demo(tmp_path / "capture with spaces" / "iq")
    project = tmp_path / "workspace with spaces"
    prepare_project(capture, project)
    assert open_project(project).path == capture
    assert not Path(json.loads((project / "project.json").read_text())["recording_dir"]).is_absolute()
    moved = write_demo(tmp_path / "mounted-iq", kind="radar")
    monkeypatch.setenv("RF_HARNESS_RECORDING", str(moved))
    assert open_project(project).path == moved


@pytest.mark.parametrize("kind", ["direction-finding", "radar"])
def test_prepared_notebook_executes_all_cells_and_saves_figures_without_gnuradio(tmp_path, kind):
    nbformat = pytest.importorskip("nbformat")
    client = pytest.importorskip("nbclient")
    capture = write_demo(tmp_path / "iq", kind=kind)
    project = tmp_path / "workspace"
    prepare_project(capture, project)
    notebook = nbformat.read(project / "analysis.ipynb", as_version=4)
    nbformat.validate(notebook)
    client.NotebookClient(notebook, timeout=90, kernel_name="python3").execute(cwd=str(project))
    experiment, = (project / "experiments").iterdir()
    result = json.loads((experiment / "results.jsonl").read_text())
    assert result["timestamp_ns"] == Archive(capture).first_timestamp_ns
    assert result["samples"] == next(Archive(capture).windows()).samples.shape[0]
    assert json.loads((experiment / "run.json").read_text())["execution_backend"] == "direct-python-window"
    for name in ("input.png", "output.png", "processor.py", "plot_output.py"):
        assert (experiment / name).stat().st_size > 0


def test_scalar_result_plot_preserves_acquisition_times_and_never_replays_iq(tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from rf_skill_harness.plotting import plot_results
    results = tmp_path / "results"
    results.mkdir()
    epoch = 123_456_789_000_000_007
    (results / "results.jsonl").write_text("\n".join(json.dumps({
        "timestamp_ns": epoch + index * 4_000_000, "measurements": {
            "rms_volts": {"antenna0": .01 + index * .01}, "large_map": [1, 2, 3]}})
        for index in range(3)))
    figure = plot_results(results)
    np.testing.assert_array_equal(figure.axes[0].lines[0].get_xdata(), [0, .004, .008])
    plt.close(figure)
    output = tmp_path / "plots"
    status = subprocess.run([sys.executable, "-m", "rf_skill_harness", "plot", str(results),
        "--output", str(output)], capture_output=True, text=True, timeout=30)
    assert status.returncode == 0, status.stderr
    assert (output / "results.png").stat().st_size > 1000


@pytest.mark.parametrize("kind", ["direction-finding", "radar"])
def test_notebook_and_gnuradio_process_exactly_the_same_selected_window(tmp_path, kind):
    pytest.importorskip("gnuradio")
    from rf_skill_harness.runner import run
    capture = write_demo(tmp_path / "iq", kind=kind)
    hook = tmp_path / "processor.py"
    hook.write_text("""from hashlib import sha256
def process_window(iq, metadata, context):
    return {'sha256': sha256(iq.tobytes()).hexdigest(), 'context': context}
""")
    _, metadata, measurements = run_window(Archive(capture), 2, str(hook))
    output = tmp_path / "batch"
    run(capture, output, processor=str(hook), start_window=2, stop_window=3)
    row = json.loads((output / "results.jsonl").read_text())
    assert {key: row[key] for key in metadata} == metadata
    assert row["measurements"] == measurements
