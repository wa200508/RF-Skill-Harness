import json

import pytest

from rf_skill_harness.archive import Archive
from rf_skill_harness.demo import write_demo
from rf_skill_harness.grc import prepare_project
from rf_skill_harness.schedule import describe_schedule, events


def test_radar_tx_rx_timing_is_derived_from_recorded_chirp_and_adc_metadata(tmp_path):
    root = write_demo(tmp_path / "iq", kind="radar")
    schedule = describe_schedule(Archive(root))
    assert schedule["kind"] == "fmcw-radar"
    assert schedule["origin_timestamp_ns"] == "123456789000000007"
    selected = events(schedule, 0, 80_000)
    tx = [e for e in selected if e["lane"] == "tx"]
    rx = [e for e in selected if e["lane"] == "rx"]
    assert [(e["start_ns"], e["stop_ns"]) for e in tx] == [(0, 32_000), (40_000, 72_000)]
    assert [(e["start_ns"], e["stop_ns"]) for e in rx] == [(3_000, 28_600), (43_000, 68_600)]
    assert all(e["evidence"] == "configured" for e in tx + rx)
    assert selected[0]["evidence"] == "recorded" and selected[0]["window"] == 0
    assert schedule["window_runs"] == [{"first_window": 0, "count": 6, "samples": 40000,
                                         "start_ns": 0, "duration_ns": 4_000_000}]


def test_receive_only_schedule_never_invents_transmitter_events(tmp_path):
    root = write_demo(tmp_path / "iq")
    schedule = describe_schedule(Archive(root))
    selected = events(schedule)
    assert [e["lane"] for e in selected] == ["collection"] * 6
    assert [e["window"] for e in selected] == list(range(6))
    assert "radar" not in schedule
    assert schedule["total_ns"] == 6 * 2_048_000


def test_missing_radar_phase_is_reported_without_fabricating_a_chirp_schedule(tmp_path):
    root = write_demo(tmp_path / "iq", kind="radar")
    path = root / "receiver-config.json"
    c = json.loads(path.read_text())
    c["acquisition"].pop("chirp_origin")
    path.write_text(json.dumps(c))
    schedule = describe_schedule(Archive(root))
    assert schedule["kind"] == "fmcw-radar" and "radar" not in schedule
    assert len(schedule["lanes"]) == 1
    assert "missing" in schedule["notes"][0]


def test_prepare_automatically_includes_a_self_contained_timeline_and_full_context(tmp_path):
    root = write_demo(tmp_path / "iq", kind="radar")
    project = tmp_path / "workspace"
    prepare_project(root, project)
    html = (project / "timeline.html").read_text()
    assert "__RF_SCHEDULE_JSON__" not in html and "123456789000000007" in html
    assert '<script src=' not in html and '<link ' not in html
    assert "configured" in html and "buffer scheduling" in html
    assert json.loads((project / "context.json").read_text())["receiver_config"] == Archive(root).config


def test_long_periodic_schedules_are_compact_and_viewport_event_expansion_is_bounded(tmp_path):
    schedule = describe_schedule(Archive(write_demo(tmp_path / "iq", kind="radar")))
    schedule["window_runs"][0]["count"] = 150_000
    schedule["total_ns"] = 600_000_000_000
    assert len(json.dumps(schedule)) < 5000
    with pytest.raises(ValueError, match="Too many events"):
        events(schedule)
    close = events(schedule, 500_000_000_000, 500_000_080_000)
    assert len(close) < 10
