# Build an RF skill from the starter

`prepare` creates `analysis.ipynb`, `processor.py`, `plot_output.py`,
`plot_results.py`, a receiver-only `context.json`, `project.json` with the
recording path, and editable `harness.grc` / `viewer.grc`.
The default code computes voltage statistics so you can verify the plumbing
before implementing an algorithm.

## Processing hook

```python
import numpy as np

def process_window(iq, metadata, context):
    fs = context['sample_rate_hz']
    channels = context['channels']
    # INSERT SIGNAL PROCESSING HERE. iq[:, i] is antenna channel i.
    return {'samples_seen': len(iq), 'duration_s': len(iq) / fs}
```

Use `--processor /path/processor.py:process_window` or, with the native CLI,
`--processor my_package.module:callable`. A processor may be a stateful callable
object exported from the module; it persists for the run. Sibling helper modules
are importable from a file-based hook. The container launcher mounts that
developer directory read-only. Install additional dependencies in a derived
container or native environment; the offline runner never installs them mid-run.

Inputs are the recorded complex volts, acquisition-window metadata and complete
receiver context. Return a JSON-compatible dict with finite scalar/list values.
NumPy scalars/arrays should be converted explicitly (`float`, `.tolist()`);
represent complex results explicitly as real/imaginary pairs. Invalid output or
an exception fails the run and marks `run.json` failed. Results already journaled
remain available. Use a separate output directory for every experiment.

Changing replay speed changes pacing only. Stateful algorithms may need earlier
windows for warm-up when selecting a subset. Input samples are read-only; make
an explicit copy if your algorithm needs in-place operations.

## Notebook and custom plotting

The browser workflow is described in the [README](../README.md). For native
Python, the optional lab dependencies are installed with:

```bash
python -m pip install -e '.[lab]'
python -m jupyterlab --notebook-dir=workspace/my-skill
```

Use an activated Python 3.10+ virtual environment. Direct notebook processing
needs no GNU Radio. If you also want native GNU Radio replay, create the
system-site-packages virtual environment described below instead. The container
lab already includes both runtimes, with no host GUI toolkit requirement.

The generated notebook calls this public API, which a custom script can use too:

```python
from rf_skill_harness.archive import Archive
from rf_skill_harness.workbench import run_window
from rf_skill_harness.plotting import plot_iq

archive = Archive('/path/to/iq')
window, metadata, result = run_window(
    archive, 0, '/path/to/my-skill/processor.py:process_window')
figure = plot_iq(window, archive.context(), channel=archive.channels[0])
figure.savefig('input.png')
```

`run_window` verifies the selected samples and provides byte-identical coherent
I/Q, original metadata and receiver context to the same hook used by GNU Radio.
Each call reloads file-based source and creates fresh processor state. Imported
helper modules follow Python's normal cache rules: restart the notebook kernel
or reload those modules explicitly when editing them. Exceptions are shown in
the notebook; the hook must return the same finite JSON-compatible dictionary
required by batch replay.

For stateful experiments, load the callable once and visit preceding windows in
order, including any required warm-up; do not repeatedly call `run_window`:

```python
from rf_skill_harness.results import load_processor

process = load_processor('/path/to/my-skill/processor.py:process_window')
for window in archive.windows(start_window=0, stop_window=3):
    result = process(window.samples, window.metadata(), archive.context())
    # INSERT CUSTOM PLOTTING OR RESULT STORAGE HERE.
```

`plot_output.py:plot_output(iq, metadata, context, measurements)` is a separate
visualization hook returning a Matplotlib Figure. It initially plots RMS voltage;
replace it for your algorithm. The notebook prints other output schemas until
you supply their visualization. All DSP remains in `processor.py`, independent
of Jupyter or plotting dependencies. To expose arrays through the existing hook
contract, use `.tolist()`; represent complex arrays as real/imaginary pairs.
Large intermediate tensors are usually easier to inspect directly in notebook
cells while prototyping than to serialize in every production result.

The standard I/Q figure displays the first 4,096 time samples, up to 65,536 FFT
samples and up to 131,072 spectrogram samples of the selected channel. It labels
these extents, voltage units and baseband/beat-frequency offsets explicitly.
Processing still receives the complete original window and every channel. These
plots are not calibrated dBm, dBFS, PSD or automatically formed radar maps.

For custom offline analysis of batch outputs, edit the prepared script or write
your own. In a native environment with Matplotlib installed:

```bash
python workspace/my-skill/plot_results.py workspace/my-skill/experiments/batch-1 \
  --output batch-1.png
```

The standard script and `rf-harness plot` read only results.jsonl, plot up to 24
scalar dictionary leaves with separate axes, and subtract integer acquisition
timestamps before converting relative time to seconds. They skip lists/maps
requiring domain-specific interpretation. Plotting saved results never reruns DSP.

The lab mounts only receiver I/Q/metadata read-only and the chosen developer
workspace read-write. It uses a host loopback port and Jupyter's normal token
authentication. Stopping the launcher cleans up its own container ID, preserving
the developer files. It never edits the archive or mounts mission evaluation truth.

## GNU Radio block interface

- `ArchiveSource` outputs one vector of simultaneous antenna samples per item.
- A single vector Throttle optionally sets wall pacing. CLI `--speed 0` omits it.
- `WindowSkillSink` reassembles complete windows and calls the hook, then emits
  JSON PMT `results` messages and optional `results.jsonl`.
- The Companion viewer taps the same vector, splits channels and shows the
  selected antenna's spectrum. The display is not a calibrated dBm instrument.
  Its frequency center is zero for dechirped radar beat I/Q and the recorded RF
  carrier for ordinary SDR I/Q; the original RF carrier stays in `rx_freq` tags.

Constructors expose signatures without opening files, so Companion can inspect
the embedded wrappers. Recording validation and hook loading happen at start.
The generated graphs share a `RunState` variable for status/failure reporting.
The CLI also checks expected completion and returns nonzero on failure; for
scripted experiments use it rather than inferring success from a GUI window.

To insert channel-wise GNU Radio DSP, split the vector, process its channels,
and rejoin coherently. Preserve the original time/window tags and channel order.
The default whole-window hook expects unchanged item counts. Decimation,
interpolation, window truncation and latency-changing channel paths require an
explicit metadata/time adapter; their output must not be relabeled as the
original sample clock. If you replace the sink entirely, consume the tags in
your own skill and publish your own result contract.

Prepared graphs refer to the existing recording by a relative path. Opening them
from another directory, moving just the project or moving the dataset requires
updating `recording_dir`; `scripts/open_companion.sh` chooses the project working
directory. Graph generation never copies or edits raw sample files.

## Native installation

GNU Radio Python bindings must match the interpreter. On Ubuntu/Debian:

```bash
sudo apt-get install gnuradio python3-venv python3-pytest
/usr/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e .
rf-harness inspect /path/to/iq --verify
rf-harness prepare /path/to/iq --output workspace/my-skill
rf-harness run /path/to/iq --processor workspace/my-skill/processor.py --output runs/native-1
bash scripts/open_companion.sh workspace/my-skill/viewer.grc
```

The GUI launcher adds the checkout to `PYTHONPATH` for Companion and the Python
it generates. If your existing virtual environment lacks the system GNU Radio
bindings, select `RF_HARNESS_PYTHON=/usr/bin/python3` explicitly for the launcher.
Archive inspection/project generation need only Python 3.10+ and NumPy;
opening the generated notebook and plotting additionally require `.[lab]`.
Stream execution needs GNU Radio 3.10. GNU Radio/Qt GUI work is native; the default
container launcher is headless and needs no host display socket.

## Container builds and verification

```bash
docker build -t rf-skill-harness:local .
docker run --rm --network none --entrypoint python3 rf-skill-harness:local \
  -m pytest /opt/rf-skill-harness/tests -q -p no:cacheprovider
bash scripts/run_harness.sh --image rf-skill-harness:local --no-pull \
  demo --kind direction-finding --output datasets/demo
```

Behind an HTTPS inspection proxy, use the build-only public CA mount:
`docker build --secret id=proxy_ca,src=/path/to/combined-ca.pem -t rf-skill-harness:local .`.
The CA is not retained in the image. Do not disable TLS verification.

CI builds the image and runs native GNU Radio tests offline as an unprivileged
user. Tests cover exact multi-channel bytes, nanosecond tags, subset replay,
radar gate preservation, corruption/metadata failures, a failing developer hook,
Companion compilation and the Qt viewer offscreen. Synthetic demo captures
exercise the interface; physical RF or scene-accuracy validation is separate.
Both radar and direction-finding notebooks execute all cells in CI, including
custom plots and saved experiments. A parity test checks that a selected window
produces the same metadata and measurements through Python and GNU Radio.

The source uses Apache-2.0. GNU Radio and system packages retain their licenses;
consult those licenses when redistributing a combined runtime or derived work.

## Source-capture interoperability check

[The recorded check](interop-validation.json) covers one 4 ms receiver window
generated externally by airsim-rf's actual CPU Sionna/ADC pipeline. It contained
three retained target echoes, 302 terrain echoes, enabled receiver noise and
40,000 complex ADC samples. The developer hook received exactly the source
journal's sample SHA-256 and acquisition timestamp through the container
launcher and GNU Radio flowgraph. Only the `iq/` child was mounted into replay.
This verifies archive/receiver interoperability; it is not a hardware or flight
validation, runtime benchmark or scene-model calibration.
