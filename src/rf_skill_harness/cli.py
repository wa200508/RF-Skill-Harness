"""Inspect, prepare and replay receiver archives for GNU Radio skill development."""
import argparse
import json
from pathlib import Path
import sys
import signal

from .archive import Archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Show receiver metadata and optionally verify every sample hash")
    inspect.add_argument("recording", type=Path)
    inspect.add_argument("--verify", action="store_true")
    prepare = commands.add_parser("prepare", help="Create editable GNU Radio graphs and a processor.py hook")
    prepare.add_argument("recording", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    timeline = commands.add_parser("timeline", help="Build the RF acquisition schedule automatically from metadata")
    timeline.add_argument("recording", type=Path)
    timeline.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run", help="Replay through GNU Radio and your processing hook")
    run.add_argument("recording", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--processor", help="/path/processor.py:process_window or module:callable")
    run.add_argument("--speed", type=float, default=0., help="0 = unpaced; 1 = sample-clock real time; never resamples")
    run.add_argument("--start-window", type=int, default=0)
    run.add_argument("--stop-window", type=int)
    for command in (inspect, prepare, timeline, run):
        command.add_argument("--allow-partial", action="store_true", help="Use only the published prefix of a stable interrupted capture")
    demo = commands.add_parser("demo", help="Write a small synthetic protocol fixture, not a scene simulation")
    demo.add_argument("--output", type=Path, required=True)
    demo.add_argument("--kind", choices=("direction-finding", "radar"), default="direction-finding")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import write_demo
            result = {"recording": str(write_demo(args.output, kind=args.kind)), "synthetic_fixture": True}
        elif args.command == "inspect":
            result = Archive(args.recording, allow_partial=args.allow_partial).inspect(verify=args.verify)
        elif args.command == "prepare":
            from .grc import prepare_project
            result = prepare_project(args.recording, args.output, allow_partial=args.allow_partial)
        elif args.command == "timeline":
            from .paths import new_directory
            from .schedule import write_timeline
            root = new_directory(args.output)
            result = write_timeline(Archive(args.recording, allow_partial=args.allow_partial), root)
            result = {"kind": result["kind"], "timeline": str(root / "timeline.html"),
                      "schedule": str(root / "schedule.json")}
        else:
            def interrupted(signum, frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, interrupted)
            if hasattr(signal, "SIGHUP"):
                signal.signal(signal.SIGHUP, interrupted)
            try:
                from .runner import run as execute
            except ModuleNotFoundError as error:
                if error.name in ("gnuradio", "pmt"):
                    raise RuntimeError("GNU Radio 3.10 is required: use the container or its system Python (see README)") from error
                raise
            result = execute(args.recording, args.output, processor=args.processor, speed=args.speed,
                             start_window=args.start_window, stop_window=args.stop_window,
                             allow_partial=args.allow_partial)
        print(json.dumps(result, indent=2, allow_nan=False))
    except KeyboardInterrupt:
        print("Replay stopped by operator; completed window results retained", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
