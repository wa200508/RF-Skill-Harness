# RF Skill Harness — GNU Radio Companion branch

Develop the skill in **GNU Radio Companion 3.10**, test the same flowgraph against
replaceable I/Q collects, then export it behind the **native AMS-GRA RF MEL/UCI
interface**. This experimental route lives on `feature/gnuradio-companion`;
[main](https://github.com/wa200508/RF-Skill-Harness/tree/main) keeps the
[JupyterLab workbench](docs/jupyter-workbench.md).

The browser workbench runs the real Companion editor and RF tools panel:

![Companion and RF tools connected through the noVNC browser client](docs/images/companion-browser-desktop.png)

<details>
<summary>See browser-rendered RX/TX plots and a saved native export</summary>

These are actual Chromium/noVNC screenshots processing the bundled **synthetic
radar protocol fixture** and generating the starter TX tone.

![RX waveform, spectrum and TX waveform rendered inside the browser desktop](docs/images/companion-browser-plots.png)

The export below was created through the browser GUI and saved in the persistent
workspace. Example calibration/power settings are interface-fixture values.

![Completed native TX/RX export in the browser workbench](docs/images/companion-browser-export.png)

</details>

```mermaid
flowchart LR
  B["Browser / noVNC"] <-->|Display and input| G
  G["Companion: skill.grc + custom Python blocks"] --> C["GNU Radio compiler"]
  D["Selectable test collects: I/Q + receiver metadata"] --> P["Preview / multi-collect evaluation"]
  C --> P
  P --> V["Qt plots tapped at DSP stages"]
  G --> E["Export dialog: select plots to retain"]
  E --> S["Generated graph + declared DSP + native host"]
  RX["RF MEL receiver callbacks"] --> S
  S --> TX["RF MEL TX waveform + TX/guard/RX schedule"]
  S --> U["UCI reports over OWP"]
```

## Start Companion in your browser

The default deployment runs **Companion, the RF tools panel, the code editor and
Qt plots inside a server container**. noVNC carries its desktop to the browser;
GTK, Qt, X11 and Mesa are installed in the image. Graphics use **server-side
software OpenGL**. The host needs Docker, Python 3.10+, curl and a browser; it
needs no GNU Radio installation, GPU, display server or X11 forwarding.

From this branch's checkout:

```bash
bash scripts/start_companion.sh --workspace "$PWD/my-rf-skill" --transmit
```

Or download just the launcher:

```bash
curl -fsSL https://raw.githubusercontent.com/wa200508/RF-Skill-Harness/feature/gnuradio-companion/scripts/start_companion.sh \
  -o start_companion.sh
bash start_companion.sh --workspace "$PWD/my-rf-skill" --transmit
```

Open **http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale** once the launcher
reports **Graphics ready**. It pulls/verifies the dedicated public image
`ghcr.io/wa200508/airsim-rf:rf-skill-companion-browser`. The standalone launcher
fetches its small stdlib host helper pinned to that image's source revision.
The active Python virtual environment or `RF_HARNESS_PYTHON` is honored.

The service checks a real Qt OpenGL context, the Mesa software renderer, GTK and
both application windows before declaring readiness. It publishes HTTP/WebSocket
traffic on host loopback, including rootless Docker; raw VNC and X11 stay inside
the container. No privileged mode, GPU device or host X socket is used.

Add captures without changing the skill:

```bash
bash scripts/start_companion.sh --workspace "$PWD/my-rf-skill" \
  --recordings /path/to/collect-one/iq --recordings /path/to/collect-two/iq
```

Captures are mounted **read-only**. Project changes, saved experiments and
**`my-rf-skill/exports/`** remain on the host. Use **Edit project file…** (Ctrl+O)
in the tools panel to edit Python/configuration files in the browser. Embedded
Python Blocks also open in the included Mousepad editor. Ctrl+Enter previews;
Ctrl+Shift+E opens the native export dialog. Maximize plot windows for more room.

Closing/reloading the browser preserves the running desktop. **Ctrl+C in the
launcher stops and removes its container**; restart the same command to resume
the saved project. `--port 6081` starts a second desktop. For a remote Linux
server, forward the service with `ssh -L 6080:127.0.0.1:6080 user@server` and use
the same local browser URL. The default service is intended for loopback/SSH
access, not direct unauthenticated public hosting.

For a local source build: add `--build --image rf-companion:local`. The optional
[native desktop route](docs/companion.md#optional-native-desktop) remains available.

## Design → test → inspect → revise

1. Edit **`skill.grc` in Companion**. The RF Receive Endpoint supplies simultaneous
   antenna-voltage vectors. Insert standard GNU Radio DSP or custom blocks before
   RF Window Measurements. The starter includes an editable Embedded Python gain.
2. Use **Embedded Python Block → Open in Editor** for custom streaming DSP.
   Companion discovers its ports and parameters. Configure your text editor in
   Companion preferences. Use `processor.py` for complete-window array processing
   and measurement output, and `report.py` for the explicit UCI report mapping.
3. Add **Qt GUI time, frequency, waterfall, constellation, vector or matrix sinks**
   at the stages you want to inspect. Plots are branch taps. The starter shows
   time/spectrum after custom DSP and, for TX projects, the TX waveform. Select an
   antenna before connecting coherent vectors to scalar plot sinks.
4. **Save**, select an external capture and window in the tools panel, then
   **Preview saved graph**. Companion's normal Run action uses the same selected
   capture. Processing runs once; the completed plots remain open until closed.
   Measurements appear in the panel's log.
5. Select a different collect and rerun the **same skill**. No new project, I/Q
   copy or export is needed. Preview compiles the saved graph into temporary files.

For starter data, generate synthetic protocol fixtures and use **Add capture…**:

```bash
rf-harness demo --kind radar --output ./captures/development
rf-harness demo --kind radar --output ./captures/held-out
```

These fixtures test the interface; they are not scene/hardware validation. They
share a deterministic seed; use genuinely different real collects for held-out
validation. The starter graph declares one antenna channel. For an array design,
change `channel_count`, the Embedded Python default vector size, and deployment
channel configuration together. Incompatible channel counts fail explicitly.
Sample rate, carrier, antenna calibration, timestamps and radar-gate metadata
come from each collect. Your algorithm must use them or reject unsupported modes.

Evaluate one saved graph on several complete collects without opening plots:

```bash
python -m rf_skill_harness.companion evaluate ./my-rf-skill \
  --recordings ./captures/development --recordings ./captures/held-out \
  --output ./evaluations/comparison-1
```

The graph is compiled once. Each collect starts fresh and has its own
`results.jsonl`, `context.json` and `run.json`. `evaluation.json` records design
hashes and per-collect timings. The source project is unchanged.

## Export the RF skill

Configure deployment in **`config/`**, independently of test data: RF MEL provider,
stream/channel mapping, calibrated volts per signed integer count, acquisition
settings and OWP identity/topics. Calibration starts unset; export requires an
explicit value. TX also needs provider-supported power, attenuation, guard and
waveform sample count. `report.py` initially reports no detections.

Click **Refresh from saved graph** in the tools panel. Its export list has a
checkbox per active Qt plot. **Unchecked plots and their unused branches are
removed**; RX measurements and TX chains stay mandatory. Check a plot to retain
it, then **Export native AMS-GRA RF skill…** to a new directory. Retained Qt plots
need a deployment display; the default headless export suits an unattended
service. Equivalent CLI:

```bash
python -m rf_skill_harness.companion export ./my-rf-skill --output ./exports/my-rf-skill
# Optional: add --keep-plot rx_spectrum to retain that plot.
cd exports/my-rf-skill
docker build -t my-rf-skill:local .
docker build --target test -t my-rf-skill:test .
```

The package includes the **compiled edited flowgraph**, generated Embedded Python
modules, declared DSP/helper files, sanitized `source/skill.grc`, source hashes,
deployment configuration, native C++ host and Dockerfile. It uses the OS GNU
Radio package. No recording catalog, selected input path, I/Q, experiments,
notebooks, replay reader or harness dependency enters the export. Deployment
obtains new samples from RF MEL at the same DSP boundary used by replay.

The native host loads provider factories, requests a Virtual Aperture and jobs,
receives `ProductRxEndpoint` callbacks, applies calibration and publishes UCI
reports through OWP. **Export is not certification.** Qualify it against your
actual starter-kit provider, hardware calibration and UCI schema.

## Transmit and execution profile

With `--transmit`, standard sources/modulators and custom Python blocks define
TX in the graph. The starter uses a small complex tone. Put **Head(`tx_samples`)
before the TX endpoint and its plot taps** so execution is finite. TX components
are normalized full scale in `[-1, 1]`; RX remains volts. Over-range TX samples
fail instead of being silently clipped.

The native profile generates a **fixed arbitrary waveform** from that branch,
quantizes to ComplexINT16, and repeats it using RF MEL `WaveformBuffer` and
`TransmitEvent`. Each sequence is **TX → guard → RX → guard** with no overlapping
events. The provider must support the waveform modulation, TX/RX groups and power
mode. Changing TX while replaying a recording cannot change its already-recorded
echo; closed-loop validation needs a live simulator or radio provider.

This first profile executes **independent finite windows**. Native streaming
block history resets at each window; RX DSP must preserve sample count, channel
order, volts and acquisition time. Dynamic waveform streaming, feedback-dependent
TX, continuous block history and simultaneous TX/RX need additional adapters.
These capabilities are not claimed here. [Detailed boundaries](docs/companion.md).

## Code, evidence and references

| Location | Purpose |
| --- | --- |
| `src/rf_skill_harness/companion.py` | Creation, GRC validation/code generation, plot pruning, preview/evaluation/export. |
| `src/rf_skill_harness/companion_gui.py` | Capture/window selector and per-plot export dialog. |
| `src/rf_skill_harness/companion_blocks/` | OOT YAML definitions for RF receive, measurements and TX endpoints. |
| `src/rf_skill_harness/companion_runtime.py` | Portable `skill_io.py` acquisition binding and GNU Radio execution. |
| `src/rf_skill_harness/native/` | RF MEL C++ host, calibration, native TX/RX scheduling and UCI/OWP reporting. |
| [Companion findings and validation](docs/companion.md) | Upstream sources, extension choices and tested limits. |
| [Native export guide](docs/native-export.md) | Provider and schema qualification. |
| [Receiver metadata](docs/metadata.md) | Voltage units, channels, acquisition tags and radar gates. |

Validated with actual GNU Radio **3.10.9.2**: custom Python edits reach the exported
DSP; selected plots run and unselected branches disappear; one skill evaluates
multiple collects; export needs no selected I/Q. A native interface fixture also
verified actual TX samples and non-overlapping TX/RX events. The full suite
passed **78 tests**. [Validation scope](docs/companion.md#validation).

Reused upstream features:
[Embedded Python Blocks](https://wiki.gnuradio.org/index.php/Embedded_Python_Block),
[out-of-tree modules](https://wiki.gnuradio.org/index.php/OutOfTreeModules),
[Qt sinks](https://github.com/gnuradio/gnuradio/tree/v3.10.12.0/gr-qtgui/grc),
and the pinned [AMS-GRA RF MEL interface](https://gitlab.com/open-arsenal/ams-gra/hello-world-sk/interfaces/rf-mel/-/tree/a8161fa965b37879e2112533a896f210f721a02a).

Harness source is Apache-2.0. GNU Radio and generated GNU Radio code retain their
upstream GPL obligations; review licensing of a combined distributed skill.
