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
        if not project.get('recording_dir'):
            raise ValueError('This skill project has no fixed recording; select a capture in the workbench')
        recording = root / project['recording_dir']
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


def available_recordings(directory='.'):
    """A lab session's test inputs; deliberately separate from skill.json."""
    root = Path(directory).resolve()
    result = {}
    path = root / 'recordings.json'
    if path.is_file():
        for label, value in json.loads(path.read_text()).items():
            result[label] = str((root / value).resolve())
    result.update(json.loads(os.environ.get('RF_HARNESS_CAPTURE_CATALOG', '{}')))
    return result


def project_notebook():
    """Skill-first UI, usable before any recording has been supplied."""
    from copy import deepcopy
    template = notebook()
    def cell(kind, source):
        value = {'cell_type': kind, 'id': sha256(source.encode()).hexdigest()[:12],
                 'metadata': {}, 'source': source.splitlines(keepends=True)}
        if kind == 'code':
            value.update(execution_count=None, outputs=[])
        return value
    result = deepcopy(template)
    result['cells'] = [
        cell('markdown', '''# Develop an RF skill → test captures → native AMS-GRA export

This project exists independently of its test captures. Edit `processor.py` beside
this notebook. Select a capture and window, click **Process window**, inspect
standard and custom plots, then revise. Changing captures keeps the same design.
Use notebook cells for exploratory arrays and custom scripts. Native deployment
configuration belongs in `config/`; selecting a test capture never changes it.
'''),
        cell('code', '''%matplotlib inline
from pathlib import Path
import json
import tempfile
import shutil
import matplotlib.pyplot as plt
import ipywidgets as widgets
from IPython.display import display, clear_output
from rf_skill_harness.archive import Archive
from rf_skill_harness.workbench import available_recordings, run_window
from rf_skill_harness.plotting import plot_iq
from rf_skill_harness.results import load_processor, ResultJournal
from rf_skill_harness.evaluation import project_processor

catalog = available_recordings()
capture = widgets.Dropdown(options=list(catalog), description='Capture:')
window_index = widgets.BoundedIntText(value=0, min=0, max=1000000000, description='Window:')
antenna = widgets.Dropdown(options=[], description='Antenna:')
process_button = widgets.Button(description='Process window', button_style='primary')
save_button = widgets.Button(description='Save experiment')
inspect_button = widgets.Button(description='Verify capture')
output = widgets.Output()
current = {}

def select_capture(change=None):
    current.clear()
    process_button.disabled = inspect_button.disabled = save_button.disabled = True
    if capture.value is None:
        with output:
            print('No captures mounted. Restart the launcher with --recordings PATH, or add paths to recordings.json and rerun this cell.')
        return
    try:
        archive = Archive(catalog[capture.value])
        current['archive'] = archive
        process_button.disabled = inspect_button.disabled = False
        window_index.max = archive.manifest['windows'] - 1
        window_index.value = 0
        antenna.options = archive.channels
        antenna.value = archive.channels[0]
        with output:
            clear_output(wait=True)
            print(json.dumps(archive.context(), indent=2))
    except Exception as error:
        with output:
            clear_output(wait=True)
            print(type(error).__name__ + ': ' + str(error))

def process_selected(button=None):
    save_button.disabled = True
    with output:
        clear_output(wait=True)
        try:
            archive = current['archive']
            window, metadata, measurements = run_window(archive, window_index.value, project_processor('.'))
            context = archive.context()
            plt.close('all')
            input_figure = plot_iq(window, context, channel=antenna.value)
            output_figure = load_processor('plot_output.py:plot_output')(window.samples, metadata, context, measurements)
            current.update(window=window, metadata=metadata, measurements=measurements,
                           context=context, input_figure=input_figure, output_figure=output_figure)
            save_button.disabled = False
            print(json.dumps({'metadata': metadata, 'measurements': measurements}, indent=2)[:8000])
            display(input_figure, output_figure)
        except Exception:
            current.pop('measurements', None)
            import traceback
            traceback.print_exc()

def save_experiment(button=None):
    with output:
        if 'measurements' not in current:
            print('Process a window successfully before saving.')
            return
        Path('experiments').mkdir(exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='run-', dir='experiments'))
        journal = ResultJournal(directory, current['context'], processor='processor.py:process_window')
        status = 'failed'
        try:
            journal.write(current['metadata'], current['context']['channels'], current['measurements'])
            status = 'complete'
        finally:
            journal.close(status, execution_backend='direct-python-window')
        for name in ('processor.py', 'report.py', 'plot_output.py', 'skill.json'):
            shutil.copyfile(name, directory / name)
        for name in ('input_figure', 'output_figure'):
            current[name].savefig(directory / (name + '.png'), dpi=150)
        print('Saved:', directory.resolve())

def verify_capture(button=None):
    with output:
        if 'archive' in current:
            print(current['archive'].inspect(verify=True)['sample_hashes_verified'])

capture.observe(select_capture, names='value')
process_button.on_click(process_selected)
save_button.on_click(save_experiment)
inspect_button.on_click(verify_capture)
display(widgets.VBox([capture, window_index, antenna,
    widgets.HBox([process_button, inspect_button, save_button]), output]))
select_capture()
'''),
        cell('markdown', '''## Your scratch cells

After processing, `current` contains `window`, `metadata`, `context`, and
`measurements`. Add cells to examine intermediates or write custom plots.
Each Process click reloads file-based hooks and starts fresh processor state.
Use sequential batch replay with one callable instance for stateful warm-up.

## Export the skill

Edit `skill.json` for identity, helper files and dependencies; edit `report.py`
for your explicit result-to-UCI Signal mapping. Configure `config/receiver.json`
and `config/runtime.json` for the deployed MEL stream IDs and calibrated voltage
scale. These are independent of whichever test capture is selected.

Export does not bundle recordings, notebooks or this workbench. This first native
profile schedules RX only; active radar TX needs a transmit-capable profile.
Generated code must still pass provider integration and UCI schema validation.
'''),
        cell('code', '''from rf_skill_harness.exporter import export_project
export_path = widgets.Text(value='exports/my-rf-skill', description='Output:')
export_button = widgets.Button(description='Export native skill', button_style='success')
export_output = widgets.Output()
def export_native(button=None):
    with export_output:
        clear_output(wait=True)
        try:
            print(json.dumps(export_project('.', export_path.value), indent=2))
        except Exception as error:
            print(type(error).__name__ + ': ' + str(error))
export_button.on_click(export_native)
display(widgets.VBox([export_path, export_button, export_output]))
''')]
    return result


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
