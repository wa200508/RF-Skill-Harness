import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("rootless", [False, True])
@pytest.mark.parametrize("mission_path", [False, True])
def test_launcher_preserves_host_paths_readonly_iq_and_correct_uid_mapping(tmp_path, rootless, mission_path):
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.jsonl"
    docker = bin_dir / "docker"
    docker.write_text("#!/usr/bin/env python3\nimport json,sys\n"
        f"with open({str(log)!r},'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"if sys.argv[1]=='info':print({json.dumps(['name=rootless'] if rootless else [])!r})\n")
    docker.chmod(0o755)
    selected = tmp_path / "selected-python"
    marker = tmp_path / "python-used"
    selected.write_text(f"#!/bin/sh\nprintf selected > '{marker}'\nexec '{sys.executable}' \"$@\"\n")
    selected.chmod(0o755)
    capture = tmp_path / "I Q capture"
    capture.mkdir()
    argument = capture
    if mission_path:
        capture = capture / "iq"
        capture.mkdir()
        (capture / "manifest.json").write_text('{}')
    processor = tmp_path / "my skill.py"
    processor.write_text("def process_window(iq, metadata, context):return {}\n")
    output = tmp_path / "results with spaces"
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "RF_HARNESS_PYTHON": str(selected)}
    result = subprocess.run(["bash", str(root / "scripts/run_harness.sh"), "run", str(argument),
                             "--processor", str(processor), "--output", str(output)], env=env,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "selected"
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0][:2] == ["pull", "ghcr.io/wa200508/airsim-rf:rf-skill-harness"]
    run = next(c for c in calls if c[0] == "run")
    assert run[run.index("--user") + 1] == ("0:0" if rootless else f"{os.getuid()}:{os.getgid()}")
    assert run[run.index("--network") + 1] == "none"
    assert "HOME=/tmp" in run and "XDG_CACHE_HOME=/tmp/.cache" in run
    assert f"type=bind,src={capture},dst=/recording,readonly" in run
    assert f"type=bind,src={output},dst=/output" in run
    assert f"/developer/{processor.name}:process_window" in run


@pytest.mark.parametrize("rootless", [False, True])
def test_lab_publishes_host_loopback_mounts_only_iq_and_cleans_up_on_sigterm(tmp_path, rootless):
    import signal
    import time
    root = Path(__file__).resolve().parents[1]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "docker.jsonl"
    docker = bin_dir / "docker"
    docker.write_text("#!/usr/bin/env python3\nimport json,sys,time\nfrom pathlib import Path\n"
        f"with open({str(log)!r},'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        f"if sys.argv[1]=='info':print({json.dumps(['name=rootless'] if rootless else [])!r})\n"
        "if sys.argv[1]=='run':\n"
        " Path(sys.argv[sys.argv.index('--cidfile')+1]).write_text('test-lab-container')\n"
        " time.sleep(1)\n")
    docker.chmod(0o755)
    capture = tmp_path / "mission"
    (capture / "iq").mkdir(parents=True)
    (capture / "iq/manifest.json").write_text('{}')
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "analysis.ipynb").write_text('{}')
    (project / "project.json").write_text('{}')
    env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
           "RF_HARNESS_PYTHON": sys.executable}
    process = subprocess.Popen(["bash", str(root / "scripts/run_harness.sh"), "lab", str(capture),
        "--workspace", str(project), "--port", "8899"], env=env, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if log.exists() and any('"run"' in row for row in log.read_text().splitlines()):
            time.sleep(.1)
            break
        time.sleep(.01)
    process.send_signal(signal.SIGTERM)
    _, stderr = process.communicate(timeout=10)
    assert process.returncode == 130, stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    run = next(c for c in calls if c[0] == "run")
    assert run[run.index("--user") + 1] == ("0:0" if rootless else f"{os.getuid()}:{os.getgid()}")
    assert run[run.index("--publish") + 1] == "127.0.0.1:8899:8888"
    assert f"type=bind,src={capture / 'iq'},dst=/recording,readonly" in run
    assert f"type=bind,src={project},dst=/workspace" in run
    assert "RF_HARNESS_RECORDING=/recording" in run
    assert "--ServerApp.port_retries=0" in run
    assert "--IdentityProvider.token=''" not in run
    assert ["stop", "--time", "5", "test-lab-container"] in calls
    assert ["rm", "--force", "test-lab-container"] in calls
