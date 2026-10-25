"""Read the coherent cf32/SigMF receiver archive without importing GNU Radio.

The journal is the acquisition clock. Neither file offsets nor playback wall
time replace it. Hash verification happens before a window reaches processing.
"""
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
import json
import math
from pathlib import Path
import re

import numpy as np

FORMAT = "airsim-rf-sample-recording-v1"
CHANNEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def load_json(path):
    return json.loads(Path(path).read_text())


def integer(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def positive(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


@dataclass(frozen=True)
class Window:
    index: int
    timestamp_ns: int
    sample_start: int
    samples: np.ndarray  # [sample, channel], complex64 volts
    clipped_component_fraction: dict


class Archive:
    def __init__(self, directory, *, allow_partial=False):
        root = Path(directory).expanduser().resolve()
        if not (root / "manifest.json").is_file() and (root / "iq/manifest.json").is_file():
            root = root / "iq"
        # A mission manifest is also named manifest.json; choose its iq child.
        if (root / "iq/manifest.json").is_file():
            candidate = load_json(root / "manifest.json") if (root / "manifest.json").is_file() else {}
            if candidate.get("format") != FORMAT:
                root = root / "iq"
        self.path = root
        self._manifest_bytes = (root / "manifest.json").read_bytes()
        self.manifest = json.loads(self._manifest_bytes)
        m = self.manifest
        if m.get("format") != FORMAT or m.get("datatype") != "cf32_le" or m.get("units") != "volts":
            raise ValueError("Expected a coherent cf32_le voltage archive (the mission's iq/ directory)")
        if m.get("status") not in ("complete", "stopped") and not allow_partial:
            raise ValueError("Incomplete archive: use --allow-partial only with a stopped, stable snapshot")
        for field in ("windows", "samples_per_channel", "payload_bytes"):
            if not integer(m.get(field), 1):
                raise ValueError("No published data or invalid manifest field: " + field)
        self.channels = m.get("channels")
        if (not isinstance(self.channels, list) or not self.channels
                or any(not isinstance(c, str) or not CHANNEL.fullmatch(c) for c in self.channels)
                or len(set(self.channels)) != len(self.channels)):
            raise ValueError("Unique safe channel IDs required")
        self.rate = m.get("sample_rate_hz")
        self.carrier = m.get("carrier_hz")
        if not positive(self.rate) or not positive(self.carrier):
            raise ValueError("Positive finite sample rate and carrier required")
        if m["payload_bytes"] != m["samples_per_channel"] * 8 * len(self.channels):
            raise ValueError("Manifest byte total differs from sample total")
        self.config = load_json(root / "receiver-config.json")
        configured = [c for site in self.config["sites"] for c in site["channels"]]
        if configured != self.channels:
            raise ValueError("Receiver configuration and manifest channel order differ")
        if (self.config["sample_rate_hz"], self.config["carrier_hz"]) != (self.rate, self.carrier):
            raise ValueError("Receiver configuration and manifest rate/carrier differ")
        self.allow_partial = allow_partial
        for channel in self.channels:
            size = (root / (channel + ".sigmf-data")).stat().st_size
            expected = m["samples_per_channel"] * 8
            if size < expected or (not allow_partial and size != expected):
                raise ValueError("Data size mismatch: " + channel)
            meta = load_json(root / (channel + ".sigmf-meta"))
            if (meta["global"].get("core:datatype"), meta["global"].get("core:sample_rate")) != ("cf32_le", self.rate):
                raise ValueError("SigMF datatype/rate mismatch: " + channel)
            for capture in meta.get("captures", []):
                if capture.get("core:frequency", self.carrier) != self.carrier:
                    raise ValueError("SigMF carrier mismatch: " + channel)
        self.first_timestamp_ns = None
        # Validate journal metadata eagerly; bulk sample bytes remain lazy.
        for row in self.rows():
            if self.first_timestamp_ns is None:
                self.first_timestamp_ns = row["timestamp_ns"]

    def unchanged(self):
        if (self.path / "manifest.json").read_bytes() != self._manifest_bytes:
            raise ValueError("Capture changed during replay; use a stopped, stable snapshot")

    def rows(self):
        m = self.manifest
        offset = 0
        next_time = None
        with (self.path / "windows.jsonl").open() as journal:
            for index in range(m["windows"]):
                line = journal.readline()
                if not line:
                    raise ValueError("Truncated published window journal")
                row = json.loads(line)
                n = row.get("samples")
                timestamp = row.get("timestamp_ns")
                if not integer(n, 1) or n > 2_000_000 or not integer(timestamp):
                    raise ValueError("Invalid sample count or acquisition timestamp")
                if not integer(row.get("sample_start")) or row["sample_start"] != offset:
                    raise ValueError("Discontinuous sample offsets")
                if next_time is not None and timestamp != next_time:
                    raise ValueError("Discontinuous acquisition timestamps")
                duration = n * 1e9 / self.rate
                if abs(duration - round(duration)) > 1e-4:
                    raise ValueError("Window does not align to integer acquisition nanoseconds")
                channels = row.get("channels", [])
                if [c.get("receiver") for c in channels] != self.channels:
                    raise ValueError("Journal channel order differs from receiver configuration")
                for channel in channels:
                    digest = channel.get("sha256")
                    clipped = channel.get("clipped_component_fraction")
                    if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                        raise ValueError("Invalid per-window sample hash")
                    if (isinstance(clipped, bool) or not isinstance(clipped, (int, float))
                            or not math.isfinite(clipped) or not 0 <= clipped <= 1):
                        raise ValueError("Invalid clipping metadata")
                offset += n
                next_time = timestamp + round(duration)
                yield row
        if offset != m["samples_per_channel"]:
            raise ValueError("Journal sample total differs from manifest")
        self.unchanged()

    def windows(self, *, start_window=0, stop_window=None):
        stop = self.manifest["windows"] if stop_window is None else stop_window
        if not integer(start_window) or not integer(stop) or not 0 <= start_window < stop <= self.manifest["windows"]:
            raise ValueError("Invalid window selection")
        with ExitStack() as stack:
            streams = {name: stack.enter_context((self.path / (name + ".sigmf-data")).open("rb"))
                       for name in self.channels}
            for index, row in enumerate(self.rows()):
                if not start_window <= index < stop:
                    continue
                data = np.empty((row["samples"], len(self.channels)), dtype=np.complex64)
                for column, channel in enumerate(row["channels"]):
                    stream = streams[channel["receiver"]]
                    stream.seek(row["sample_start"] * 8)
                    payload = stream.read(row["samples"] * 8)
                    if len(payload) != row["samples"] * 8 or sha256(payload).hexdigest() != channel["sha256"]:
                        raise ValueError("Sample SHA-256 mismatch: " + channel["receiver"])
                    data[:, column] = np.frombuffer(payload, dtype="<c8")
                if not np.isfinite(data).all():
                    raise ValueError("Nonfinite recorded samples")
                data.flags.writeable = False
                yield Window(index, row["timestamp_ns"], row["sample_start"], data,
                             {c["receiver"]: c["clipped_component_fraction"] for c in row["channels"]})

    def context(self):
        return {"schema_version": 1, "archive_format": FORMAT,
                "channels": list(self.channels), "sample_rate_hz": self.rate,
                "carrier_hz": self.carrier, "units": "volts",
                "voltage_reference": self.config.get("voltage_reference", "input-referred complex volts"),
                "first_timestamp_ns": self.first_timestamp_ns,
                "timestamp_clock": "original acquisition clock; epoch is not assumed to be UTC",
                "receiver_config": self.config, "manifest": self.manifest}

    def inspect(self, *, verify=False):
        if verify:
            for _ in self.windows():
                pass
        return {**self.context(), "signal_seconds": self.manifest["samples_per_channel"] / self.rate,
                "sample_hashes_verified": verify}
