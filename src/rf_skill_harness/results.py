"""Load a developer hook and journal receiver-only results; no GNU Radio import."""
from hashlib import sha256
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
from .paths import new_directory


def load_processor(spec=None):
    if not spec:
        from .processor import process_window
        return process_window
    module, separator, name = spec.rpartition(":")
    if not separator:
        module, name = spec, "process_window"
    path = Path(module).expanduser()
    if path.suffix == ".py" or path.is_file():
        identifier = "rf_harness_user_" + sha256(str(path.resolve()).encode()).hexdigest()[:16]
        description = importlib.util.spec_from_file_location(identifier, path)
        if description is None or description.loader is None:
            raise ValueError("Cannot load processor: " + module)
        imported = importlib.util.module_from_spec(description)
        # A developer workspace may include helper modules and stateful classes.
        # Register it as a normal module (also required by dataclasses).
        parent = str(path.resolve().parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        sys.modules[identifier] = imported
        # Iterative notebook edits can have identical size and filesystem mtime.
        # Read source every time instead of reusing timestamp-based .pyc files.
        exec(compile(path.read_bytes(), str(path), "exec"), imported.__dict__)
    else:
        imported = importlib.import_module(module)
    function = getattr(imported, name)
    if not callable(function):
        raise ValueError("Processor must be callable")
    return function


class RunState:
    def __init__(self):
        self.lock = threading.Lock()
        self.error = None
        self.windows = 0
        self.samples = 0
        self.source_done = False
        self.expected_windows = None

    def fail(self, error):
        with self.lock:
            if self.error is None:
                self.error = error


class ResultJournal:
    def __init__(self, output, context, *, processor=None):
        self.path = new_directory(output)
        (self.path / "context.json").write_text(json.dumps(context, indent=2, allow_nan=False) + "\n")
        self.file = (self.path / "results.jsonl").open("x")
        self.processor = processor or "rf_skill_harness.processor:process_window"
        self.windows = 0
        self.samples = 0
        self.status("running")

    def write(self, metadata, channels, measurements):
        if not isinstance(measurements, dict):
            raise ValueError("process_window must return a JSON-compatible dict")
        row = {"schema_version": 1, **metadata, "channels": channels, "measurements": measurements}
        encoded = json.dumps(row, allow_nan=False)
        self.file.write(encoded + "\n")
        self.file.flush()
        self.windows += 1
        self.samples += metadata["samples"]

    def status(self, status, **details):
        value = {"schema_version": 1, "status": status, "processor": self.processor,
                 "windows": self.windows, "samples_per_channel": self.samples, **details}
        temporary = self.path / "run.json.tmp"
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        temporary.replace(self.path / "run.json")

    def close(self, status, **details):
        self.file.close()
        self.status(status, **details)
