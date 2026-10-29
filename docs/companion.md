# Companion interface and native export

This branch uses GNU Radio Companion as the design surface. The editor stays
upstream; our library adds RF endpoints and a small tools panel. The default
deployment serves that desktop through a browser.

## Existing features and extensions

| Requested feature | Upstream support | Addition here |
| --- | --- | --- |
| Custom Python blocks from the GUI | Embedded Python Blocks, code editor, constructor parameters and signature discovery. | Starter block and export of its generated Python module. |
| Intermediate plots | Qt time, frequency, waterfall, constellation, vector and matrix sinks. | Per-plot export selection and removal of unused plot branches. |
| AMS-GRA skill export | GRC compiles Python flowgraphs and hierarchical blocks. | Compile the edited graph and run it behind the native RF MEL/UCI host. |
| Transmit design | Signal sources, modulators, tagged/message streams and radio hardware blocks. | RF Transmit Waveform endpoint and native fixed-waveform TX/guard/RX profile. |

The reviewed 3.10 GTK editor has no general-purpose menu-extension registry. Its
menus/actions are constructed in
[MainWindow.py](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/grc/gui/MainWindow.py)
and [Actions.py](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/grc/gui/Actions.py).
The experimental Qt editor also builds its tool menu explicitly in
[window.py](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/grc/gui_qt/components/window.py).
This branch uses supported **OOT block YAML**, discovered through
[`GRC_BLOCKS_PATH`](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/grc/core/Config.py),
with a separate tools panel. Companion's supported
[`run_command` option](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/grc/blocks/options.block.yml)
routes Run through the acquisition-binding preview wrapper. Save first; the panel
reads disk, not unsaved Companion editor buffers.

Embedded Python's
[signature discovery](https://github.com/gnuradio/gnuradio/blob/v3.10.12.0/grc/core/utils/epy_block_io.py)
instantiates constructor defaults. Vector-port defaults must declare the actual
antenna width. Changing a collect does not change these design parameters. A
one-channel skill can test many one-channel collects; a different antenna layout
is an explicit design change.

Plot implementations are the upstream
[Qt block definitions](https://github.com/gnuradio/gnuradio/tree/v3.10.12.0/gr-qtgui/grc).
For custom images, use a matrix sink or custom Qt block. Matrix dimensions,
range/Doppler coordinates and algorithm labels belong to that DSP/display;
the harness does not infer them. Plot taps participate in GNU Radio scheduling,
so heavy plots can slow preview. Headless export removes that work.

The GNU Radio wiki returned HTTP 403 here. Implementation decisions were checked
against the linked source and executed with the distro's GNU Radio 3.10.9.2;
wiki links in the README are reading references.

## Acquisition and dataset independence

`skill.grc`, declared helpers and `config/` define the skill. The selector's
`companion-session.json` and optional `recordings.json` are test-only and excluded
from export. Endpoint blocks and generated DSP contain no recording reader.
Preview verifies window hashes, binds the complex voltage matrix and acquisition
context, then constructs the graph. Deployment binds calibrated RF MEL data at
that same boundary.

RF Window Measurements gathers the complete processed window and calls its
declared local hook. `rf_window`, exact `rf_timestamp_ns`, `rf_context` and
`rx_rate` tags accompany the stream. Acquisition timestamps stay separate from
wall time. Context includes receiver antennas, calibration and radar-gate
metadata, without simulation scene truth or target labels.

RX edits must preserve count, channel order, volts and acquisition-time meaning.
A delay, rate conversion or changed voltage reference needs an explicit adapter;
connecting a block does not establish correct timing/calibration. Each window
starts a fresh graph and window hook. Continuous filter history and tracker state
need a persistent adapter. Finite windows may contain many pulses, subject to the
2,000,000-sample input bound and the native event limits.

`companion evaluate` compiles the saved design once into a staging area and runs
it on each collect. Each collect starts fresh; only result artifacts and hashes
persist. `rf-harness evaluate` offers the corresponding same-project workflow on
main. Neither changes deployment configuration when selecting a collect.
Different rates or waveform metadata do not make every algorithm compatible;
DSP must use those fields or reject unsupported inputs.

## Export selection

The panel lists active `qtgui_*` sinks, defaulting them off. Export traces backward
from measurements, TX and selected plots. It keeps those processing chains and
declarations, removes unused branches, and never reconnects the measurement path.
GUI variable controls become ordinary variables with saved default values.
Mandatory RF chains cannot be deselected; disabled blocks are omitted.

GNU Radio's own `grcc` validates/generates the graph. Generated Python and Embedded
Python modules are hashed and copied to `design/`. Standard file and hardware I/O
blocks are rejected: use RF endpoints. Helpers and dependencies must be declared
in `skill.json`. A sanitized source graph and separate hash remain for review.
Retained plots keep Qt; runtime pumps its event queue. Supply a display or explicit
`QT_QPA_PLATFORM=offscreen` for automated Qt checks. A no-plot export has no Qt GUI
imports in its generated graph. The image installs GNU Radio through apt, not PyPI.

## Transmit

The implementation follows RF MEL at
`a8161fa965b37879e2112533a896f210f721a02a`:

- [`CommonModulations.h`](https://gitlab.com/open-arsenal/ams-gra/hello-world-sk/interfaces/rf-mel/-/blob/a8161fa965b37879e2112533a896f210f721a02a/include/rfmel/jobs/CommonModulations.h): `WaveformBuffer` embeds arbitrary integer I/Q; event duration / sample count defines sample duration.
- [`JobEvent.h`](https://gitlab.com/open-arsenal/ams-gra/hello-world-sk/interfaces/rf-mel/-/blob/a8161fa965b37879e2112533a896f210f721a02a/include/rfmel/jobs/JobEvent.h): `TransmitEvent` controls carrier, timing, attenuation and modulation index.
- [`JobInterval.h`](https://gitlab.com/open-arsenal/ams-gra/hello-world-sk/interfaces/rf-mel/-/blob/a8161fa965b37879e2112533a896f210f721a02a/include/rfmel/jobs/JobInterval.h): sequence events cannot overlap and require switching time.
- [`WaveformTxEndpoint.h`](https://gitlab.com/open-arsenal/ams-gra/hello-world-sk/interfaces/rf-mel/-/blob/a8161fa965b37879e2112533a896f210f721a02a/include/rfmel/c2/WaveformTxEndpoint.h): dynamic waveform transfer is a separate interface, outside this first profile.

At startup, the host runs the TX graph once, validates finite normalized samples,
quantizes round(component × 32767), and retains the buffer for the job lifetime.
It requests RX/TX element groups, sets provider TX power/mode, and references the
waveform in each sequence. RX follows TX and a guard, with another guard at the
end. Power is whole-watt base power plus attenuation because the pinned
`TxPowerLevel` is unsigned integer. Establish supported commands and actual
delivered power with your provider.

Scheduled gaps between complete RX windows survive as timestamps; they are not
filled or concatenated into a continuous coherent CPI. Unexpected gaps or gaps
within a window fail. This TX profile requires one complete RX event per window.
A coherent multi-pulse radar skill spanning separate scheduled events needs an
explicit state/timing adapter; it is not supplied by this branch.

TX cannot depend on the RX endpoint in this fixed-waveform profile. Arbitrary
finite waveform construction is supported; feedback-driven TX, dynamic waveform
streaming, synchronized multi-device TX and simultaneous TX/RX need additional
adapters. Editing TX during replay changes the generated waveform, not the echo
already recorded in the selected I/Q file.

## Validation

Ubuntu 24.04 CPU container, GNU Radio 3.10.9.2, Qt offscreen; no radio or GPU.
The complete suite passed **78 tests**, including existing notebook/launcher
checks and new Companion tests:

- Custom Embedded Python edits change actual exported measurements.
- Two compatible collects use the same unchanged graph.
- Headless export removes Qt and unused plot branches.
- One selected plot compiles and executes through the real Qt sink.
- The tools panel switches collects and preserves checkbox selections.
- Export succeeds without any selected I/Q and excludes local capture paths.
- The generated TX waveform matches expected complex tone samples.

The C++ host was compiled against pinned starter-kit interfaces and exercised
through real factory/job/callback/OWP calls with an **interface fixture provider**.
Two RX windows reached the generated graph. The fixture checked all 256 TX samples
against graph output and validated non-overlapping events. At 2 MS/s: TX 128 µs,
guard 16 µs, RX 2048 µs, guard 16 µs, totaling 2208 µs. Timestamp low bits survived,
and the service outlived a silent subscription longer than its transaction timeout.
Harness and simulator packages were absent from that qualification run.

The starter report mapper is empty, so this checked service-status publication
without inventing a detection. It is not live Squall integration, physical radar
validation, full UCI schema validation or AMS-GRA certification. Qualify against
your deployed provider, power configuration, calibration and mission controls.
[Native export details](native-export.md).

[Recorded qualification summary](companion-validation.json).
The README's Companion screenshots were captured on 2026-10-29 from actual
GNU Radio 3.10.9.2 windows: GTK editor under Xvfb and the Qt tools/preview under
offscreen Qt. They show the stock starter, a real successful export with one
selected plot, and processing of the synthetic radar protocol fixture. The
example calibration and power settings were fixture values. No screenshots
were composited and no hardware or live provider is implied by those images.


## Browser server deployment

`Dockerfile.companion` contains GNU Radio, GTK3, Qt5, Mesa llvmpipe, Xvfb,
Openbox, Mousepad, x11vnc, websockify and [noVNC](https://github.com/novnc/noVNC).
The browser is a display/input client; Companion and RF processing execute on the
server. This preserves the existing editor and Qt plots without depending on
host GNU Radio graphics libraries. It is not a JavaScript rewrite of Companion.

`companion_server.py` supervises the virtual display, window manager, RF panel,
Companion editor, VNC endpoint and browser endpoint. Startup creates and binds a
real Qt OpenGL context, checks the software renderer with `glxinfo`, loads GTK,
then waits for both application windows and the editor's completed graph load.
`/health.json` is published only after those checks. A per-launch instance ID
prevents an unrelated/stale service from satisfying the host launcher's check.
A graphics or application failure stops the service with its relevant log.

The host launcher only uses Python's standard library. It pulls the image,
selects the rootful host UID/GID or rootless container-root mapping, mounts the
project writable and receiver captures read-only, and publishes port 6080 on
127.0.0.1. Neither X11/VNC ports nor a Docker socket enter the host interface.
Temporary HOME, display state and caches stay inside the container. SIGINT or
SIGTERM stops/removes the launcher's own container; project files persist.

Exports default to `/workspace/exports/<skill-name>` inside the persistent
workspace. The browser's **Edit project file** action opens the included editor,
so processor, reporter, deployment configuration and custom Python block code
can all be edited without host GUI tools. Browser reload/disconnect leaves the
same desktop running; stopping the run script ends it. A remote user can use
SSH port forwarding. Direct public hosting needs a separate authenticated TLS
front end; the shipped launcher binds only to loopback.

The image is published in airsim-rf's established public GHCR package as
`rf-skill-companion-browser`, with immutable `rf-skill-companion-browser-sha-<source>`
tags. Only this harness repository is built; the image has no AirSim/Sionna
runtime dependency. The release workflow pins the harness source and performs a
real Chromium/noVNC qualification before publishing, then verifies anonymous pull.

`qualify_companion_browser.py` connects Chromium, previews actual RX/TX Qt plots,
edits and saves a host-visible file through browser key events, exports a native
skill through the GUI, reconnects, checks cleanup, then restarts the same project.
Its screenshots and summary are uploaded by CI. The host launcher additionally
has rootful/rootless mapping and standalone/virtual-environment regression tests.
The synthetic radar fixture is an interface test, not a physical radar collect.

## Optional native desktop

The browser server is the normal route. For developers who deliberately want a
local desktop, Ubuntu 24.04 can install the matching libraries and use the
explicit native launcher:

```bash
sudo apt-get install gnuradio gir1.2-gtk-3.0 python3-gi-cairo python3-venv python3-pip
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e .
bash scripts/start_companion_native.sh --workspace "$PWD/my-rf-skill" --transmit
```


### Browser qualification evidence

[Recorded browser validation](companion-browser-validation.json) and the README's
browser screenshots were captured on 2026-10-29 from the built image, with no
source mounts, host X11 socket or GPU devices. Chromium interacted with the
noVNC canvas. The server created a real Qt OpenGL 4.5 context on llvmpipe, rendered
RX/TX plots, saved an editor change onto the host and exported a native TX/RX skill
through the GUI. Browser reconnect and container restart preserved the expected
session/project state, and shutdown removed the launcher's container. The I/Q was
the bundled synthetic radar fixture; this checks deployment, not physical RF accuracy.
