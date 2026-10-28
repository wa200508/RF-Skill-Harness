"""Generate editable GRC projects with tagged coherent source and developer hook."""
import json
import os
from pathlib import Path
import shutil

from .paths import new_directory
from .archive import Archive


def flowgraph(archive, recording_dir, *, gui=False, allow_partial=False):
    count = len(archive.channels)
    dechirped = archive.config.get("acquisition", {}).get("type") == "fmcw-dechirped"

    def block(name, identifier, parameters, x, y):
        return {"name": name, "id": identifier, "parameters": parameters,
                "states": {"coordinate": [x, y], "rotation": 0, "state": "enabled"}}

    source_code = '''import rf_skill_harness.gr_blocks as replay
class blk(replay.ArchiveSource):
    def __init__(self, recording_dir='.', channels=CHANNELS, start_window=0,
                 stop_window=-1, allow_partial=False, state=None):
        replay.ArchiveSource.__init__(self, recording_dir, channels, start_window,
                               stop_window, allow_partial, state)
'''.replace("CHANNELS", str(count))
    sink_code = '''import rf_skill_harness.gr_blocks as replay
class blk(replay.WindowSkillSink):
    def __init__(self, recording_dir='.', channels=CHANNELS, processor='processor.py:process_window',
                 output_dir='', allow_partial=False, state=None):
        replay.WindowSkillSink.__init__(self, recording_dir, channels, processor,
                                 output_dir, allow_partial, state)
'''.replace("CHANNELS", str(count))
    entries = [
        block("imports", "import", {"imports": "from rf_skill_harness.results import RunState"}, 20, 20),
        block("recording_dir", "parameter", {"type": "str", "value": recording_dir, "label": "Recording directory"}, 20, 100),
        block("processor", "parameter", {"type": "str", "value": "processor.py:process_window", "label": "Your processor.py hook"}, 250, 100),
        block("output_dir", "parameter", {"type": "str", "value": "", "label": "New results directory (empty = messages only)"}, 540, 100),
        block("samp_rate", "variable", {"value": str(archive.rate)}, 20, 180),
        block("carrier_hz", "variable", {"value": str(archive.carrier)}, 200, 180),
        block("display_center_hz", "variable", {"value": "0" if dechirped else str(archive.carrier)}, 800, 260),
        block("channel_count", "variable", {"value": str(count)}, 400, 180),
        block("run_state", "variable", {"value": "RunState()"}, 600, 180),
        block("start_window", "parameter", {"type": "intx", "value": "0", "label": "First window (inclusive)"}, 20, 260),
        block("stop_window", "parameter", {"type": "intx", "value": "-1", "label": "Stop window (exclusive; -1 = EOF)"}, 260, 260),
        block("playback_speed", "parameter", {"type": "eng_float", "value": "1", "label": "Playback speed (positive)"}, 540, 260),
        block("recorded_iq", "epy_block", {"_source_code": source_code, "recording_dir": "recording_dir",
              "channels": "channel_count", "start_window": "start_window", "stop_window": "stop_window",
              "allow_partial": str(allow_partial), "state": "run_state"}, 20, 410),
        block("shared_pacer", "blocks_throttle", {"type": "complex", "samples_per_second": "samp_rate * playback_speed",
              "vlen": "channel_count", "ignoretag": "True"}, 310, 410),
        block("insert_signal_processing_here", "epy_block", {"_source_code": sink_code, "recording_dir": "recording_dir",
              "channels": "channel_count", "processor": "processor", "output_dir": "output_dir",
              "allow_partial": str(allow_partial), "state": "run_state"}, 600, 410),
        block("results", "blocks_message_debug", {"en_uvec": "True"}, 970, 410),
    ]
    connections = [["recorded_iq", "0", "shared_pacer", "0"],
                   ["shared_pacer", "0", "insert_signal_processing_here", "0"],
                   ["recorded_iq", "errors", "results", "print"],
                   ["insert_signal_processing_here", "results", "results", "print"],
                   ["insert_signal_processing_here", "errors", "results", "print"]]
    if gui:
        entries.extend([
            block("view_channel", "variable_qtgui_chooser", {"type": "int", "value": "0", "label": "Antenna channel",
                  "num_opts": "0", "options": str(list(range(count))), "labels": repr(archive.channels), "widget": "combo_box"}, 800, 180),
            block("coherent_split", "blocks_vector_to_streams", {"type": "complex", "num_streams": "channel_count", "vlen": "1"}, 600, 650),
            block("channel_selector", "blocks_selector", {"type": "complex", "num_inputs": "channel_count", "num_outputs": "1",
                  "input_index": "view_channel", "output_index": "0", "enabled": "True", "vlen": "1"}, 850, 650),
            block("spectrum", "qtgui_freq_sink_x", {"type": "complex", "name": "ADC beat-frequency offset (volts)" if dechirped else "Recorded receiver I/Q (volts)",
                  "fftsize": "2048", "fc": "display_center_hz", "bw": "samp_rate", "nconnections": "1",
                  "ymin": "-160", "ymax": "0", "label": "FFT magnitude", "units": "dB", "autoscale": "True", "grid": "True"}, 1100, 650),
        ])
        connections.append(["shared_pacer", "0", "coherent_split", "0"])
        connections.extend([["coherent_split", str(i), "channel_selector", str(i)] for i in range(count)])
        connections.append(["channel_selector", "0", "spectrum", "0"])
    return {"options": {"parameters": {"id": "rf_skill_viewer" if gui else "rf_skill_replay",
            "title": "RF Skill Harness — INSERT SIGNAL PROCESSING HERE", "author": "RF Skill Harness",
            "output_language": "python", "generate_options": "qt_gui" if gui else "no_gui", "run_options": "run",
            "description": "One vector item contains all simultaneous antenna voltages. Acquisition rx_time/rate/freq, exact nanoseconds, window boundaries and full receiver metadata are tagged. Edit processor.py or insert native GNU Radio blocks while preserving tags and channel alignment. No scene truth or signal regeneration."},
            "states": {"coordinate": [0, 0], "rotation": 0, "state": "enabled"}},
            "blocks": entries, "connections": connections, "metadata": {"file_format": 1}}


def prepare_project(directory, output, *, allow_partial=False):
    archive = Archive(directory, allow_partial=allow_partial)
    root = new_directory(output)
    relative = os.path.relpath(archive.path, root)
    for filename, gui in (("harness.grc", False), ("viewer.grc", True)):
        (root / filename).write_text(json.dumps(flowgraph(archive, relative, gui=gui,
                                                       allow_partial=allow_partial), indent=2) + "\n")
    for filename in ("processor.py", "plot_output.py", "plot_results.py"):
        shutil.copyfile(Path(__file__).with_name(filename), root / filename)
    from .workbench import notebook
    (root / "analysis.ipynb").write_text(json.dumps(notebook(), indent=2) + "\n")
    (root / "project.json").write_text(json.dumps({"schema_version": 1,
        "recording_dir": relative, "allow_partial": allow_partial}, indent=2) + "\n")
    (root / "context.json").write_text(json.dumps(archive.context(), indent=2) + "\n")
    from .schedule import write_timeline
    write_timeline(archive, root)
    (root / "README.md").write_text("""# Your RF skill workspace

Edit **processor.py → process_window(iq, metadata, context)**.
Open **analysis.ipynb** in JupyterLab to select a window, run the hook, inspect
standard I/Q plots and visualize your outputs. Save processor.py and rerun its
processing cell to load fresh code. Edit **plot_output.py** for your custom plot.
The notebook runs your Python hook directly; GNU Radio replay separately tests
the streaming integration. **plot_results.py** plots saved scalar CLI results.
Open **timeline.html** to inspect the RF acquisition schedule automatically
derived from metadata: receive windows, plus configured radar TX and ADC gates.
`iq` has shape `[sample, channel]`, complex64 volts, with recorded channel order.
Open **harness.grc** or **viewer.grc** in GNU Radio Companion from this directory.
`recording_dir` points to the original read-only archive; no I/Q is copied here.
If you move this workspace or archive, update project.json and the GRC parameter,
or use the container lab launcher with the recording's new path.
Use the CLI for explicit failure status and an unpaced run:

```sh
rf-harness run /path/to/iq --processor "$PWD/processor.py:process_window" --output /path/to/new-results
```

GRC defaults to 1x playback. Bypass its single shared Throttle for unpaced DSP.
Do not give that Throttle a zero or negative sample rate. Rate-changing DSP needs
an explicit acquisition-time/tag adapter. Zero-filled radar gate gaps are retained.
Context is receiver metadata, not simulator paths, scene truth or target labels.
""")
    return {"project": str(root), "recording": str(archive.path), "channels": archive.channels,
            "files": ["analysis.ipynb", "project.json", "processor.py", "plot_output.py", "plot_results.py",
                      "harness.grc", "viewer.grc", "context.json", "timeline.html", "schedule.json", "README.md"]}
