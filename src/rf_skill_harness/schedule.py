"""Derive an RF acquisition timeline from receiver metadata, never scene truth.

This describes acquisition events, not GNU Radio's buffer/work scheduler. TX
chirps and ADC gates are configured timing, not evidence of actual RF TX state.
"""
import json
import math
from pathlib import Path


def nanoseconds(value, label, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid acquisition timing: " + label)
    nanos = value * 1e9
    if abs(nanos - round(nanos)) > 1e-4 or nanos < 0 or (not allow_zero and nanos == 0):
        raise ValueError("Acquisition timing must align to nonnegative integer nanoseconds: " + label)
    return round(nanos)


def describe_schedule(archive):
    # Run-length encoding keeps long constant-window captures compact. Ten
    # minutes of 25 kHz radar chirps does not expand to 30 million JSON events.
    runs = []
    first = archive.first_timestamp_ns
    for index, row in enumerate(archive.rows()):
        duration = round(row["samples"] * 1e9 / archive.rate)
        if runs and runs[-1]["samples"] == row["samples"]:
            runs[-1]["count"] += 1
        else:
            runs.append({"first_window": index, "count": 1, "samples": row["samples"],
                         "start_ns": row["timestamp_ns"] - first, "duration_ns": duration})
    result = {"schema_version": 1, "kind": "receiver-collection", "origin_timestamp_ns": str(first),
              "total_ns": runs[-1]["start_ns"] + runs[-1]["count"] * runs[-1]["duration_ns"],
              "channels": archive.channels, "window_runs": runs,
              "lanes": [{"id": "collection", "label": "Recorded receive / collection windows",
                         "evidence": "recorded", "color": "#44bda3"}],
              "notes": ["Acquisition time is independent of playback speed and GNU Radio buffer scheduling.",
                        "No transmitter or emitter activity is inferred for receive-only recordings."]}
    acquisition = archive.config.get("acquisition", {})
    if acquisition.get("type") == "fmcw-dechirped":
        result["kind"] = "fmcw-radar"
        required = ("pri_s", "chirp_duration_s", "adc_start_s", "num_samples")
        if any(key not in acquisition for key in required) or acquisition.get("chirp_origin") != "first window timestamp":
            result["notes"] = ["Radar TX/RX event timing or chirp origin is missing. Only recorded receive windows can be placed accurately.",
                               "No missing timing or TX phase is invented."]
            return result
        pri = nanoseconds(acquisition["pri_s"], "PRI")
        tx = nanoseconds(acquisition["chirp_duration_s"], "chirp duration")
        adc_start = nanoseconds(acquisition["adc_start_s"], "ADC start", allow_zero=True)
        samples = acquisition["num_samples"]
        if not isinstance(samples, int) or isinstance(samples, bool) or samples <= 0:
            raise ValueError("Positive ADC gate sample count required")
        adc_duration = nanoseconds(samples / archive.rate, "ADC gate duration")
        if tx > pri or adc_start + adc_duration > pri:
            raise ValueError("Configured TX/RX events exceed the chirp PRI")
        result.update(kind="fmcw-radar", radar={"pri_ns": pri, "tx_duration_ns": tx,
            "rx_start_ns": adc_start, "rx_duration_ns": adc_duration}, acquisition=acquisition)
        result["lanes"] += [
            {"id": "tx", "label": "TX chirp · configured", "evidence": "configured", "color": "#f2b65d"},
            {"id": "rx", "label": "RX / ADC gate · configured", "evidence": "configured", "color": "#6caaff"}]
        result["notes"] = [
            "TX chirps and RX gates are derived from configured timing; the archive does not log per-window TX on/off commands.",
            "Recorded receive windows locate the archived samples. Zeros outside ADC gates are timing placeholders.",
            "TX/RX overlap is expected for FMCW. Zoom in to inspect individual chirps.",
            "Acquisition time is independent of playback speed and GNU Radio buffer scheduling."]
    return result


def events(schedule, start_ns=0, stop_ns=None, *, max_events=5000):
    """Return exact visible intervals, refusing unbounded expansion of long runs."""
    stop = schedule["total_ns"] if stop_ns is None else stop_ns
    start = max(0, start_ns)
    stop = min(schedule["total_ns"], stop)
    if start >= stop:
        return []
    values = []
    for run in schedule["window_runs"]:
        first = max(0, (start - run["start_ns"]) // run["duration_ns"])
        last = min(run["count"], math.ceil((stop - run["start_ns"]) / run["duration_ns"]))
        if last - first + len(values) > max_events:
            raise ValueError("Too many events; select a shorter interval")
        for index in range(first, last):
            begin = run["start_ns"] + index * run["duration_ns"]
            values.append({"lane": "collection", "start_ns": begin, "stop_ns": begin + run["duration_ns"],
                           "window": run["first_window"] + index, "evidence": "recorded"})
    if schedule.get("radar"):
        radar = schedule["radar"]
        first = max(0, start // radar["pri_ns"])
        last = math.ceil(stop / radar["pri_ns"])
        if 2 * (last - first) + len(values) > max_events:
            raise ValueError("Too many events; select a shorter interval")
        for chirp in range(first, last):
            for lane, offset, duration in (("tx", 0, radar["tx_duration_ns"]),
                                          ("rx", radar["rx_start_ns"], radar["rx_duration_ns"])):
                begin = chirp * radar["pri_ns"] + offset
                end = min(schedule["total_ns"], begin + duration)
                if begin < stop and end > start:
                    values.append({"lane": lane, "start_ns": begin, "stop_ns": end,
                                   "chirp": chirp, "evidence": "configured"})
    return values


def write_timeline(archive, directory):
    root = Path(directory)
    schedule = describe_schedule(archive)
    (root / "schedule.json").write_text(json.dumps(schedule, indent=2) + "\n")
    payload = json.dumps(schedule, allow_nan=False).replace("<", "\\u003c")
    template = Path(__file__).with_name("timeline.html").read_text()
    (root / "timeline.html").write_text(template.replace("__RF_SCHEDULE_JSON__", payload))
    return schedule
