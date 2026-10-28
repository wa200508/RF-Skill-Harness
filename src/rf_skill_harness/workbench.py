"""Finite-window experiments using the production hook, without a stream scheduler."""
import json
from hashlib import sha256
import os
from pathlib import Path

from .archive import Archive
from .results import load_processor


def open_project(directory="."):
    """Resolve a prepared workspace on the host or inside the lab container."""
    root = Path(directory).expanduser().resolve()
    project = json.loads((root / "project.json").read_text())
    recording = os.environ.get("RF_HARNESS_RECORDING")
    if not recording:
        recording = root / project["recording_dir"]
    return Archive(recording, allow_partial=(project.get("allow_partial", False)
        or os.environ.get("RF_HARNESS_ALLOW_PARTIAL") == "1"))


def run_window(archive, index=0, processor=None):
    """Reload a file-based hook and process exactly one original coherent window.

    Each call starts fresh processor state. For sequential/stateful experiments,
    load_processor once and iterate archive.windows() in acquisition order, or
    use the GNU Radio CLI. No plotting data is inserted into the skill contract.
    """
    iterator = archive.windows(start_window=index, stop_window=index + 1)
    try:
        window = next(iterator)
    finally:
        iterator.close()
    archive.unchanged()
    metadata = window.metadata()
    measurements = load_processor(processor)(window.samples, dict(metadata), archive.context())
    if not isinstance(measurements, dict):
        raise ValueError("process_window must return a JSON-compatible dict")
    json.dumps(measurements, allow_nan=False)
    return window, metadata, measurements


def notebook():
    """Generate an ordinary, editable notebook with no mandatory notebook imports."""
    def cell(kind, source):
        value = {"cell_type": kind, "id": sha256((kind + source).encode()).hexdigest()[:12],
                 "metadata": {}, "source": source.splitlines(keepends=True)}
        if kind == "code":
            value.update(execution_count=None, outputs=[])
        return value

    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"display_name": "Python 3 (ipykernel)", "language": "python", "name": "python3"},
        "language_info": {"name": "python"}}, "cells": [
        cell("markdown", """# RF skill: edit → run one window → plot → revise

The input is the original coherent receiver I/Q and acquisition metadata.
Edit `processor.py` in JupyterLab's text editor, save it, then rerun the processing
cell. It loads fresh source on each call. Edit `plot_output.py` for custom figures.
Run All also works on an unchanged workspace. The schedule is in `timeline.html`.
"""),
        cell("code", """%matplotlib inline
# For pan/zoom widgets instead, change the line above to %matplotlib widget.
from pathlib import Path
import json
import matplotlib.pyplot as plt
from rf_skill_harness.workbench import open_project, run_window
from rf_skill_harness.results import load_processor
from rf_skill_harness.plotting import plot_iq, plot_results

archive = open_project()
context = archive.context()
print('Windows:', archive.manifest['windows'])
print('Channels:', context['channels'])
print('Sample rate (Hz):', context['sample_rate_hz'])
print('Acquisition:', context['receiver_config'].get('acquisition', 'continuous receive'))
"""),
        cell("markdown", """## Select one block and run your processing

`window_index` is zero-based. A block here means one recorded acquisition window
with all antenna channels, not an arbitrary GNU Radio scheduler buffer. Selecting
a display channel below never removes antennas from the processor's input.
Each run starts fresh hook state. An isolated window may lack algorithm warm-up.
"""),
        cell("code", """window_index = 0
display_channel = context['channels'][0]

window, metadata, measurements = run_window(
    archive, window_index, 'processor.py:process_window')
print('Original acquisition metadata:', json.dumps(metadata, indent=2))
print('Processor outputs:', json.dumps(measurements, indent=2)[:4000])
"""),
        cell("markdown", """## Standard input plots

These show received voltage, FFT magnitude, and a within-window spectrogram.
They are diagnostics of the stored I/Q, not a radar range–Doppler map. The latter
requires your chirp/gate-aware processing. No samples are changed for processing;
only plotted time/FFT/spectrogram extents are bounded to keep the UI responsive.
"""),
        cell("code", """plt.close('all')
input_figure = plot_iq(window, context, channel=display_channel)
plt.show()
"""),
        cell("markdown", """## Your output visualization

Edit `plot_output.py` to display a range–Doppler image, MUSIC search, demodulated
symbols, or any other output. It receives the same input, metadata, context and
measurements. Processing remains in `processor.py`; plotting is a separate hook.
You can also write plotting code directly in this cell. Add extra cells to inspect
intermediate arrays while prototyping, then put reusable DSP in the processor.
"""),
        cell("code", """output_figure = load_processor('plot_output.py:plot_output')(
    window.samples, metadata, context, measurements)
plt.show()
"""),
        cell("markdown", """## Save this experiment (optional)

Run this cell when you want artifacts. It creates a new directory every time;
the notebook otherwise leaves experiment outputs in memory. The recording is
read-only. Copy any custom scripts or configuration alongside results for provenance.
"""),
        cell("code", """import tempfile
from rf_skill_harness.results import ResultJournal

Path('experiments').mkdir(exist_ok=True)
experiment = Path(tempfile.mkdtemp(prefix='window-', dir='experiments'))
journal = ResultJournal(experiment, context, processor='processor.py:process_window')
status = 'failed'
try:
    journal.write(metadata, context['channels'], measurements)
    status = 'complete'
finally:
    journal.close(status, start_window=window_index, stop_window=window_index + 1,
                  execution_backend='direct-python-window')
import shutil
for name in ('processor.py', 'plot_output.py', 'project.json'):
    shutil.copyfile(name, experiment / name)
input_figure.savefig(experiment / 'input.png', dpi=150)
output_figure.savefig(experiment / 'output.png', dpi=150)
print('Saved:', experiment.resolve())
"""),
        cell("markdown", """## Inspect a batch run (optional)

Set a path to a completed CLI run and use `plot_results(path)` to plot scalar
measurements against original acquisition time. For arrays, maps, or custom data,
write your own script or extend `plot_output.py`. `plot_results.py` is an editable
command-line starter. Restart the kernel after changing imported helper modules.
For stateful DSP, process preceding windows in order with one callable instance;
the CLI does this automatically. Direct Python tests the hook, while the GNU Radio
CLI additionally tests stream tags, coherent reassembly, and backpressure.
"""),
    ]}
