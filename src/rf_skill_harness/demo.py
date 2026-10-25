"""Small synthetic protocol fixtures, not terrain/radar propagation simulations."""
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

from .paths import new_directory
from .archive import FORMAT


def write_demo(output, *, kind="direction-finding", windows=6):
    root = new_directory(output)
    if kind == "radar":
        rate, carrier, samples = 10_000_000, 77_000_000_000, 40_000
        channels = ["radar_rx0"]
        config = {"schema_version": 1, "sample_rate_hz": rate, "carrier_hz": carrier,
            "voltage_reference": "bias-subtracted ADC volts",
            "sites": [{"id": "radar", "channels": channels}],
            "acquisition": {"type": "fmcw-dechirped", "bandwidth_hz": 50_000_000,
                "chirp_duration_s": 32e-6, "pri_s": 40e-6, "num_samples": 256,
                "adc_start_s": 3e-6, "adc_gate": "zero-filled outside acquisition gate",
                "chirp_origin": "first window timestamp"}}
    elif kind == "direction-finding":
        rate, carrier, samples = 2_000_000, 915_000_000, 4096
        channels = [f"site{s}_e{i}" for s in range(2) for i in range(4)]
        spacing = 299792458. / carrier / 2
        config = {"schema_version": 1, "sample_rate_hz": rate, "carrier_hz": carrier,
            "voltage_reference": "input-referred complex volts", "sites": [
                {"id": f"site{s}", "channels": channels[s * 4:(s + 1) * 4],
                 "center_rf_m": [s * 25., 0., 8.], "yaw_rad": s * .2,
                 "element_offsets_m": [(i - 1.5) * spacing for i in range(4)],
                 "calibration_complex": [[1., 0.] for _ in range(4)]} for s in range(2)]}
    else:
        raise ValueError("Unknown demo kind")
    rng = np.random.default_rng(2201)
    origin = 123_456_789_000_000_007  # Deliberately not a whole second; test exact tags.
    captures = []
    handles = {c: (root / (c + ".sigmf-data")).open("wb") for c in channels}
    try:
        with (root / "windows.jsonl").open("w") as journal:
            for index in range(windows):
                t = index * samples + np.arange(samples)
                if kind == "radar":
                    chirp = t // 400
                    fast = t % 400
                    active = (fast >= 30) & (fast < 286)
                    signal = np.zeros((samples, 1), np.complex64)
                    signal[active, 0] = (.02 * np.exp(2j * np.pi * (24 * (fast[active] - 30) / 256
                                              + 5 * chirp[active] / 64))
                                        + .0002 * (rng.standard_normal(active.sum())
                                                   + 1j * rng.standard_normal(active.sum())))
                else:
                    phase = np.arange(len(channels))[None, :] * .37
                    signal = (.01 * np.exp(1j * (2 * np.pi * .03 * t[:, None] + phase))
                              + .0001 * (rng.standard_normal((samples, len(channels)))
                                         + 1j * rng.standard_normal((samples, len(channels)))))
                timestamp = origin + round(index * samples * 1e9 / rate)
                rows = []
                for column, name in enumerate(channels):
                    payload = np.asarray(signal[:, column], dtype="<c8").tobytes()
                    handles[name].write(payload)
                    rows.append({"receiver": name, "clipped_component_fraction": 0.,
                                 "sha256": sha256(payload).hexdigest()})
                journal.write(json.dumps({"timestamp_ns": timestamp, "sample_start": index * samples,
                                          "samples": samples, "channels": rows}) + "\n")
                captures.append({"core:sample_start": index * samples, "core:frequency": carrier})
    finally:
        for handle in handles.values():
            handle.close()
    manifest = {"format": FORMAT, "status": "complete", "channels": channels,
                "sample_rate_hz": rate, "carrier_hz": carrier, "units": "volts", "datatype": "cf32_le",
                "windows": windows, "samples_per_channel": windows * samples,
                "payload_bytes": windows * samples * len(channels) * 8}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (root / "receiver-config.json").write_text(json.dumps(config, indent=2) + "\n")
    for name in channels:
        meta = {"global": {"core:datatype": "cf32_le", "core:sample_rate": rate,
                            "core:version": "1.2.6", "core:description": "Synthetic harness test fixture, not RF scene validation"},
                "captures": captures, "annotations": []}
        (root / (name + ".sigmf-meta")).write_text(json.dumps(meta) + "\n")
    return root
