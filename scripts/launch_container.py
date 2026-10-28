#!/usr/bin/env python3
"""Pull and run the CPU GNU Radio harness with host-owned outputs, also rootless."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import tempfile
from hashlib import sha256

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rf_skill_harness.catalog import discover_recordings


def command(arguments):
    subprocess.run(arguments, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=os.environ.get("RF_HARNESS_IMAGE", "ghcr.io/wa200508/airsim-rf:rf-skill-harness"))
    parser.add_argument("--no-pull", action="store_true", help="Use an explicitly selected local development image")
    subs = parser.add_subparsers(dest="action", required=True)
    init = subs.add_parser('init', help='Create a skill project without selecting I/Q')
    init.add_argument('--output', type=Path, required=True)
    init.add_argument('--name', default='my-rf-skill')
    export = subs.add_parser('export', help='Export the developed project as a native AMS-GRA skill')
    export.add_argument('workspace', type=Path)
    export.add_argument('--output', type=Path, required=True)
    for name in ("inspect", "prepare", "timeline", "run"):
        sub = subs.add_parser(name)
        sub.add_argument("recording", type=Path)
        sub.add_argument("--allow-partial", action="store_true")
        if name == "inspect":
            sub.add_argument("--verify", action="store_true")
        else:
            sub.add_argument("--output", type=Path, required=True)
        if name == "run":
            sub.add_argument("--processor", help="A local processor.py[:function] file")
            sub.add_argument("--speed", type=float, default=0.)
            sub.add_argument("--start-window", type=int, default=0)
            sub.add_argument("--stop-window", type=int)
    demo = subs.add_parser("demo")
    demo.add_argument("--output", type=Path, required=True)
    demo.add_argument("--kind", choices=("direction-finding", "radar"), default="direction-finding")
    lab = subs.add_parser("lab", help="Open an editable JupyterLab workspace on the host's loopback interface")
    lab.add_argument('recording', type=Path, nargs='?', help='Legacy fixed-capture workspace')
    lab.add_argument('--recordings', type=Path, action='append', default=[], help='Optional capture or capture library; repeat for multiple locations')
    lab.add_argument('--name', default='my-rf-skill')
    lab.add_argument("--workspace", type=Path, required=True, help="Persistent skill project; created automatically if empty")
    lab.add_argument("--port", type=int, default=8888)
    lab.add_argument("--allow-partial", action="store_true")
    plot = subs.add_parser("plot", help="Plot saved scalar results without rerunning DSP")
    plot.add_argument("results", type=Path)
    plot.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "lab" and not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535 (also for rootless Docker)")
    if not args.no_pull:
        command(["docker", "pull", args.image])
    command(["docker", "image", "inspect", "--format", "{{.Id}}", args.image])
    security = subprocess.run(["docker", "info", "--format", "{{json .SecurityOptions}}"],
                              check=True, capture_output=True, text=True)
    rootless = "rootless" in security.stdout.lower()
    uid = "0:0" if rootless else f"{os.getuid()}:{os.getgid()}"
    options = ["docker", "run", "--rm", "--init", "--network", "bridge" if args.action == "lab" else "none", "--user", uid,
               "-e", "HOME=/tmp", "-e", "XDG_CACHE_HOME=/tmp/.cache"]
    arguments = [args.action]
    recording = None
    if getattr(args, 'recording', None) is not None:
        recording = args.recording.expanduser().resolve()
        if not recording.is_dir():
            parser.error("Recording directory does not exist")
        if (recording / "iq/manifest.json").is_file():
            manifest = json.loads((recording / "manifest.json").read_text()) if (recording / "manifest.json").is_file() else {}
            if manifest.get("format") != "airsim-rf-sample-recording-v1":
                recording = recording / "iq"
        # A mission may also contain evaluation truth and camera/results files.
        # Only the receiver's IQ/metadata directory enters the skill container.
        options += ["--mount", f"type=bind,src={recording},dst=/recording,readonly"]
        arguments += ["/recording"]
        if args.allow_partial:
            arguments += ["--allow-partial"]
    if args.action == "lab":
        workspace = args.workspace.expanduser().resolve()
        legacy = recording is not None and (workspace / 'analysis.ipynb').is_file() and not (workspace / 'skill.json').is_file()
        if workspace.exists() and any(workspace.iterdir()) and not legacy and not (workspace / 'skill.json').is_file():
            parser.error('--workspace must be an RF skill project or a new/empty directory')
        workspace.mkdir(parents=True, exist_ok=True)
        options += ["--mount", f"type=bind,src={workspace},dst=/workspace",
                    "--workdir", "/workspace", "--publish", f"127.0.0.1:{args.port}:8888",
                    "-e", f"RF_HARNESS_ALLOW_PARTIAL={int(args.allow_partial)}", "--entrypoint", "python3"]
        if recording is not None:
            options += ['-e', 'RF_HARNESS_RECORDING=/recording']
        catalog = {}
        for label, path in discover_recordings(args.recordings).items():
            mount = '/recordings/' + sha256(str(path).encode()).hexdigest()[:16]
            options += ['--mount', f'type=bind,src={path},dst={mount},readonly']
            catalog[label] = mount
        if recording is not None:
            catalog.setdefault(recording.parent.name if recording.name == 'iq' else recording.name, '/recording')
        options += ['-e', 'RF_HARNESS_CAPTURE_CATALOG=' + json.dumps(catalog)]
        print(f"Open http://127.0.0.1:{args.port}/lab and use the token printed below. Ctrl+C stops this lab container.", flush=True)
        # The host-side recording path in project.json is unchanged; notebook
        # kernels use the read-only mount via this process-local environment.
        # An explicit container ID also lets SIGTERM clean up a closed terminal.
        def interrupted(signum, frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            with tempfile.TemporaryDirectory(prefix="rf-harness-lab-") as temporary:
                cidfile = Path(temporary) / "container.id"
                try:
                    entry = (['-m', 'jupyterlab', '--no-browser', '--ip=0.0.0.0', '--port=8888',
                              '--ServerApp.port_retries=0', '--ServerApp.root_dir=/workspace',
                              '--ServerApp.allow_root=True', '--ServerApp.use_redirect_file=False'] if legacy else
                             ['-m', 'rf_skill_harness', 'workbench', '--workspace', '/workspace', '--name', args.name])
                    command(options + ["--cidfile", str(cidfile), args.image] + entry)
                finally:
                    if cidfile.is_file() and cidfile.read_text().strip():
                        container = cidfile.read_text().strip()
                        subprocess.run(["docker", "stop", "--time", "5", container],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run(["docker", "rm", "--force", container],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            signal.signal(signal.SIGTERM, previous)
        return
    if args.action == 'export':
        workspace = args.workspace.expanduser().resolve()
        if not (workspace / 'skill.json').is_file():
            parser.error('Export requires a skill project containing skill.json')
        options += ['--mount', f'type=bind,src={workspace},dst=/developer,readonly']
        arguments += ['/developer']
    if args.action == "plot":
        results = args.results.expanduser().resolve()
        if not (results / "results.jsonl").is_file():
            parser.error("Results directory must contain results.jsonl")
        options += ["--mount", f"type=bind,src={results},dst=/results,readonly"]
        arguments += ["/results"]
    output = None
    if hasattr(args, "output"):
        output = args.output.expanduser().resolve()
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            parser.error("Choose a new or empty output directory")
        output.mkdir(parents=True, exist_ok=True)
        options += ["--mount", f"type=bind,src={output},dst=/output"]
        arguments += ["--output", "/output"]
    if args.action == "demo":
        arguments += ["--kind", args.kind]
    if args.action == 'init':
        arguments += ['--name', args.name]
    if args.action == "inspect" and args.verify:
        arguments += ["--verify"]
    if args.action == "run":
        arguments += ["--speed", str(args.speed), "--start-window", str(args.start_window)]
        if args.stop_window is not None:
            arguments += ["--stop-window", str(args.stop_window)]
        if args.processor:
            module, separator, function = args.processor.rpartition(":")
            if not separator:
                module, function = args.processor, "process_window"
            path = Path(module).expanduser().resolve()
            if not path.is_file():
                parser.error("--processor must name a local Python file")
            options += ["--mount", f"type=bind,src={path.parent},dst=/developer,readonly"]
            arguments += ["--processor", f"/developer/{path.name}:{function}"]
    command(options + [args.image] + arguments)
    if args.action == "prepare":
        # Container paths must not leak into a project opened on the host.
        actual = recording / "iq" if (recording / "iq/manifest.json").is_file() else recording
        for filename in ("harness.grc", "viewer.grc"):
            path = output / filename
            graph = json.loads(path.read_text())
            entry = next(b for b in graph["blocks"] if b["name"] == "recording_dir")
            entry["parameters"]["value"] = os.path.relpath(actual, output)
            path.write_text(json.dumps(graph, indent=2) + "\n")
        path = output / "project.json"
        project = json.loads(path.read_text())
        project["recording_dir"] = os.path.relpath(actual, output)
        path.write_text(json.dumps(project, indent=2) + "\n")
    if output:
        print("Host output: " + str(output))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode)
    except KeyboardInterrupt:
        raise SystemExit(130)
