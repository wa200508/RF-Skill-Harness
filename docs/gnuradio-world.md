# GNU Radio World RF skill development

The main-branch workbench extends [777arc/gnuradio-world](https://github.com/777arc/gnuradio-world)
at `96e805fb694e3e797b72ebfad30232c732e3f257`. Its TypeScript canvas, CodeMirror
Python editor, Emscripten GNU Radio scheduler and WebAssembly Qt plotting sinks
run in the browser. Our local HTTP server adds capture binding, persisted project
files, isolated native tests and AMS-GRA export. It starts no desktop process.

## Code map

| Concern | Location |
|---|---|
| Upstream source revision and exact runtime hashes | `world/upstream-lock.json` |
| Build editor with a small, checked integration patch | `scripts/build_world.py` |
| RF toolbar, capture selector, file editor, export dialog | `world/rf-extension.ts`, `world/rf-extension.css` |
| HTTP API, verified window selection, Range reads, persistence | `src/rf_skill_harness/world_server.py` |
| Independent design template and browser/native endpoint conversion | `src/rf_skill_harness/world.py` |
| Native graph validation/compilation, visualization pruning | `src/rf_skill_harness/companion.py` |
| Portable acquisition binding and GNU Radio RF endpoint blocks | `src/rf_skill_harness/companion_runtime.py` (copied to project `skill_io.py`) |
| Receiver archive, coherent window verification and metadata | `src/rf_skill_harness/archive.py` |
| Native MEL/UCI source-package export | `src/rf_skill_harness/exporter.py`, `src/rf_skill_harness/native/` |
| Container, host launcher and lifecycle | `Dockerfile.world`, `scripts/start_world.sh`, `scripts/launch_world.py` |
| Actual browser/native qualification | `scripts/qualify_world.py`, `tests/test_world.py`, `tests/test_world_launcher.py` |

The integration adds three narrow hooks to the pinned upstream editor: initialize
our toolbar after editor readiness; bind the declared RF receive File Source to
a session descriptor; and supply acquisition rate/carrier/metadata to the run-only
graph. It does not replace the upstream canvas, Python editor, scheduler or plots.
Upstream has no general stable extension API for these operations, so the build
checks its insertion points and fails on an incompatible source version.

## Data and skill separation

`skill.grc`, `processor.py`, `report.py`, declared source helpers and `config/`
are the design. Mounted receiver archives are external test inputs. Selecting a
capture does not create a skill or edit deployment configuration. Save explicitly
persists design edits; the browser's selected input is never saved in the graph.

The receive endpoint is an ordinary World File Source named `receive`, with
`file=RF_RECEIVE`, `type=complex` and vector length `channel_count`. The declared
measurement/TX endpoints are Null Sinks. Their role mapping is in `skill.json`.
Native export replaces those exact declared endpoints with portable RF MEL-bound
RX, measurements and TX endpoints, then uses GNU Radio's own compiler. The same
DSP in between is retained. This also lets the native exporter prune plots
without reverse-engineering an arbitrary browser graph.

A selected window is validated against the declared channel count and read
through the archive reader, including payload hashes. Channels are interleaved
sample by sample in `[sample, channel]` order, not concatenated in time. At most
two selected coherent windows are held in server memory. The browser reads that
finite byte stream through HTTP Range requests; the entire flight is not loaded.
Starting another selection stops the previous browser run. Inputs stay read-only.

`RF_WINDOW` and `RF_CONTEXT` are injected only into the run-time copy of Embedded
Python source. The native graph instead obtains them from its acquisition
binding. Native metadata is refreshed when a generated graph is reused for a new
window. Use acquisition-dependent metadata in `work()` or `processor.py`:
GNU Radio's compiler also instantiates Embedded Python Blocks to discover ports,
so metadata is empty during compile-time introspection. Ports/constructor defaults
must describe a dataset-independent interface. Incompatible channel layouts need
an explicit redesign; selecting a capture never silently rewires an array.

Nanosecond timestamps exceed JavaScript's exact integer range. The HTTP API
transports such integers as decimal strings, and the Python block receives exact
integer `timestamp_ns`/`first_timestamp_ns` values. Receiver configuration,
radar acquisition gates and the original sample clock are available in context;
playback wall time is not substituted for acquisition time. These metadata values
are not inserted as GNU Radio stream tags by World's plain File Source. Add tag
handling explicitly if your algorithm needs it; the native RF endpoint emits its
existing acquisition tags.

## Processing and export limits

World's Embedded Python Blocks run on Pyodide, using its GNU Radio shim and
supported packages. NumPy and SciPy are bundled. It is not a general desktop
Python environment; arbitrary native Python wheels cannot simply be imported.
The [upstream implementation](https://github.com/777arc/gnuradio-world/blob/96e805fb694e3e797b72ebfad30232c732e3f257/docs/embedded-python.md)
describes its extra input/output copying and supported scheduler methods.

The browser runs streaming DSP and plot taps. Our complete-window `processor.py`
and UCI `report.py` execute during **Native test** and in the exported skill;
World does not import these arbitrary project files into Pyodide. This boundary
is deliberate and visible in the UI. Native test runs in a fresh subprocess so
state from one collect cannot contaminate another. Browser reloads stop the
browser graph but preserve the explicitly saved design.

The native target currently uses distro GNU Radio 3.10.9.2 and NumPy 1.x. World
uses its upstream pinned GNU Radio/Pyodide build. Identical `.grc` syntax does not
guarantee identical supported blocks or numerical behavior. Browser-only
JavaScript, WebUSB and World-specific blocks have no automatic native RF MEL
translation. Export errors identify unsupported blocks, and native testing is
part of deployment qualification. Retained Qt plots require a display; default
exports are headless. Do not benchmark the browser as if it were a native GPU
renderer.

TX design uses ordinary source/modulator/filter blocks, bounded by
`Head(tx_samples)` and connected to the declared TX endpoint. The native host
prepares a finite complex waveform, sends it through RF MEL and schedules
TX/guard/RX/guard without overlap. This profile does not implement adaptive
streaming TX, full duplex or cross-window coherent DSP state. Browser USB radio
support in upstream World is a separate feature, not an RF MEL export mechanism.

## Reproducible deployment

A [native Python release](install.md) bundles these assets into a pip/uv wheel
and source archive. `rf-world serve` binds localhost without a container. The
Docker route below remains an alternative.

The source revision, runtime build stamp and SHA-256 hashes of every downloaded
runtime/Pyodide artifact live in one lock file. The editor is built from source;
all runtime assets are downloaded **during the container build**, checked, then
served locally. A changed public upstream artifact causes a build failure,
rather than mixing old JavaScript and new WebAssembly. An existing published
image continues working after upstream deployment changes. Updating the pin
requires rerunning browser and native qualification.

The host launcher honors its Python environment, maps ownership for rootful or
rootless Docker, publishes only loopback HTTP, and waits for the matching launch
instance. It mounts only discovered receiver archives and the writable project.
Ctrl+C stops/removes only that launch's container. The HTTP server requires
localhost Host headers and same-origin JSON writes. Use SSH forwarding for remote
use, or add your own authenticated HTTPS deployment rather than publicly exposing
this development file-editor service.

## Validation

[Local validation results](world-validation.json) record the actual browser run
and 81 passing regression tests (4 Jupyter-only checks skipped in the browser
development image). The recorded local run mounts development source/assets;
CI additionally exercises the installed image without source mounts.

The browser qualifier loads the installed server image without source mounts,
changes the Python gain, runs real WebAssembly DSP, checks block sample counters
and plot data, changes captures without changing the saved graph, edits a project
file, runs the native window test, exports a headless native TX/RX skill, reloads
the project, and checks that no I/Q entered the export. Test calibration/power
are synthetic interface-fixture values.

```bash
python -m pip install numpy playwright
python -m playwright install chromium
python scripts/qualify_world.py --image rf-world:local --output ./world-validation
```

The host launcher suite separately exercises rootful/rootless mappings,
standalone/check-out launchers, Python selection, read-only archive mounts and
signal cleanup. Native graph tests verify edited gain, exact timestamps,
independent collects, TX samples and export pruning. Existing native C++ RF MEL
factory/callback/OWP fixture tests remain in CI.
