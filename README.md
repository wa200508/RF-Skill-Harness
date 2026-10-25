# RF Skill Harness

Develop an RF skill from saved receiver I/Q in **GNU Radio 3.10**. Point it at a
capture, edit `processor.py`, and replay without rerunning the simulation.
It works on CPU and has no AirSim, Sionna, GPU or detector dependency.

```mermaid
flowchart LR
  A["cf32 / SigMF + receiver config + acquisition journal"] --> B["Verify samples and metadata"]
  B --> C["GNU Radio: coherent antenna vectors + acquisition tags"]
  C --> D["INSERT SIGNAL PROCESSING HERE"]
  D --> E["GNU Radio result messages + JSONL"]
  C --> F["Optional Companion spectrum viewer"]
```

## Use your recorded I/Q

The container launcher needs Linux, Docker and host Python 3.10+. No GPU is needed.

Copy the **whole `iq/` folder** from
`airsim-rf/recordings/dashboard/replays/<run-id>/iq/`. A mission folder containing
`iq/` also works. Data stays on disk; the harness reads it without making another
bulk copy. For a remote simulation host, transfer that folder with your normal
file-copy tool first. Large recordings are not stored in this Git repository.

```bash
git clone https://github.com/wa200508/RF-Skill-Harness.git
cd RF-Skill-Harness

# Pull the CPU container and verify the capture.
bash scripts/run_harness.sh inspect /path/to/iq --verify

# Generate an editable GNU Radio project and processing hook.
bash scripts/run_harness.sh prepare /path/to/iq --output workspace/my-skill

# Edit workspace/my-skill/processor.py, then run your skill.
bash scripts/run_harness.sh run /path/to/iq \
  --processor "$PWD/workspace/my-skill/processor.py:process_window" \
  --output runs/experiment-1
```

The launcher pulls `ghcr.io/wa200508/airsim-rf:rf-skill-harness`, checks it exists,
mounts input data read-only and runs offline. It supports rootless Docker and
host-owned outputs. Its host Python honors the active virtual environment or
`RF_HARNESS_PYTHON`. GNU Radio runs inside the container with its matching Python.
The image is built from this repository and hosted in airsim-rf's existing public
GHCR package; that hosting location introduces no simulation dependency.
[Public image release workflow](https://github.com/wa200508/airsim-rf/blob/main/.github/workflows/rf-skill-harness.yml).

## Automatic RF acquisition schedule

`prepare` also builds **`timeline.html`** and `schedule.json` from the captured
metadata. Open the HTML directly in your host browser; no container web service
or manually defined schedule is needed. The Companion launcher opens it too.

- **Radar:** recorded receive windows plus configured TX chirps and RX/ADC gates,
  derived from the PRI, chirp duration, gate start/length and chirp origin.
- **ESM/receive-only:** recorded receive/collection windows; no emitter TX
  events are invented from passive I/Q.

Zoom and pan to individual events, or fit the whole capture. Long periodic runs
are stored compactly and aggregate visually when zoomed out. Exact original
timestamps remain available. Configured radar timing is labeled explicitly:
the archive does not record per-window TX enable/disable commands.

This RF acquisition schedule describes when samples were collected. GNU Radio's
separate buffer scheduler runs the DSP with backpressure; it does not redefine
acquisition time. [Timing and metadata details](docs/metadata.md#rf-acquisition-schedule).

## Insert signal processing here

```python
def process_window(iq, metadata, context):
    # iq: read-only complex64 volts, shape [samples, antenna channels]
    # metadata: original acquisition timestamp, sample offset, window, clipping
    # context: ordered channels, rate, carrier, antennas, calibration, radar gates
    return {"your_measurement": ...}  # Return a JSON-compatible dictionary.
```

The supplied hook reports RMS/peak voltage as a starter. Replace it with your
processing, or export a stateful callable. It sees complete coherent acquisition
windows regardless of GNU Radio's scheduler chunk size. [Hook and GNU Radio guide](docs/development.md).

**The samples include whatever the receiver captured.** In the current radar
demo that means target echoes, terrain clutter and receiver noise after ADC
modeling. Replay does not separate components, normalize amplitudes, resample,
regenerate waveforms or change Doppler. Radar samples are dechirped ADC volts;
the SDR scenario uses input-referred complex volts. Zero-filled gate gaps remain.

## Open GNU Radio Companion

Install GNU Radio 3.10 on the host (Ubuntu/Debian: `sudo apt-get install gnuradio`).
After `prepare`, open the editable graph with:

```bash
RF_HARNESS_PYTHON=/usr/bin/python3 \
  bash scripts/open_companion.sh workspace/my-skill/viewer.grc
```

`harness.grc` is the headless processing graph; `viewer.grc` adds an antenna
selector and spectrum. Both use the tagged, coherent source and your processing
hook. Native GNU Radio DSP can be inserted while preserving channel alignment
and tags. The host launcher exposes the package to Companion and its generated
Python; it opens the project from the correct working directory.

## Replay controls and output

`run` defaults to unpaced execution. Add `--speed 1` for sample-clock real-time
pacing, `--speed 2` for twice that rate, or `--start-window 10 --stop-window 30`
for a subset. Wall pacing never changes recorded acquisition time. A slow
processor applies backpressure. There is no total run deadline or implicit loop.
Use `--allow-partial` only for a stable, stopped copy of an interrupted capture.

Every experiment writes a new output directory:

- `context.json`: complete captured receiver configuration and channel order.
- `results.jsonl`: one timestamped result per processed coherent window.
- `run.json`: completion/failure, counts, pacing, elapsed time and GNU Radio version.

Corrupt data and processing errors fail the run; completed results remain.
Existing nonempty output directories are never overwritten.

[Receiver metadata and stream tags](docs/metadata.md) ·
[Native development, tests and container builds](docs/development.md)

For a small synthetic input before transferring a real recording:
`bash scripts/run_harness.sh demo --kind radar --output datasets/demo-radar`.
This tests the archive/processing path; it is not a scene propagation model.

Apache-2.0, aligned with the standalone RF skills. GNU Radio and its packaged
dependencies retain their own licenses.
