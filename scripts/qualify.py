"""Installed-runtime smoke test: replay and RF schedule without simulation code."""
import importlib.abc
import json
from pathlib import Path
import sys
import tempfile
import numpy as np


class DenySimulation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {"airsim_rf", "projectairsim", "sionna", "mitsuba", "drjit", "cupy", "df_skill", "radar_skill"}:
            raise RuntimeError("Receiver harness attempted a source/algorithm import: " + fullname)


sys.meta_path.insert(0, DenySimulation())
from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo
from rf_skill_harness.runner import run
from rf_skill_harness.schedule import describe_schedule
from rf_skill_harness.workbench import run_window
from rf_skill_harness.plotting import plot_iq, plot_results

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    summary = []
    for kind in ("direction-finding", "radar"):
        capture = write_demo(root / kind, kind=kind)
        a = Archive(capture)
        assert a.inspect(verify=True)["sample_hashes_verified"]
        result = run(capture, root / (kind + "-results"))
        assert result["status"] == "complete" and result["windows"] == 6
        window, metadata, measurements = run_window(a)
        first = json.loads((root / (kind + "-results") / "results.jsonl").read_text().splitlines()[0])
        assert {key: first[key] for key in metadata} == metadata
        assert first["measurements"]["algorithm"] == measurements["algorithm"]
        # Identical inputs need not produce bit-identical NumPy reductions in
        # different worker threads / SIMD alignments. Compare at their precision;
        # separate byte-hash tests enforce exact sample preservation.
        for field, precision in (("rms_volts", np.float64), ("peak_volts", np.float32)):
            assert first["measurements"][field].keys() == measurements[field].keys()
            np.testing.assert_allclose(list(first["measurements"][field].values()),
                list(measurements[field].values()), rtol=8 * np.finfo(precision).eps, atol=0)
        for name, figure in (("input", plot_iq(window, a.context())),
                             ("results", plot_results(root / (kind + "-results")))):
            figure.savefig(root / (kind + "-" + name + ".png"))
            plt.close(figure)
        schedule = describe_schedule(a)
        assert bool(schedule.get("radar")) == (kind == "radar")
        summary.append({"input": kind, "channels": len(a.channels), "windows": result["windows"],
                        "acquisition_schedule": schedule["kind"], "gnuradio_version": result["gnuradio_version"],
                        "notebook_hook_matches_gnuradio": True, "standard_plots": True})
print(json.dumps({"status": "passed", "no_simulator_gpu_or_detector_dependency": True,
                  "recordings": summary}, indent=2))
