"""Codex rollout invariants, with hand-derived disjoint token totals.

The three on-disk slices retain the shape inspected in a local CLI rollout;
models, timestamps, ordinals, counters and meter values are replaced. They carry
no session identity, cwd, prompt, message content or other private metadata.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SKILL_ROOT = Path(os.environ.get("SKILL_ROOT", Path(__file__).resolve().parents[1]))
SCRIPTS = SKILL_ROOT / "scripts"
DATA = SKILL_ROOT / "fixtures" / "data"
sys.path.insert(0, str(SCRIPTS))
import runtime_adapters as adapters

PYTHON = os.environ.get("SKILL_CLEAN_PYTHON", sys.executable)


def run(root: Path, *args: str):
    return subprocess.run(
        [PYTHON, str(SCRIPTS / "usage_cost.py"), "--runtime", "codex",
         "--data-root", str(root), "--tz", "UTC", *args],
        capture_output=True, text=True, check=False,
    )


def report(case: str, *args: str):
    proc = run(DATA / "codex" / case, "--format", "json", *args)
    assert proc.returncode == 3, proc.stderr  # no OpenAI rate rows are shipped
    return json.loads(proc.stdout)


def test_repeated_per_turn_records_do_not_add_usage():
    payload = report("repeated")
    # Final 1500 input contains 350 read + 150 write. Output 300 includes
    # reasoning 180. Disjoint total = 1000 + 350 + 150 + 300 = 1800.
    assert payload["models"][0]["tokens"] == {
        "input": 1000, "cache_read": 350, "cache_write_5m": 150,
        "cache_write_1h": 0, "output": 300, "total": 1800,
    }
    assert payload["totals"]["requests"] == 2
    assert payload["collection"]["duplicate_records"] == 1
    assert payload["collection"]["malformed_lines"] == 2
    assert payload["complete"] is False
    assert payload["models"][0]["cost"] is None
    assert payload["totals"]["priced_tokens"] == 0
    table = run(DATA / "codex" / "repeated")
    assert "UNPRICED" in table.stdout
    assert "Ignored 2 unparseable line(s)" in table.stdout


def test_mid_session_counter_decrease_starts_a_new_segment():
    payload = report("reset")
    # Segment 1 ends at 1400 input + 260 output = 1660; segment 2 ends
    # at 300 input + 90 output = 390. Sum=2050, not max(1660,390).
    # Reads=300+70=370, writes=120+20=140, uncached=1700-370-140=1190.
    assert payload["models"][0]["tokens"] == {
        "input": 1190, "cache_read": 370, "cache_write_5m": 140,
        "cache_write_1h": 0, "output": 350, "total": 2050,
    }
    assert payload["collection"]["counter_resets"] == 1
    assert payload["totals"]["requests"] == 4
    assert "rate_limits" not in payload


def test_two_models_follow_the_latest_turn_context():
    payload = report("models")
    by_model = {m["model_id"]: m for m in payload["models"]}
    # A's first snapshot: 100+20=120. B's increment: (300-100)+(70-20)=250.
    assert by_model["gpt-5.2-codex"]["tokens"]["total"] == 120
    assert by_model["gpt-5.1-codex"]["tokens"]["total"] == 250
    assert by_model["gpt-5.1-codex"]["tokens"]["input"] == 140
    assert payload["totals"]["requests"] == 2
    assert payload["collection"]["duplicate_records"] == 1


def test_window_uses_the_prior_cumulative_baseline():
    payload = report("repeated", "--since", "2026-08-11", "--until", "2026-08-11")
    assert payload["totals"]["tokens"] == 600
    assert payload["totals"]["requests"] == 1
    assert payload["collection"]["filtered_out"] == 1
    assert payload["rate_limits"]["timestamp"] == "2026-08-11T00:02:00Z"
    prior = report("repeated", "--since", "2026-08-10", "--until", "2026-08-10")
    # The duplicate has a newer meter even though it adds no usage.
    assert prior["totals"]["tokens"] == 1200
    assert prior["rate_limits"]["primary"]["used_percent"] == 60
    assert prior["rate_limits"]["timestamp"] == "2026-08-10T23:59:00Z"


def test_meter_is_a_timestamped_server_value_and_uses_report_timezone():
    payload = report("repeated", "--tz", "America/Los_Angeles")
    meter = payload["rate_limits"]
    assert meter["source"] == "server_snapshot"
    assert meter["primary"]["used_percent"] == 62.5
    assert meter["primary"]["window_minutes"] == 10080
    assert meter["primary"]["resets_at_local"] == "2026-08-11T17:00:00-07:00"
    text = run(DATA / "codex" / "repeated", "--tz", "America/Los_Angeles").stdout
    assert "server snapshot at 2026-08-11T00:02:00Z" in text
    assert "Used: 62.5%" in text
    assert "10,080 minutes (weekly)" in text
    assert "not derived from the local token total" in text
    assert "rate limit" not in run(DATA / "codex" / "models").stdout
    empty = run(DATA / "codex" / "repeated", "--since", "2026-09-01", "--format", "json")
    assert empty.returncode == 0
    assert "rate_limits" not in json.loads(empty.stdout)


def _entries():
    path = DATA / "codex" / "models" / "rollout-models.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def _write(root, entries, name="rollout-test.jsonl"):
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_meter_only_records_and_latest_across_files(tmp_path):
    def meter(stamp, used):
        return {"type": "event_msg", "timestamp": stamp, "payload": {
            "type": "token_count", "info": None,
            "rate_limits": {"primary": {"used_percent": used, "window_minutes": 10080,
                                        "resets_at": 1786492800}}}}
    _write(tmp_path, [meter("2026-08-11T00:04:00Z", 73)], "rollout-a.jsonl")
    _write(tmp_path, [meter("2026-08-11T00:01:00Z", 19)], "rollout-z.jsonl")
    proc = run(tmp_path, "--format", "json")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["totals"]["tokens"] == 0
    assert payload["rate_limits"]["primary"]["used_percent"] == 73


@pytest.mark.parametrize("root_kind", ["missing", "empty", "wrong_name"])
def test_unavailable_source_exits_four_with_reason(tmp_path, root_kind):
    root = tmp_path / "sessions"
    if root_kind != "missing":
        root.mkdir()
    if root_kind == "wrong_name":
        (root / "other.jsonl").write_text("{}\n")
    proc = run(root)
    assert proc.returncode == 4
    assert not proc.stdout
    assert "unavailable for runtime 'codex'" in proc.stderr
    assert "--data-root" in proc.stderr


def test_codex_home_and_data_root_override(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write(tmp_path / "sessions", _entries())
    adapter = adapters.get_adapter("codex")
    assert adapter.availability(None).supported
    assert adapter.resolve_data_root(None) == tmp_path / "sessions"
    assert adapter.resolve_data_root(tmp_path / "override") == tmp_path / "override"


@pytest.mark.parametrize("value", [-1, 1.5, True, "100", None, []])
def test_malformed_counters_are_counted_not_coerced(tmp_path, value):
    entries = _entries()[:2]
    entries[1]["payload"]["info"]["total_token_usage"]["input_tokens"] = value
    _write(tmp_path, entries)
    proc = run(tmp_path, "--format", "json")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["collection"]["invalid_records"] == 1
    assert payload["totals"]["tokens"] == 0


@pytest.mark.parametrize("field", ["cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens"])
def test_impossible_subset_counts_are_rejected(tmp_path, field):
    entries = _entries()[:2]
    entries[1]["payload"]["info"]["total_token_usage"][field] = 1000
    _write(tmp_path, entries)
    payload = json.loads(run(tmp_path, "--format", "json").stdout)
    assert payload["collection"]["invalid_records"] == 1
    assert payload["totals"]["tokens"] == 0


def test_missing_turn_model_is_unpriced_not_dropped(tmp_path):
    _write(tmp_path, _entries()[1:2])
    payload = json.loads(run(tmp_path, "--format", "json").stdout)
    assert payload["unpriced_models"] == ["unknown-codex-model"]
    assert payload["totals"]["tokens"] == 120


@pytest.mark.parametrize("dataset,fmt,expected,exit_code", [
    ("priced", "table", "6b949e9da79bd26eae65f1e7f03c8cd40bc18035bd3a0eb7125dc744b47ebf6d", 0),
    ("priced", "json", "2317b68c5534fd34dbf9524823cc882f726f7f501f193d510dec1255e85b9fe5", 0),
    ("unpriced", "table", "720ef0ef0bd44b44dcc67e56e04f650cf8d4b99ba43837f8162a8db936ab9169", 3),
    ("unpriced", "json", "18bca1d93e272e7d86bc0e7711e0ee26b62c12c462d99dae65df69ccefe74c78", 3),
])
def test_claude_code_fixture_output_is_byte_identical(dataset, fmt, expected, exit_code):
    # SHA-256 of the existing fixture outputs, captured before adding Codex.
    # The explicit window keeps this compatibility check independent of today.
    #
    # The two `priced` hashes were re-pinned when server-side tool calls began
    # to be priced per call: that fixture records two web searches, which now
    # add a $0.02 row and $0.02 to the total. The `unpriced` hashes are
    # deliberately untouched -- that fixture records no server-tool calls, and
    # its output being unchanged is the actual compatibility claim: a date-only
    # window over records with no server-tool calls still renders byte for byte
    # as it did.
    proc = run(DATA / dataset, "--runtime", "claude-code", "--since", "2026-08-01",
               "--until", "2026-08-31", "--format", fmt)
    assert proc.returncode == exit_code
    assert hashlib.sha256(proc.stdout.encode()).hexdigest() == expected


@pytest.mark.parametrize("field,value", [
    ("used_percent", float("nan")), ("used_percent", float("inf")),
    ("used_percent", True), ("used_percent", -1), ("window_minutes", 0),
    ("window_minutes", "10080"), ("resets_at", 10**100),
])
def test_invalid_meters_are_counted_without_hiding_valid_usage(tmp_path, field, value):
    entries = _entries()[:2]
    primary = {"used_percent": 52, "window_minutes": 10080, "resets_at": 1786492800}
    primary[field] = value
    entries[1]["payload"]["rate_limits"] = {"primary": primary}
    _write(tmp_path, entries)
    proc = run(tmp_path, "--format", "json")
    assert proc.returncode == 3
    payload = json.loads(proc.stdout)
    assert payload["totals"]["tokens"] == 120
    assert payload["collection"]["invalid_rate_limits"] == 1
    assert "rate_limits" not in payload


@pytest.mark.parametrize("timestamp", ["2026-08-11 not-an-instant", "not-a-date", None])
def test_unstampable_meters_report_timestamp_reason_without_losing_usage(tmp_path, timestamp):
    entries = _entries()[:2]
    entries[1]["timestamp"] = timestamp
    entries[1]["payload"]["rate_limits"] = {"primary": {
        "used_percent": 52, "window_minutes": 10080, "resets_at": 1786492800}}
    _write(tmp_path, entries)
    proc = run(tmp_path, "--format", "json")
    assert proc.returncode == 3
    payload = json.loads(proc.stdout)
    assert payload["totals"]["tokens"] == 120
    assert payload["collection"]["invalid_records"] == 0
    assert payload["collection"]["invalid_rate_limits"] == 0
    assert payload["collection"]["unstamped_rate_limits"] == 1
    assert "rate_limits" not in payload
    table = run(tmp_path)
    assert table.returncode == 3
    assert "Ignored 1 rate-limit snapshot(s) with missing or unparseable timestamps." in table.stdout
    assert "malformed rate-limit" not in table.stdout


@pytest.mark.parametrize("day,expected", [("2026-08-10", 0), ("2026-08-11", 1), ("2026-08-12", 0)])
@pytest.mark.parametrize("defect", ["limits", "primary", "timestamp"])
def test_meter_diagnostics_only_count_the_selected_window(tmp_path, day, expected, defect):
    limits = {"primary": {"used_percent": 52, "window_minutes": 10080,
                          "resets_at": 1786492800}}
    timestamp = day + "T12:00:00Z"
    if defect == "limits":
        limits = []
    elif defect == "primary":
        limits["primary"]["window_minutes"] = 0
    else:
        timestamp = day + " not-an-instant"
    entries = _entries()[:2]
    entries.append({"type": "event_msg", "timestamp": timestamp, "payload": {
        "type": "token_count", "info": None, "rate_limits": limits}})
    _write(tmp_path, entries)
    window = ("--since", "2026-08-11", "--until", "2026-08-11")
    proc = run(tmp_path, *window, "--format", "json")
    assert proc.returncode == 3
    payload = json.loads(proc.stdout)
    assert payload["totals"]["tokens"] == 120
    assert payload["collection"]["invalid_rate_limits"] == (expected if defect != "timestamp" else 0)
    assert payload["collection"]["unstamped_rate_limits"] == (expected if defect == "timestamp" else 0)
    assert "rate_limits" not in payload
    table = run(tmp_path, *window)
    assert table.returncode == 3
    assert ("rate-limit snapshot(s)" in table.stdout) == bool(expected)


@pytest.mark.parametrize("zone,expected", [("UTC", 1), ("America/Los_Angeles", 0)])
def test_meter_diagnostic_window_uses_local_day(tmp_path, zone, expected):
    _write(tmp_path, [{"type": "event_msg", "timestamp": "2026-08-11T00:30:00Z",
                       "payload": {"type": "token_count", "info": None, "rate_limits": []}}])
    proc = run(tmp_path, "--since", "2026-08-11", "--until", "2026-08-11",
               "--tz", zone, "--format", "json")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["collection"]["invalid_rate_limits"] == expected
    assert payload["collection"]["unstamped_rate_limits"] == 0


@pytest.mark.parametrize("timestamp", ["not-a-date", "2026-08-99 bad timestamp", None])
@pytest.mark.parametrize("window", [
    ("--since", "2026-08-01"), ("--until", "2026-09-01"),
    ("--since", "2026-08-01", "--until", "2026-09-01"),
])
def test_meter_without_a_calendar_day_is_excluded_from_any_window(tmp_path, timestamp, window):
    _write(tmp_path, [{"type": "event_msg", "timestamp": timestamp, "payload": {
        "type": "token_count", "info": None, "rate_limits": {"primary": {
            "used_percent": 52, "window_minutes": 10080, "resets_at": 1786492800}}}}])
    proc = run(tmp_path, *window, "--format", "json")
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["collection"]["invalid_rate_limits"] == 0
    assert payload["collection"]["unstamped_rate_limits"] == 0
    assert "rate_limits" not in payload


def test_integer_meter_is_rendered_without_float_overflow(tmp_path):
    entries = _entries()[:2]
    entries[1]["payload"]["rate_limits"] = {"primary": {
        "used_percent": 10**400, "window_minutes": 10080, "resets_at": 1786492800}}
    _write(tmp_path, entries)
    proc = run(tmp_path)
    assert proc.returncode == 3, proc.stderr
    assert f"Used: {10**400}%" in proc.stdout


def test_inconsistent_total_and_subcounter_deltas_are_counted(tmp_path):
    entries = _entries()
    # Model B's otherwise-growing snapshot cannot reduce cumulative input.
    bad = entries[-1]
    usage = bad["payload"]["info"]["total_token_usage"]
    usage.update(input_tokens=90, output_tokens=300, total_tokens=390)
    _write(tmp_path, entries)
    payload = json.loads(run(tmp_path, "--format", "json").stdout)
    assert payload["collection"]["invalid_records"] == 1
    assert payload["totals"]["tokens"] == 120
    usage.update(input_tokens=300, output_tokens=70, total_tokens=999)
    _write(tmp_path, entries)
    payload = json.loads(run(tmp_path, "--format", "json").stdout)
    assert payload["collection"]["invalid_records"] == 1
    assert payload["totals"]["tokens"] == 120


def test_all_unreadable_rollouts_are_unavailable(tmp_path, monkeypatch):
    _write(tmp_path, _entries())
    original = Path.open
    def denied(path, *args, **kwargs):
        if path.parent == tmp_path and path.suffix == ".jsonl":
            raise PermissionError("unreadable fixture")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(adapters.RuntimeUnavailable, match="all 1 rollout file"):
        adapters.get_adapter("codex").collect(tmp_path, None, None, adapters.resolve_timezone("UTC"))
