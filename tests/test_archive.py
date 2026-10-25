import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo


@pytest.fixture(params=["direction-finding", "radar"])
def recording(request, tmp_path):
    return write_demo(tmp_path / "capture with spaces" / "iq", kind=request.param)


def test_exact_channel_bytes_metadata_and_mission_path(recording):
    archive = Archive(recording.parent)
    windows = list(archive.windows())
    assert archive.inspect(verify=True)["sample_hashes_verified"]
    context = archive.context()
    assert context["receiver_config"] == json.loads((recording / "receiver-config.json").read_text())
    assert context["first_timestamp_ns"] == 123_456_789_000_000_007
    assert context["timestamp_clock"].endswith("not assumed to be UTC")
    for column, name in enumerate(archive.channels):
        actual = np.concatenate([w.samples[:, column] for w in windows])
        assert actual.astype("<c8").tobytes() == (recording / (name + ".sigmf-data")).read_bytes()
    selected = list(archive.windows(start_window=2, stop_window=4))
    assert [w.index for w in selected] == [2, 3]
    assert selected[0].sample_start == 2 * len(windows[0].samples)
    assert not windows[0].samples.flags.writeable


def test_corruption_fails_before_corrupt_window_is_yielded(recording):
    a = Archive(recording)
    path = recording / (a.channels[0] + ".sigmf-data")
    with path.open("r+b") as stream:
        stream.seek(10)
        old = stream.read(1)
        stream.seek(10)
        stream.write(bytes([old[0] ^ 1]))
    with pytest.raises(ValueError, match="SHA-256"):
        next(a.windows())


def test_interrupted_prefix_requires_opt_in_and_ignores_unpublished_tail(recording):
    path = recording / "manifest.json"
    m = json.loads(path.read_text())
    m["status"] = "failed"
    path.write_text(json.dumps(m))
    for name in m["channels"]:
        with (recording / (name + ".sigmf-data")).open("ab") as stream:
            stream.write(b"unpublished tail")
    with pytest.raises(ValueError, match="Incomplete"):
        Archive(recording)
    archive = Archive(recording, allow_partial=True)
    assert len(list(archive.windows())) == m["windows"]


def test_active_manifest_change_is_detected(recording):
    archive = Archive(recording)
    path = recording / "manifest.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="changed"):
        list(archive.windows())


@pytest.mark.parametrize("mutation", ["channel_order", "timestamp", "sigmf", "unsafe_name"])
def test_bad_metadata_is_rejected(tmp_path, mutation):
    root = write_demo(tmp_path / "iq")
    if mutation == "channel_order":
        path = root / "receiver-config.json"
        c = json.loads(path.read_text())
        c["sites"][0]["channels"].reverse()
        path.write_text(json.dumps(c))
    elif mutation == "timestamp":
        path = root / "windows.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[1]["timestamp_ns"] += 1
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    elif mutation == "sigmf":
        path = root / "site0_e0.sigmf-meta"
        c = json.loads(path.read_text())
        c["global"]["core:sample_rate"] += 1
        path.write_text(json.dumps(c))
    else:
        path = root / "manifest.json"
        c = json.loads(path.read_text())
        c["channels"][0] = "../../outside"
        path.write_text(json.dumps(c))
    with pytest.raises(ValueError):
        Archive(root)


def test_archive_and_processor_have_no_simulator_or_algorithm_imports(tmp_path):
    root = write_demo(tmp_path / "iq")
    script = '''import importlib.abc,sys
class DenySource(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'airsim_rf','projectairsim','sionna','mitsuba','drjit','cupy','radar_skill','df_skill'}:
   raise RuntimeError('Forbidden source/algorithm dependency: '+fullname)
sys.meta_path.insert(0,DenySource())
from rf_skill_harness.archive import Archive
from rf_skill_harness.processor import process_window
a=Archive(sys.argv[1]);w=next(a.windows())
assert process_window(w.samples,{},a.context())['algorithm']=='insert-signal-processing-here'
'''
    result = subprocess.run([sys.executable, "-c", script, str(root)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
