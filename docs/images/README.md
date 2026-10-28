# Workbench screenshots

Captured from the live JupyterLab 4.6.4 workbench on 2026-10-28, using the native
Python installation described in [development](../development.md). These are
browser screenshots, with the stock notebook, processor and plotting hooks.

The captures are the bundled **synthetic protocol fixtures**. The plots show
actual processing outputs from those files using the voltage-statistics starter
processor and standard input diagnostics. Native export produces a source package;
provider integration and schema qualification are separate deployment steps.

| Image | Interaction shown |
| --- | --- |
| `workbench-process.png` | Select the radar fixture, choose window 0 and its antenna, then click Process window. |
| `workbench-iq.png` | Scroll to the standard input diagnostics: I/Q volts, FFT magnitude and spectrogram. |
| `workbench-export.png` | Switch to the direction-finding fixture, process eight channels, save the experiment, and export the receive skill. The file browser shows the actual generated directory. |

To reproduce the content, create a new independent project and two fixture
captures. In a native Python environment with `.[lab]` installed:

```bash
rf-harness init --output /tmp/rf-workbench/my-rf-skill
rf-harness demo --kind direction-finding --output /tmp/rf-workbench/captures/direction-finding
rf-harness demo --kind radar --output /tmp/rf-workbench/captures/radar
```

Set the project's `recordings.json` to:

```json
{
  "direction-finding fixture": "/tmp/rf-workbench/captures/direction-finding",
  "radar fixture": "/tmp/rf-workbench/captures/radar"
}
```

Start JupyterLab with the project as its root:

```bash
python -m jupyterlab --ServerApp.root_dir=/tmp/rf-workbench/my-rf-skill
```

Open `analysis.ipynb` and Run All. Select the captures and use the buttons as described above. For the screenshots,
code inputs were collapsed with the notebook's `jupyter.source_hidden` cell
metadata. The viewports were 1440 pixels wide; screenshots preserve the browser
view without compositing or replacing UI elements.

For the export illustration only, `config/receiver.json` was copied from the
direction-finding fixture's receiver configuration. Its eight channel names were
mapped to stream IDs 1–8 in `config/runtime.json`, with an **example** scale of
0.001 V/count per channel. That scale is not a hardware calibration. Click Export
native skill and open `exports/my-rf-skill/` in the file browser. The export's
`certified: false` result is intentional; real provider and schema qualification
remain the deployment steps described in [native export](../native-export.md).

Only screenshot assets and documentation are checked in. Test I/Q, experiment files, authentication
tokens and generated export packages are excluded.
