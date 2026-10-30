# Install and run without Docker

The [v0.2.0 release](https://github.com/wa200508/RF-Skill-Harness/releases/tag/v0.2.0)
provides a **Python wheel and source archive with the built GNU Radio World editor,
WebAssembly DSP, Pyodide and scientific Python browser assets included**. Pip or uv
installs the local HTTP server and its Python dependencies. A browser renders the
interface and runs the streaming graph. Docker, Node, Emscripten, X11 and a host
Qt display are unnecessary for the browser workflow.

Release files live on GitHub Releases; the commands below use their explicit URLs,
not a similarly named package on PyPI. The wheel is about 38 MB. Once installed,
its browser assets are served from the installation and work offline with local
captures. The optional public example-recordings and AI features in upstream World
have their own network requirements.

## Pip: recommended isolated installation

Requires Python 3.10+ and a recent Chrome/Chromium or Firefox browser. On Ubuntu,
install `python3-venv` first if your Python cannot create virtual environments.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install \
  https://github.com/wa200508/RF-Skill-Harness/releases/download/v0.2.0/rf_skill_harness-0.2.0-py3-none-any.whl
.venv/bin/rf-world doctor
.venv/bin/rf-world serve --workspace ./my-rf-skill --transmit
```

Open **http://127.0.0.1:8090/**. A new project is created without needing a recording.
Add external test data, including several collects, when launching:

```bash
.venv/bin/rf-world serve --workspace ./my-rf-skill \
  --recordings /path/to/mission-one/iq --recordings /path/to/mission-two/iq
```

Receiver archives are read directly; no capture is copied into the skill. The
recording reader verifies each processed window. The local developer process has
your normal filesystem permissions; use stopped/stable archives. This installation
is not a filesystem sandbox. Saved changes and `my-rf-skill/exports/` stay in your
project directory. Ctrl+C stops the server. Restart with the same workspace to
resume the saved design. `--port 8091` starts another instance.

Windows uses `.venv\Scripts\python.exe` and `.venv\Scripts\rf-world.exe` instead.
The wheel is platform independent; the complete native test/export qualification
currently runs on **Ubuntu 24.04**. Other desktop systems can use the browser
workflow; native GNU Radio availability and ABI compatibility need their own
qualification. Use `doctor` to inspect the actual installation.

## uv

Install as a persistent tool:

```bash
uv tool install \
  https://github.com/wa200508/RF-Skill-Harness/releases/download/v0.2.0/rf_skill_harness-0.2.0-py3-none-any.whl
rf-world doctor
rf-world serve --workspace ./my-rf-skill --transmit
```

If uv's tool bin directory is not on PATH, use `uv tool update-shell` and reopen
the shell, or run directly through uv:

```bash
uv tool run --from \
  https://github.com/wa200508/RF-Skill-Harness/releases/download/v0.2.0/rf_skill_harness-0.2.0-py3-none-any.whl \
  rf-world serve --workspace ./my-rf-skill --transmit
```

## Downloadable installer

This creates a private virtual environment under
`~/.local/share/rf-skill-harness/venv`, without editing your shell or system Python:

```bash
curl -fsSL https://github.com/wa200508/RF-Skill-Harness/releases/download/v0.2.0/install.sh -o install-rf-harness.sh
bash install-rf-harness.sh
~/.local/share/rf-skill-harness/venv/bin/rf-world serve --workspace ./my-rf-skill --transmit
```

It verifies the wheel against release `SHA256SUMS` before installing. Options
`--prefix DIRECTORY`, `--python PYTHON` and `--version VERSION` select its location,
interpreter and release. `RF_HARNESS_PYTHON` and the active shell Python are honored.
The installer does not run sudo or install system GNU Radio automatically.

To uninstall this private installation, remove the chosen prefix after stopping
the server; your separately located skill projects and captures remain.

## Optional native testing and AMS-GRA export

Browser editing, Python blocks, verified I/Q replay and plots work with the Python
release alone. **Native test** and **Export AMS-GRA…** compile the graph using a
local GNU Radio installation. On Ubuntu 24.04:

```bash
sudo apt-get update
sudo apt-get install gnuradio python3-numpy python3-yaml
.venv/bin/rf-world doctor
.venv/bin/rf-world serve --workspace ./my-rf-skill \
  --recordings /path/to/mission/iq --native-python /usr/bin/python3
```

The launcher first checks its own Python, then `/usr/bin/python3`, for GNU Radio,
NumPy, PyYAML and `grcc`. Set `--native-python` or `RF_WORLD_NATIVE_PYTHON` for a
different install. Explicit selections fail clearly rather than silently falling
back. A pip/uv Python can have a different version and NumPy from the native
interpreter: the bridge loads only the harness package into that interpreter,
keeping its own GNU Radio/NumPy bindings intact. No whole virtual-environment
`site-packages` directory is injected into the native interpreter.

The exported result is an independent **RF MEL/UCI source package**. Local
compilation/export does not require Docker. Building and deploying its C++ host
additionally requires the pinned AMS-GRA dependencies, compiler libraries and a
real RF MEL provider/OWP connection; the generated Dockerfile remains one supported
way to build that deployed service. See [native deployment details](native-export.md).

## AirSim compatibility is a separate integration

| Path | Current status |
|---|---|
| Open airsim-rf receiver I/Q archives in the workbench | Supported recording format and acquisition metadata boundary |
| Run edited DSP against independent recordings | Supported browser replay and native window testing |
| Export a native AMS-GRA RF MEL/UCI skill | Implemented and qualified against native interface fixtures |
| Select that native export as an airsim-rf dashboard skill | **Not a supported direct connection:** current airsim-rf scenarios require `receiver-tcp-v2` |
| Live simulated native RF MEL/UCI operation | Requires a compatible simulation RF MEL provider/transport adapter and an end-to-end integration test |

The native interface is intentional. A simulation provider should supply that
interface so the RF skill remains unaware of the simulation. Matching an I/Q
format alone does not supply RF MEL jobs/callbacks, UCI/OWP control/report traffic,
TX scheduling or dashboard displays. This release does not establish live AirSim
compatibility, and installing the workbench does not modify AirSim or its skills.

## Source development and releases

A plain `pip install .` installs Python source but does not cross-compile or include
ignored generated browser assets. Developers can build the pinned editor once:

```bash
python3 scripts/build_world.py --output ./build/world
python3 -m pip install .
rf-world serve --workspace ./my-rf-skill --assets ./build/world
```

That development build needs Node 20+, npm, git and curl. The release **wheel and
sdist already include its result**; installing either does not need these tools.
To build the full release files from a checkout:

```bash
python3 -m pip install build
python3 scripts/build_release.py --assets ./build/world --output ./dist
```

The release workflow builds browser assets from the pinned source, verifies runtime
hashes, creates wheel/sdist/checksums, installs the wheel into a clean venv, and
uses real Chromium to exercise RX/TX DSP, plot data, capture switching, native
testing and export. The native bridge uses system GNU Radio in that test, while
the server runs in a separate pip environment. A version tag publishes the
qualified files to GitHub Releases. No Docker daemon is used in this release job.
