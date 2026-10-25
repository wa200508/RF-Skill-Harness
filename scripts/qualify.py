"""Installed-runtime smoke test: replay and RF schedule without simulation code."""
import importlib.abc
import json
from pathlib import Path
import sys
import tempfile


class DenySimulation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {"airsim_rf", "projectairsim", "sionna", "mitsuba", "drjit", "cupy", "df_skill", "radar_skill"}:
            raise RuntimeError("Receiver harness attempted a source/algorithm import: " + fullname)


sys.meta_path.insert(0, DenySimulation())
from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo
from rf_skill_harness.runner import run
from rf_skill_harness.schedule import describe_schedule

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    summary = []
    for kind in ("direction-finding", "radar"):
        capture = write_demo(root / kind, kind=kind)
        a = Archive(capture)
        assert a.inspect(verify=True)["sample_hashes_verified"]
        result = run(capture, root / (kind + "-results"))
        assert result["status"] == "complete" and result["windows"] == 6
        schedule = describe_schedule(a)
        assert bool(schedule.get("radar")) == (kind == "radar")
        summary.append({"input": kind, "channels": len(a.channels), "windows": result["windows"],
                        "acquisition_schedule": schedule["kind"], "gnuradio_version": result["gnuradio_version"]})
print(json.dumps({"status": "passed", "no_simulator_gpu_or_detector_dependency": True,
                  "recordings": summary}, indent=2))
