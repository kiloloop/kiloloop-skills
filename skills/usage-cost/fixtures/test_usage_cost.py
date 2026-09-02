"""Fixture claims for the usage-cost skill.

The central claim is exact and hand-checkable: the synthetic transcript in
`data/priced/` contains a known set of token counts, and at the rates pinned in
`scripts/rates.json` those tokens come to exactly $2.1040. Every figure asserted
below is derived by hand in `EXPECTED_COST_DERIVATION` so a reviewer can check
the arithmetic without running anything.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
DATA = Path(__file__).resolve().parent / "data"
PRICED = DATA / "priced"
UNPRICED = DATA / "unpriced"
ENTRYPOINT = SCRIPTS / "usage_cost.py"

sys.path.insert(0, str(SCRIPTS))

# Imported after the path insert above: the skill ships as a plain directory, so
# its modules are reached by path rather than as an installed package.
import runtime_adapters
import usage_cost

# Per-million-token list rates from scripts/rates.json, with cache rates derived
# as input x {read 0.1, write_5m 1.25, write_1h 2.0}, except where a model
# carries its own read multiplier (claude-fable-5-1: read 0.025):
#
#   claude-opus-5     in 5.00   out 25.00  read 0.50  w5m 6.25   w1h 10.00
#   claude-opus-5#fast in 10.00 out 50.00
#   claude-sonnet-5   in 2.00   out 10.00  read 0.20  w5m 2.50
#   claude-opus-4-8   in 5.00   out 25.00             w5m 6.25
#   claude-fable-5-1  in 10.00  out 50.00  read 0.25  w5m 12.50  w1h 20.00
#
# msg_alpha_a  opus-5      1000 in, 2000 out, 400000 read, 80000 w5m, 20000 w1h
#              0.005 + 0.050 + 0.200 + 0.500 + 0.200                  = 0.9550
# msg_alpha_b  sonnet-5    2000 in,  500 out, 100000 read, 40000 w5m
#              0.004 + 0.005 + 0.020 + 0.100                          = 0.1290
# msg_alpha_c  opus-4-8     500 in, 1500 out, 16000 unsplit cache write
#              0.0025 + 0.0375 + 0.100                                = 0.1400
# msg_alpha_f  opus-5 fast 1000 in, 1000 out
#              0.010 + 0.050                                          = 0.0600
# msg_alpha_g  fable-5-1   1000 in,  200 out, 400000 read, 40000 w5m, 10000 w1h
#              0.010 + 0.010 + 0.100 + 0.500 + 0.200                  = 0.8200
#                                                              total  = 2.1040
EXPECTED_COST_DERIVATION = Decimal("2.1040")
EXPECTED_SONNET_COST = Decimal("0.1290")
EXPECTED_FABLE_5_1_COST = Decimal("0.8200")

EXPECTED_PRICED_TOKENS = 503_000 + 142_500 + 18_000 + 2_000 + 451_200  # 1,116,700


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    """Run the CLI, pinned to UTC unless the caller asked for another zone.

    Days are bucketed in the reader's local zone by default, which is right for
    a human-facing report and wrong for a test: the same fixture would land on
    different days depending on where the machine running CI happens to be.
    """
    argv = [sys.executable, str(ENTRYPOINT), *arguments]
    if "--tz" not in arguments:
        argv += ["--tz", "UTC"]
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def run_json(*arguments: str) -> tuple[dict, subprocess.CompletedProcess[str]]:
    completed = run_cli("--format", "json", *arguments)
    return json.loads(completed.stdout), completed


def test_known_usage_prices_to_an_exact_figure() -> None:
    payload, completed = run_json("--data-root", str(PRICED))

    assert completed.returncode == 0, completed.stderr
    assert payload["complete"] is True
    assert payload["unpriced_models"] == []
    assert Decimal(payload["totals"]["cost"]) == EXPECTED_COST_DERIVATION
    assert payload["totals"]["tokens"] == EXPECTED_PRICED_TOKENS
    assert payload["totals"]["requests"] == 5

    by_id = {model["model_id"]: model for model in payload["models"]}
    assert Decimal(by_id["claude-opus-5"]["cost"]) == Decimal("0.9550")
    assert Decimal(by_id["claude-sonnet-5"]["cost"]) == EXPECTED_SONNET_COST
    assert Decimal(by_id["claude-opus-4-8"]["cost"]) == Decimal("0.1400")
    assert Decimal(by_id["claude-opus-5#fast"]["cost"]) == Decimal("0.0600")
    # 400,000 cache reads at the model's own 0.025x multiplier: $0.10, where the
    # table default of 0.1x would have charged $0.40 for the same tokens.
    assert Decimal(by_id["claude-fable-5-1"]["cost"]) == EXPECTED_FABLE_5_1_COST

    # The unsplit cache-creation total is attributed to the 5-minute tier.
    assert by_id["claude-opus-4-8"]["tokens"]["cache_write_5m"] == 16_000
    assert by_id["claude-opus-4-8"]["tokens"]["cache_write_1h"] == 0


def test_repeated_transcript_records_are_counted_once() -> None:
    payload, completed = run_json("--data-root", str(PRICED))

    assert completed.returncode == 0, completed.stderr
    # The transcript holds eight usage-bearing lines for five distinct requests.
    assert payload["collection"]["duplicate_records"] == 3
    assert payload["totals"]["requests"] == 5
    # Summing every line instead would inflate the cost well past the true
    # figure: one extra copy of the triple-recorded request already exceeds this.
    assert Decimal(payload["totals"]["cost"]) < EXPECTED_COST_DERIVATION + Decimal("0.9550")


def test_malformed_lines_are_skipped_without_failing_the_run() -> None:
    payload, completed = run_json("--data-root", str(PRICED))

    assert completed.returncode == 0, completed.stderr
    assert payload["collection"]["malformed_lines"] == 1
    assert payload["collection"]["files_scanned"] == 1


def test_server_tool_requests_are_reported_but_not_costed() -> None:
    payload, completed = run_json("--data-root", str(PRICED))

    assert completed.returncode == 0, completed.stderr
    assert payload["totals"]["web_search_requests"] == 2
    # The token total alone still reconciles to the hand-derived figure, so the
    # two web searches contributed nothing to the cost.
    assert Decimal(payload["totals"]["cost"]) == EXPECTED_COST_DERIVATION

    table = run_cli("--data-root", str(PRICED))
    assert "billed per request rather than per token" in table.stdout


def test_window_filter_excludes_earlier_days() -> None:
    payload, completed = run_json("--data-root", str(PRICED), "--since", "2026-08-11")

    assert completed.returncode == 0, completed.stderr
    # Dropping the 2026-08-10 request removes exactly its 0.9550 contribution.
    assert Decimal(payload["totals"]["cost"]) == EXPECTED_COST_DERIVATION - Decimal("0.9550")
    assert payload["totals"]["requests"] == 4
    # One *request* is excluded, not its three raw copies: reconciliation now
    # runs before the window, so the counter reports requests throughout.
    assert payload["collection"]["filtered_out"] == 1


def test_unknown_model_is_flagged_and_never_silently_costed() -> None:
    payload, completed = run_json("--data-root", str(UNPRICED))

    assert completed.returncode == usage_cost.EXIT_INCOMPLETE
    assert payload["complete"] is False
    assert payload["unpriced_models"] == ["claude-unreleased-9"]

    by_id = {model["model_id"]: model for model in payload["models"]}
    unknown = by_id["claude-unreleased-9"]
    assert unknown["priced"] is False
    assert unknown["cost"] is None
    # Its tokens are still counted and visible.
    assert unknown["tokens"]["total"] == 10_000
    # ...but excluded from the total, which covers only the priced request.
    assert Decimal(payload["totals"]["cost"]) == Decimal("0.0300")

    table = run_cli("--data-root", str(UNPRICED))
    assert table.returncode == usage_cost.EXIT_INCOMPLETE
    assert "UNPRICED" in table.stdout
    assert "INCOMPLETE" in table.stdout
    assert "claude-unreleased-9" in table.stdout


def test_cost_states_which_tokens_it_covers_when_rows_are_excluded() -> None:
    """A token count and a cost on one line imply the cost prices those tokens.

    The unpriced fixture holds 10,000 unpriced tokens, 2,000 priced ones, and 50
    non-billable ones, so the token total is six times the tokens behind the
    cost. The gap has to be stated rather than left to be inferred from the rows.
    """
    payload, completed = run_json("--data-root", str(UNPRICED))

    assert completed.returncode == usage_cost.EXIT_INCOMPLETE
    assert payload["totals"]["tokens"] == 12_050
    assert payload["totals"]["priced_tokens"] == 2_000
    assert payload["totals"]["requests"] == 3
    assert payload["totals"]["priced_requests"] == 1

    table = run_cli("--data-root", str(UNPRICED))
    assert "Cost covers 2,000 of the 12,050 token(s) shown" in table.stdout

    # With nothing excluded the two scopes agree, so the caveat stays off.
    priced_payload, priced_completed = run_json("--data-root", str(PRICED))
    assert priced_completed.returncode == 0, priced_completed.stderr
    assert priced_payload["totals"]["priced_tokens"] == EXPECTED_PRICED_TOKENS
    assert priced_payload["totals"]["priced_tokens"] == priced_payload["totals"]["tokens"]
    assert "Cost covers" not in run_cli("--data-root", str(PRICED)).stdout


def test_locally_generated_messages_are_excluded_as_non_billable() -> None:
    payload, completed = run_json("--data-root", str(UNPRICED))

    assert completed.returncode == usage_cost.EXIT_INCOMPLETE
    assert payload["non_billable_models"] == ["<synthetic>"]
    by_id = {model["model_id"]: model for model in payload["models"]}
    assert by_id["<synthetic>"]["billable"] is False
    assert by_id["<synthetic>"]["cost"] is None


def test_days_are_bucketed_in_the_requested_zone_not_utc() -> None:
    """A day is a human unit, so it follows the reader's zone.

    04:30 UTC on the 11th is still the evening of the 10th in Los Angeles and
    already the afternoon of the 11th in Tokyo. Bucketing everything in UTC
    would file a US evening's work under tomorrow, which is exactly the figure
    someone opening this report is asking about.
    """
    stamp = "2026-08-11T04:30:00.000Z"
    assert runtime_adapters.day_of(stamp, runtime_adapters.resolve_timezone("UTC")) == (
        "2026-08-11"
    )
    assert runtime_adapters.day_of(
        stamp, runtime_adapters.resolve_timezone("America/Los_Angeles")
    ) == "2026-08-10"
    assert runtime_adapters.day_of(
        stamp, runtime_adapters.resolve_timezone("Asia/Tokyo")
    ) == "2026-08-11"

    # The whole pipeline honours it: in Los Angeles the 2026-08-11T11:15Z
    # request lands on the 11th, but a --since of the 11th in Tokyo keeps it.
    payload, completed = run_json(
        "--data-root", str(PRICED), "--tz", "Asia/Tokyo", "--since", "2026-08-12"
    )
    assert completed.returncode == 0, completed.stderr
    # 11:15Z on the 11th is 20:15 on the 11th in Tokyo, so it stays excluded,
    # while 14:30Z, 15:05Z and 16:00Z on the 12th stay included.
    assert payload["totals"]["requests"] == 3
    assert payload["timezone"] == "Asia/Tokyo"


def test_unknown_timezone_is_rejected_rather_than_silently_falling_back() -> None:
    completed = run_cli("--data-root", str(PRICED), "--tz", "Mars/Olympus_Mons")
    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "unknown timezone" in completed.stderr


def test_summary_leads_the_report_and_yields_to_an_explicit_window() -> None:
    payload, completed = run_json("--data-root", str(PRICED))

    assert completed.returncode == 0, completed.stderr
    assert [row["key"] for row in payload["summary"]] == ["today", "yesterday", "this_week"]

    table = run_cli("--data-root", str(PRICED))
    assert table.stdout.index("Period") < table.stdout.index("Model")
    assert "By model, over the full window:" in table.stdout

    # An explicit window is the same question asked precisely, so the block
    # would only compete with the answer the caller asked for.
    windowed, _ = run_json("--data-root", str(PRICED), "--since", "2026-08-11")
    assert windowed["summary"] == []


def test_summary_periods_sum_the_days_they_claim() -> None:
    """Pinned to a fixed 'today' so the assertion does not rot overnight."""
    zone = runtime_adapters.resolve_timezone("UTC")
    collection = runtime_adapters.get_adapter(runtime_adapters.CLAUDE_CODE).collect(
        PRICED, None, None, zone
    )
    rates = usage_cost.RateTable.load(SCRIPTS / "rates.json")

    # 2026-08-12 is a Wednesday, so its week starts Monday 2026-08-10 and covers
    # every request in the fixture.
    rows = {
        row.key: row
        for row in usage_cost.build_summary(
            collection.records, rates, runtime_adapters.CLAUDE_CODE, date(2026, 8, 12)
        )
    }
    assert rows["today"].cost == (
        Decimal("0.1400") + Decimal("0.0600") + EXPECTED_FABLE_5_1_COST
    )
    assert rows["yesterday"].cost == EXPECTED_SONNET_COST
    assert rows["this_week"].since == "2026-08-10"
    assert rows["this_week"].cost == EXPECTED_COST_DERIVATION
    assert rows["this_week"].tokens == EXPECTED_PRICED_TOKENS


def test_summary_token_counts_are_rounded_for_reading() -> None:
    assert usage_cost._abbrev(6_153_706_655) == "6.2B"
    assert usage_cost._abbrev(597_746_572) == "597.7M"
    assert usage_cost._abbrev(12_050) == "12.1K"
    # Below a thousand there is nothing to round away.
    assert usage_cost._abbrev(950) == "950"


# --- Adversarial regressions ------------------------------------------------
#
# Each test below pins a defect an earlier version of this skill shipped. They
# are grouped so the class of gap stays visible: the original fixtures asserted
# the behavior the code already had, rather than the behavior it owed, and a
# fully green suite hid every one of these.


def _record(adapter, entry, result, line=1):
    return adapter._to_record(
        entry, runtime_adapters.resolve_timezone("UTC"), result, Path("t.jsonl"), line
    )


def _usage_entry(**usage):
    return {
        "timestamp": "2026-08-10T09:00:00Z",
        "requestId": usage.pop("_req", "rq"),
        "message": {
            "id": usage.pop("_id", "mm"),
            "model": "claude-opus-5",
            "usage": usage,
        },
    }


def test_sonnet_5_priced_at_the_current_published_rate() -> None:
    """the table shipped $3/$10 after $2/$10 became standard.

    A rate table is the one input no test can derive, so it is pinned against
    the published figure directly rather than only through the fixture total.
    """
    rates = usage_cost.RateTable.load(SCRIPTS / "rates.json")
    sonnet = rates.lookup("claude-sonnet-5")
    assert sonnet is not None
    assert sonnet.input == Decimal("2.00")
    assert sonnet.output == Decimal("10.00")
    assert sonnet.cache_read == Decimal("0.200")
    assert sonnet.cache_write_5m == Decimal("2.5000")


def test_fable_5_1_cache_reads_price_at_a_fortieth_of_input() -> None:
    """The published rate is $0.25 per million cache reads on Claude Fable 5.1
    and Claude Mythos 5.1 — 0.025x input, where every other model is 0.1x.

    Pinned against the shipped table directly, like the Sonnet rate above:
    the override's arithmetic is proved by the synthetic table below, but only
    this asserts that the shipped table actually carries it.
    """
    rates = usage_cost.RateTable.load(SCRIPTS / "rates.json")
    for model_id in ("claude-fable-5-1", "claude-mythos-5-1"):
        model = rates.lookup(model_id)
        assert model is not None, model_id
        assert model.input == Decimal("10.00")
        assert model.output == Decimal("50.00")
        assert model.cache_read == Decimal("0.25000")
        # The write multipliers are the table's own: 1.25x and 2x still apply.
        assert model.cache_write_5m == Decimal("12.5000")
        assert model.cache_write_1h == Decimal("20.000")

    # The previous generation keeps the default read multiplier.
    fable_5 = rates.lookup("claude-fable-5")
    assert fable_5 is not None
    assert fable_5.cache_read == Decimal("1.000")


def _table(**models) -> dict:
    return {
        "rate_table_version": "test",
        "currency": "USD",
        "cache_multipliers": {"read": "0.1", "write_5m": "1.25", "write_1h": "2.0"},
        "models": models,
    }


def test_a_per_model_cache_multiplier_overrides_only_that_tier_for_that_model() -> None:
    rates = usage_cost.RateTable(
        _table(
            **{
                "model-a": {
                    "input": "10.00",
                    "output": "50.00",
                    "cache_multipliers": {"read": "0.025"},
                    "speeds": {
                        "fast": {"input": "20.00", "output": "100.00"},
                        "slow": {
                            "input": "10.00",
                            "output": "50.00",
                            "cache_multipliers": {"write_1h": "3.0"},
                        },
                    },
                },
                "model-b": {"input": "10.00", "output": "50.00"},
            }
        )
    )
    a = rates.lookup("model-a")
    b = rates.lookup("model-b")
    assert a is not None and b is not None

    # Only the named tier moves, and only on the model that names it.
    assert a.cache_read == Decimal("10.00") * Decimal("0.025")
    assert a.cache_write_5m == Decimal("10.00") * Decimal("1.25")
    assert a.cache_write_1h == Decimal("10.00") * Decimal("2.0")
    assert b.cache_read == Decimal("10.00") * Decimal("0.1")
    assert b.cache_write_5m == Decimal("10.00") * Decimal("1.25")

    # A speed variant inherits the model's override on top of its own rates...
    fast = rates.lookup("model-a#fast")
    assert fast is not None
    assert fast.cache_read == Decimal("20.00") * Decimal("0.025")
    assert fast.cache_write_1h == Decimal("20.00") * Decimal("2.0")
    # ...and may layer its own tier on top, without losing the model's.
    slow = rates.lookup("model-a#slow")
    assert slow is not None
    assert slow.cache_read == Decimal("10.00") * Decimal("0.025")
    assert slow.cache_write_1h == Decimal("10.00") * Decimal("3.0")


def test_a_malformed_cache_multiplier_override_is_a_rate_table_error(tmp_path: Path) -> None:
    """A listed model with a corrupt override is configuration, not a guess.

    Every shape below would otherwise either crash mid-report or silently price
    the model at the table default — the confident-wrong figure this table
    refuses to produce.
    """
    malformed = (
        "0.025",                      # not an object
        ["0.025"],                    # not an object
        {"read": "abc"},              # not a number
        {"read": "-0.1"},             # negative
        {"read": "NaN"},              # not finite
        {"read": None},               # null
        {"read": True},               # boolean
        {"reads": "0.025"},           # not a cache tier
        {"read": "0.025", "input": "1"},  # a rate, not a tier
    )
    for override in malformed:
        payload = _table(**{"model-a": {"input": "10.00", "output": "50.00",
                                        "cache_multipliers": override}})
        with pytest.raises(usage_cost.RateTableError):
            usage_cost.RateTable(payload)
        # The same shape on a speed variant is rejected just the same.
        payload = _table(**{"model-a": {"input": "10.00", "output": "50.00",
                                        "speeds": {"fast": {"input": "20.00", "output": "100.00",
                                                            "cache_multipliers": override}}}})
        with pytest.raises(usage_cost.RateTableError):
            usage_cost.RateTable(payload)

    # And the documented exit code at the CLI, on a table a reader could ship.
    broken = tmp_path / "rates.json"
    broken.write_text(json.dumps(_table(**{"claude-opus-5": {
        "input": "5.00", "output": "25.00", "cache_multipliers": {"read": "ten percent"}}})))
    completed = run_cli("--data-root", str(PRICED), "--rates", str(broken))
    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "cache_multipliers.read" in completed.stderr


def _collect_both_orders(tmp_path: Path, records: list[dict]):
    """Run the same copies through the real collection path in both read orders.

    Reconciliation claims order independence, so every claim about it is
    asserted against forward and reversed transcripts rather than a helper the
    production path might not share.
    """
    results = []
    for tag, ordering in (("fwd", records), ("rev", list(reversed(records)))):
        root = tmp_path / tag
        _write_transcript(root, ordering)
        results.append(
            runtime_adapters.get_adapter(runtime_adapters.CLAUDE_CODE).collect(
                root, None, None, runtime_adapters.resolve_timezone("UTC")
            )
        )
    return results


def test_growing_snapshots_reconcile_to_the_complete_one(tmp_path: Path) -> None:
    """copies of one request are NOT identical.

    A response is appended repeatedly as it streams, so the first copy can hold
    a small fraction of the final output tokens. First-seen deduplication threw
    the rest away and reported the loss as a clean total.
    """
    for result in _collect_both_orders(tmp_path, [
        _snapshot("2026-08-10T09:00:00Z", output_tokens=2),
        _snapshot("2026-08-10T09:00:01Z", output_tokens=1093),
    ]):
        assert len(result.records) == 1
        assert result.records[0].output_tokens == 1093


def test_local_zone_uses_a_transition_table_not_a_frozen_offset() -> None:
    """a fixed offset misplaces records across a DST boundary."""
    zone = runtime_adapters.resolve_timezone("local")
    stamp = "2026-01-15T07:30:00Z"
    if isinstance(zone, ZoneInfo):
        # A real IANA zone applies January's offset to a January timestamp.
        assert runtime_adapters.day_of(stamp, zone) == runtime_adapters.day_of(
            stamp, ZoneInfo(str(zone))
        )
    else:
        # No resolvable system zone name: bucketing goes through the
        # platform's own per-instant rules, and the label claims exactly that
        # rather than presenting an abbreviation as an IANA zone.
        assert isinstance(zone, runtime_adapters.SystemLocal)
        assert runtime_adapters.timezone_label(zone) == "system local"


def test_a_directory_without_transcripts_is_unavailable_not_zero(tmp_path: Path) -> None:
    """an empty directory reported a confident $0.00 and exit 0."""
    empty = tmp_path / "no-transcripts"
    empty.mkdir()
    completed = run_cli("--data-root", str(empty))

    assert completed.returncode == usage_cost.EXIT_RUNTIME_UNAVAILABLE
    assert completed.stdout == ""
    assert "not the same as zero usage" in completed.stderr


def test_empty_cache_creation_falls_back_to_the_flat_total() -> None:
    """an empty dict is still a dict, so the split branch won and
    silently discarded every cache-write token the flat total carried."""
    adapter = runtime_adapters.ClaudeCodeAdapter()
    result = runtime_adapters.CollectionResult()

    empty = _record(adapter, _usage_entry(cache_creation_input_tokens=1000, cache_creation={}), result)
    assert empty.cache_write_5m_tokens == 1000
    assert empty.cache_write_1h_tokens == 0

    # A usable split still wins over the flat total.
    split = _record(
        adapter,
        _usage_entry(
            cache_creation_input_tokens=1000,
            cache_creation={"ephemeral_5m_input_tokens": 600, "ephemeral_1h_input_tokens": 400},
        ),
        result,
    )
    assert (split.cache_write_5m_tokens, split.cache_write_1h_tokens) == (600, 400)


def test_negative_counters_are_rejected_rather_than_reducing_the_total() -> None:
    """a negative counter produced a negative cost and exit 0.

    The check runs on the RAW fields, before the cache fallback: validating
    the reconciled counts instead let a negative flat total read as zero cache
    usage, and a negative split tier get padded back up to the flat total as
    though the record were merely partial.
    """
    adapter = runtime_adapters.ClaudeCodeAdapter()
    result = runtime_adapters.CollectionResult()

    corrupt_usages: list[dict] = [
        {"input_tokens": -1_000_000},
        {"output_tokens": -5},
        {"cache_read_input_tokens": -5},
        # Flat cache total: with an empty split, normalization would have
        # silently turned this into zero cache usage.
        {"cache_creation_input_tokens": -1000, "cache_creation": {}},
        # A negative split tier: padding against flat=1000 would have restored
        # exactly the corrupt amount.
        {
            "cache_creation_input_tokens": 1000,
            "cache_creation": {"ephemeral_5m_input_tokens": -5},
        },
        {"cache_creation": {"ephemeral_1h_input_tokens": -5}},
        {"server_tool_use": {"web_search_requests": -2}},
    ]
    for usage in corrupt_usages:
        assert _record(adapter, _usage_entry(**usage), result) is None, usage
    assert result.invalid_records == len(corrupt_usages)
    assert result.partial_cache_splits == 0
    assert result.cache_split_conflicts == 0


def test_malformed_present_counters_are_invalid_not_zero(tmp_path: Path) -> None:
    """a present non-integer counter collapsed to zero, exactly like absence.

    `input_tokens: -1.5` sailed past the raw guard (which recognized only
    ints) and normalized to zero — exit 0, invalid_records=0, corruption
    indistinguishable from an absent field. A counter that is present must be
    a non-negative integer; only absence contributes zero. (Measured across
    127,859 real usage blocks: every present counter is a non-negative
    integer, so strictness rejects no legitimate usage.)
    """
    adapter = runtime_adapters.ClaudeCodeAdapter()
    result = runtime_adapters.CollectionResult()

    malformed_values = (-1.5, 1.0, "10", True, False, None, [1], {"n": 1})
    checked = 0
    for value in malformed_values:
        for usage in (
            {"input_tokens": value},
            {"output_tokens": value},
            {"cache_read_input_tokens": value},
            {"cache_creation_input_tokens": value},
            {"cache_creation": {"ephemeral_5m_input_tokens": value}},
            {"cache_creation": {"ephemeral_1h_input_tokens": value}},
            {"server_tool_use": {"web_search_requests": value}},
        ):
            assert _record(adapter, _usage_entry(**usage), result) is None, (value, usage)
            checked += 1
    assert result.invalid_records == checked

    # Absence is still fine: a record with no counters at all parses to zeros.
    clean = _record(adapter, _usage_entry(), result)
    assert clean is not None
    assert runtime_adapters._totals_of(clean) == (0, 0, 0, 0, 0, 0)

    # And codex's exact CLI-level probe: the corrupt record is reported, the
    # healthy one still counts.
    root = tmp_path / "malformed"
    _write_transcript(root, [
        _snapshot("2026-08-10T09:00:00Z", input_tokens=-1.5),
        _snapshot("2026-08-10T09:01:00Z", key="req-2", input_tokens=7),
    ])
    payload, completed = run_json("--data-root", str(root))
    assert completed.returncode == 0, completed.stderr
    assert payload["collection"]["invalid_records"] == 1
    assert payload["totals"]["tokens"] == 7


def test_malformed_usage_containers_are_invalid_not_zero(tmp_path: Path) -> None:
    """`cache_creation: 5` was read as an empty container — a trusted zero.

    The counter parser held values to a present-versus-absent standard, but
    their parent containers were conflated: a present scalar, list, or null
    `cache_creation` or `server_tool_use` was treated exactly like an absent
    one, silently erasing whatever the container was supposed to hold. Only an
    absent parent may make its counters absent.
    """
    adapter = runtime_adapters.ClaudeCodeAdapter()
    result = runtime_adapters.CollectionResult()

    checked = 0
    for value in (5, -1, 1.5, "cache", True, None, [1, 2]):
        for field_name in ("cache_creation", "server_tool_use"):
            assert _record(
                adapter, _usage_entry(**{field_name: value}), result
            ) is None, (field_name, value)
            checked += 1
    assert result.invalid_records == checked

    # Codex's exact CLI probes, with a healthy sibling to prove isolation.
    root = tmp_path / "containers"
    _write_transcript(root, [
        _snapshot("2026-08-10T09:00:00Z", cache_creation=5),
        _snapshot("2026-08-10T09:00:30Z", key="req-2", server_tool_use=5),
        _snapshot("2026-08-10T09:01:00Z", key="req-3", input_tokens=7),
    ])
    payload, completed = run_json("--data-root", str(root))
    assert completed.returncode == 0, completed.stderr
    assert payload["collection"]["invalid_records"] == 2
    assert payload["totals"]["requests"] == 1
    assert payload["totals"]["tokens"] == 7


def test_records_without_identity_do_not_collapse_together() -> None:
    """two unrelated requests both keyed ('', ''), so one vanished."""
    adapter = runtime_adapters.ClaudeCodeAdapter()
    result = runtime_adapters.CollectionResult()
    first = _record(adapter, {"timestamp": "2026-08-10T09:00:00Z",
                              "message": {"model": "claude-opus-5",
                                          "usage": {"input_tokens": 10}}}, result, 1)
    second = _record(adapter, {"timestamp": "2026-08-10T09:00:00Z",
                               "message": {"model": "claude-opus-5",
                                           "usage": {"input_tokens": 99}}}, result, 2)

    assert first.dedup_key != second.dedup_key
    assert result.unidentified_records == 2


def test_impossible_calendar_dates_are_usage_errors() -> None:
    """shape-only validation accepted 2026-99-99 and reported empty."""
    for bad in ("2026-99-99", "2026-02-30", "2026-13-01", "2026-02-29"):
        completed = run_cli("--data-root", str(PRICED), "--since", bad)
        assert completed.returncode == usage_cost.EXIT_ERROR, bad

    # 2028 is a leap year, so this one is real and must be accepted.
    assert run_cli("--data-root", str(PRICED), "--since", "2028-02-29").returncode == 0


def _write_transcript(directory: Path, records: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def _snapshot(stamp: str, key: str = "req-1", model: str = "claude-opus-5", **usage) -> dict:
    return {
        "timestamp": stamp,
        "requestId": key,
        "message": {"id": key, "model": model, "usage": usage},
    }


def test_equal_magnitude_composition_conflicts_resolve_by_timestamp_or_drop(
    tmp_path: Path,
) -> None:
    """a token-class ordering convention decided which price became fact.

    A million input tokens and a million output tokens sum alike but price
    $5 versus $25. An earlier lexicographic tie-break selected the input-only
    copy in both read orders even when the output-only copy carried a uniquely
    later timestamp — order-independent, but wrong against the only real
    evidence. Composition disagreements at equal magnitude are facts about
    what to price: they resolve by the uniquely latest usable timestamp, and
    with nothing to order them the request is dropped and the drop reported.
    """
    # A uniquely later copy is the request's final state, in both orders.
    for result in _collect_both_orders(tmp_path / "ordered", [
        _snapshot("2026-08-10T09:00:00Z", input_tokens=1_000_000),
        _snapshot("2026-08-10T09:00:05Z", output_tokens=1_000_000),
    ]):
        assert result.conflicting_records == 0
        assert len(result.records) == 1
        assert result.records[0].output_tokens == 1_000_000
        assert result.records[0].input_tokens == 0

    # Tied timestamps leave nothing to order the copies: dropped, identically
    # in both orders, and counted rather than silently resolved.
    for result in _collect_both_orders(tmp_path / "tied", [
        _snapshot("2026-08-10T09:00:00Z", input_tokens=1_000_000),
        _snapshot("2026-08-10T09:00:00Z", output_tokens=1_000_000),
    ]):
        assert result.records == []
        assert result.conflicting_records == 1


def test_a_request_is_reconciled_before_any_window_is_applied(tmp_path: Path) -> None:
    """Filtering first let one request be counted in two adjacent days.

    Two snapshots of a single request straddling local midnight were each
    filtered on their own timestamp, so a one-day window on the earlier side
    priced the partial snapshot as though it were a whole request.
    """
    root = tmp_path / "straddle"
    _write_transcript(root, [
        _snapshot("2026-08-11T06:59:00Z", output_tokens=2),     # 23:59 on the 10th, LA
        _snapshot("2026-08-11T07:01:00Z", output_tokens=1093),  # 00:01 on the 11th, LA
    ])
    zone = "America/Los_Angeles"

    everything, _ = run_json("--data-root", str(root), "--tz", zone)
    day_one, _ = run_json("--data-root", str(root), "--tz", zone,
                          "--since", "2026-08-10", "--until", "2026-08-10")
    day_two, _ = run_json("--data-root", str(root), "--tz", zone,
                          "--since", "2026-08-11", "--until", "2026-08-11")

    # One logical request, on the day it started, with its final counts.
    assert everything["totals"]["requests"] == 1
    assert everything["totals"]["tokens"] == 1093
    # It belongs to exactly one of the two days, priced whole, never both.
    assert (day_one["totals"]["requests"], day_one["totals"]["tokens"]) == (1, 1093)
    assert day_two["totals"]["requests"] == 0


def test_divergent_copies_are_counted_per_request_not_per_copy(tmp_path: Path) -> None:
    """Three snapshots of one request are one reconciled request, not two."""
    root = tmp_path / "growing"
    _write_transcript(root, [
        _snapshot("2026-08-10T09:00:00Z", output_tokens=2),
        _snapshot("2026-08-10T09:00:01Z", output_tokens=50),
        _snapshot("2026-08-10T09:00:02Z", output_tokens=1093),
    ])
    payload, completed = run_json("--data-root", str(root))

    assert completed.returncode == 0, completed.stderr
    assert payload["totals"]["requests"] == 1
    assert payload["totals"]["tokens"] == 1093
    assert payload["collection"]["duplicate_records"] == 2
    assert payload["collection"]["reconciled_records"] == 1

    table = run_cli("--data-root", str(root))
    assert "1 request(s) had copies whose counts differed" in table.stdout


def test_a_partial_cache_split_carries_its_remainder() -> None:
    """A split accounting for less than the flat total dropped the difference.

    The earlier fix only fell back when the split was entirely empty, so a
    split naming one tier still discarded whatever the other tier held.
    """
    adapter = runtime_adapters.ClaudeCodeAdapter()
    result = runtime_adapters.CollectionResult()
    record = _record(adapter, _usage_entry(
        cache_creation_input_tokens=1000,
        cache_creation={"ephemeral_5m_input_tokens": 600},
    ), result)

    assert record.cache_write_5m_tokens + record.cache_write_1h_tokens == 1000
    assert result.partial_cache_splits == 1

    # A split that reconciles exactly is left alone and not flagged.
    exact = _record(adapter, _usage_entry(
        cache_creation_input_tokens=1000,
        cache_creation={"ephemeral_5m_input_tokens": 600, "ephemeral_1h_input_tokens": 400},
    ), result)
    assert (exact.cache_write_5m_tokens, exact.cache_write_1h_tokens) == (600, 400)
    assert result.partial_cache_splits == 1


def test_an_exact_split_outranks_a_padded_partial_at_equal_totals(tmp_path: Path) -> None:
    """padding a partial split made it tie the exact one — and win.

    A partial split of flat=1000 naming only 600 five-minute tokens was padded
    to (1000, 0), which the per-class tie-break preferred over the exact
    (600, 400) allocation in both read orders — discarding the real lifetime
    composition and underpricing the 1-hour tier. An allocation the source
    stated must outrank one approximated from a flat total.
    """
    for result in _collect_both_orders(tmp_path, [
        _snapshot("2026-08-10T09:00:00Z",
                  cache_creation_input_tokens=1000,
                  cache_creation={"ephemeral_5m_input_tokens": 600}),
        _snapshot("2026-08-10T09:00:00Z",
                  cache_creation_input_tokens=1000,
                  cache_creation={"ephemeral_5m_input_tokens": 600,
                                  "ephemeral_1h_input_tokens": 400}),
    ]):
        assert len(result.records) == 1
        record = result.records[0]
        assert (record.cache_write_5m_tokens, record.cache_write_1h_tokens) == (600, 400)


def test_an_overfull_split_is_used_as_stated_and_the_disagreement_counted(
    tmp_path: Path,
) -> None:
    """a split claiming more than the flat total was summed without comment.

    flat=1000 with an 800+400 split priced 1200 cache-write tokens silently.
    On real transcripts this shape is the aggregated multi-iteration record,
    where the legacy flat field lags the lifetime split — including reading 0
    while the split is populated — so the split is the figure to trust. What
    was missing is the visibility: the disagreement must be counted and stated,
    not absorbed.
    """
    root = tmp_path / "overfull"
    _write_transcript(root, [
        _snapshot("2026-08-10T09:00:00Z",
                  cache_creation_input_tokens=1000,
                  cache_creation={"ephemeral_5m_input_tokens": 800,
                                  "ephemeral_1h_input_tokens": 400}),
        # The flat-reads-zero variant seen on real multi-iteration records.
        _snapshot("2026-08-10T09:01:00Z", key="req-2",
                  cache_creation_input_tokens=0,
                  cache_creation={"ephemeral_1h_input_tokens": 500}),
    ])

    payload, completed = run_json("--data-root", str(root))
    assert completed.returncode == 0, completed.stderr
    assert payload["collection"]["cache_split_conflicts"] == 2
    assert payload["totals"]["requests"] == 2
    by_id = {model["model_id"]: model for model in payload["models"]}
    tokens = by_id["claude-opus-5"]["tokens"]
    assert tokens["cache_write_5m"] == 800
    assert tokens["cache_write_1h"] == 400 + 500

    table = run_cli("--data-root", str(root))
    assert "cache-lifetime split larger than their flat cache-write total" in table.stdout


def test_split_exactness_cannot_discard_other_token_classes(tmp_path: Path) -> None:
    """exactness ranked above the per-class comparison, so it overrode it.

    A padded copy with 1000 output tokens and flat cache 1000 ties an
    exact-split copy with no output and a 1600+400 split at aggregate
    magnitude 2000 — and exactness-first selected the exact copy in both
    orders, silently discarding 1000 output tokens. Exactness is knowledge
    about how cache writes divide between lifetimes, nothing more: it may only
    arbitrate between copies that agree on every other class. A cross-class
    disagreement like this one resolves by timestamp evidence, or is dropped
    and reported when the copies cannot be ordered.
    """
    padded_later = _snapshot("2026-08-10T09:00:05Z",
                             output_tokens=1000,
                             cache_creation_input_tokens=1000)
    exact_earlier = _snapshot("2026-08-10T09:00:00Z",
                              cache_creation_input_tokens=2000,
                              cache_creation={"ephemeral_5m_input_tokens": 1600,
                                              "ephemeral_1h_input_tokens": 400})
    for result in _collect_both_orders(tmp_path, [exact_earlier, padded_later]):
        assert len(result.records) == 1
        record = result.records[0]
        assert record.output_tokens == 1000
        assert (record.cache_write_5m_tokens, record.cache_write_1h_tokens) == (1000, 0)

    # With tied timestamps neither copy's composition is evidence against the
    # other's: the request is dropped and counted, never silently truncated.
    tied = [
        _snapshot("2026-08-10T09:00:00Z",
                  output_tokens=1000,
                  cache_creation_input_tokens=1000),
        _snapshot("2026-08-10T09:00:00Z",
                  cache_creation_input_tokens=2000,
                  cache_creation={"ephemeral_5m_input_tokens": 1600,
                                  "ephemeral_1h_input_tokens": 400}),
    ]
    for result in _collect_both_orders(tmp_path / "tied", tied):
        assert result.records == []
        assert result.conflicting_records == 1


def test_pricing_identity_conflicts_are_not_decided_by_read_order(tmp_path: Path) -> None:
    """identical counts under two speeds priced $5 or $10 by file order.

    Two copies of one request with the same million input tokens, one at
    standard speed and one marked fast, tied on every priced field, so
    whichever was read first won — and the diagnostics stayed silent. With no
    evidence to order them the request is dropped and the drop reported,
    identically in both read orders.
    """
    copies = [
        _snapshot("2026-08-10T09:00:00Z", input_tokens=1_000_000),
        _snapshot("2026-08-10T09:00:00Z", input_tokens=1_000_000, speed="fast"),
    ]
    for result in _collect_both_orders(tmp_path, copies):
        assert result.records == []
        assert result.conflicting_records == 1
        assert result.duplicate_records == 1

    # The same applies to a model conflict, not just a speed variant.
    model_conflict = [
        _snapshot("2026-08-10T09:00:00Z", input_tokens=1_000_000),
        _snapshot("2026-08-10T09:00:00Z", input_tokens=1_000_000, model="claude-sonnet-5"),
    ]
    for result in _collect_both_orders(tmp_path / "model", model_conflict):
        assert result.records == []
        assert result.conflicting_records == 1

    # The drop is stated, not just counted.
    root = tmp_path / "surface"
    _write_transcript(root, copies)
    table = run_cli("--data-root", str(root))
    assert table.returncode == 0
    assert "disagreed on what to price" in table.stdout


def test_a_later_timestamp_resolves_a_pricing_identity_conflict(tmp_path: Path) -> None:
    """The final snapshot is the request's final state, whatever order files land in."""
    for result in _collect_both_orders(tmp_path, [
        _snapshot("2026-08-10T09:00:00Z", input_tokens=1_000_000),
        _snapshot("2026-08-10T09:00:05Z", input_tokens=1_000_000, speed="fast"),
    ]):
        assert result.conflicting_records == 0
        assert [record.model for record in result.records] == ["claude-opus-5#fast"]


def test_malformed_rate_values_exit_two_without_a_traceback(tmp_path: Path) -> None:
    """A bad multiplier escaped as decimal.InvalidOperation and exit 1.

    NaN and infinity are covered too: both parse as Decimals and would poison
    every downstream total while looking like valid numbers.
    """
    base = json.loads((SCRIPTS / "rates.json").read_text())
    for bad in ("bogus", "NaN", "Infinity", "-1"):
        table = dict(base)
        table["cache_multipliers"] = {**base["cache_multipliers"], "read": bad}
        path = tmp_path / f"rates-{bad}.json"
        path.write_text(json.dumps(table), encoding="utf-8")

        completed = run_cli("--data-root", str(PRICED), "--rates", str(path))
        assert completed.returncode == usage_cost.EXIT_ERROR, bad
        assert "Traceback" not in completed.stderr, bad
        assert "cache_multipliers.read" in completed.stderr, bad


def test_local_fallback_matches_iana_rules_across_transitions(monkeypatch) -> None:
    """the fallback tzinfo reinterpreted local wall fields as UTC.

    A hand-written tzinfo double-applied the offset near DST transitions —
    2025-03-09T10:00Z rendered as 03:00-08:00 PST, an instant one hour off.
    The fallback now buckets through `time.localtime` directly, so the claim
    to pin is exact agreement with the IANA zone the platform is following,
    on both sides of both transitions.
    """
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset is unavailable on this platform")
    monkeypatch.setattr(runtime_adapters, "_local_zone_name", lambda: None)
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    time.tzset()
    try:
        zone = runtime_adapters.resolve_timezone("local")
        assert isinstance(zone, runtime_adapters.SystemLocal)
        assert runtime_adapters.timezone_label(zone) == "system local"

        reference = ZoneInfo("America/Los_Angeles")
        probes = [
            "2026-01-15T07:30:00Z",  # winter: 23:30 on the 14th, PST
            "2025-07-01T06:59:00Z",  # summer: 23:59 on June 30th, PDT
            "2025-03-09T09:59:00Z",  # the last PST minute before spring-forward
            "2025-03-09T10:00:00Z",  # the spring-forward instant itself
            "2025-11-02T08:59:00Z",  # the last PDT minute before fall-back
            "2025-11-02T09:00:00Z",  # the fall-back instant itself
        ]
        for stamp in probes:
            assert runtime_adapters.day_of(stamp, zone) == runtime_adapters.day_of(
                stamp, reference
            ), stamp
        # And one absolute anchor, so agreement cannot mean 'both wrong'.
        assert runtime_adapters.day_of("2026-01-15T07:30:00Z", zone) == "2026-01-14"
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


def test_unsupported_runtime_reports_an_explicit_unavailable_state() -> None:
    completed = run_cli("--runtime", "codex")

    assert completed.returncode == usage_cost.EXIT_RUNTIME_UNAVAILABLE
    assert completed.stdout == ""
    assert "unavailable for runtime 'codex'" in completed.stderr
    assert "no local usage source is established" in completed.stderr


def test_missing_data_root_reports_an_actionable_unavailable_state(tmp_path: Path) -> None:
    absent = tmp_path / "not-a-transcript-directory"
    completed = run_cli("--data-root", str(absent))

    assert completed.returncode == usage_cost.EXIT_RUNTIME_UNAVAILABLE
    assert "--data-root" in completed.stderr
    assert str(absent) in completed.stderr


def test_dated_model_ids_resolve_to_their_base_rates() -> None:
    rates = usage_cost.RateTable.load(SCRIPTS / "rates.json")

    dated = rates.lookup("claude-haiku-4-5-20251001")
    undated = rates.lookup("claude-haiku-4-5")
    assert dated is not None and undated is not None
    assert dated.input == undated.input
    assert dated.output == undated.output


def test_near_miss_model_ids_are_not_matched_by_similarity() -> None:
    rates = usage_cost.RateTable.load(SCRIPTS / "rates.json")

    # A prefix of a real id must not be priced at that id's rates.
    assert rates.lookup("claude-opus") is None
    assert rates.lookup("claude-opus-5-turbo") is None
    # A speed variant the table does not define is unpriced, not silently
    # charged at the standard rate.
    assert rates.lookup("claude-sonnet-5#fast") is None


def test_cache_tiers_use_their_own_multiples_of_the_input_rate() -> None:
    rates = usage_cost.RateTable.load(SCRIPTS / "rates.json")
    opus = rates.lookup("claude-opus-5")

    assert opus is not None
    assert opus.cache_read == opus.input / 10
    assert opus.cache_write_5m == opus.input * Decimal("1.25")
    assert opus.cache_write_1h == opus.input * 2


def test_a_corrupt_rate_table_is_a_configuration_error(tmp_path: Path) -> None:
    broken = tmp_path / "rates.json"
    broken.write_text('{"rate_table_version": "x"}', encoding="utf-8")

    completed = run_cli("--data-root", str(PRICED), "--rates", str(broken))

    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "rate table is missing required data" in completed.stderr


def test_a_listed_model_with_a_corrupt_rate_is_a_configuration_error(
    tmp_path: Path,
) -> None:
    """model entries were validated only when a lookup happened to reach them.

    A bogus rate on a model present in the data escaped as a traceback and
    exit 1 mid-report; deleting the field instead exited 3 and called the model
    'unpriced' — as though the table did not know a model it explicitly lists.
    Both are configuration errors, distinct from a genuinely unknown model id,
    and both are caught when the table loads.
    """
    base = json.loads((SCRIPTS / "rates.json").read_text())

    def run_with(mutate) -> subprocess.CompletedProcess[str]:
        table = json.loads(json.dumps(base))
        mutate(table["models"])
        path = tmp_path / "rates.json"
        path.write_text(json.dumps(table), encoding="utf-8")
        return run_cli("--data-root", str(PRICED), "--rates", str(path))

    for bad in ("bogus", "NaN", "-1"):
        completed = run_with(lambda models, value=bad: models["claude-opus-5"].update(input=value))
        assert completed.returncode == usage_cost.EXIT_ERROR, bad
        assert "Traceback" not in completed.stderr, bad
        assert "models.claude-opus-5.input" in completed.stderr, bad

    # A listed model missing a required rate is malformed, not unknown.
    completed = run_with(lambda models: models["claude-opus-5"].pop("input"))
    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "missing the required 'input' rate" in completed.stderr

    # An entry that is not an object at all.
    completed = run_with(lambda models: models.update({"claude-opus-5": "5.00"}))
    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "must be an object" in completed.stderr

    # Speed variants are held to the same standard as base entries.
    completed = run_with(
        lambda models: models["claude-opus-5"]["speeds"].update(fast={"input": "oops"})
    )
    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "models.claude-opus-5.speeds.fast.input" in completed.stderr

    # The eager check fires even when no usage reaches the broken model: the
    # table itself is the artifact being validated.
    table = json.loads(json.dumps(base))
    table["models"]["claude-idle-model"] = {"input": "nonsense", "output": "1.00"}
    path = tmp_path / "rates.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    completed = run_cli("--data-root", str(PRICED), "--rates", str(path))
    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "models.claude-idle-model.input" in completed.stderr


def test_a_speed_variant_never_inherits_the_standard_rates(tmp_path: Path) -> None:
    """an empty variant merged over its base entry and priced at standard.

    Variant validation merged the base entry before checking required fields,
    so `speeds.fast: {}` passed validation and a million fast input tokens
    priced at $5 instead of the $10 the shipped fast rate charges — a
    confident figure at the wrong rate, from the table meant to prevent
    exactly that. A variant exists because it prices differently; it must
    state its own rates.
    """
    base = json.loads((SCRIPTS / "rates.json").read_text())

    table = json.loads(json.dumps(base))
    table["models"]["claude-opus-5"]["speeds"]["fast"] = {}
    path = tmp_path / "rates.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    completed = run_cli("--data-root", str(PRICED), "--rates", str(path))
    assert completed.returncode == usage_cost.EXIT_ERROR
    assert "Traceback" not in completed.stderr
    assert "models.claude-opus-5.speeds.fast" in completed.stderr

    # And the shipped table's own variant carries explicit rates that differ
    # from standard, so the guard is protecting a real difference.
    rates = usage_cost.RateTable.load(SCRIPTS / "rates.json")
    fast = rates.lookup("claude-opus-5#fast")
    standard = rates.lookup("claude-opus-5")
    assert fast is not None and standard is not None
    assert fast.input != standard.input


def test_non_billable_models_must_be_a_list_of_strings(tmp_path: Path) -> None:
    """`non_billable_models: null` escaped as a TypeError traceback, exit 1."""
    base = json.loads((SCRIPTS / "rates.json").read_text())
    for bad in (None, "x", [5], {"a": 1}):
        table = json.loads(json.dumps(base))
        table["non_billable_models"] = bad
        path = tmp_path / "rates.json"
        path.write_text(json.dumps(table), encoding="utf-8")
        completed = run_cli("--data-root", str(PRICED), "--rates", str(path))
        assert completed.returncode == usage_cost.EXIT_ERROR, bad
        assert "Traceback" not in completed.stderr, bad
        assert "non_billable_models" in completed.stderr, bad


def test_adapter_registry_exposes_the_supported_runtime() -> None:
    assert runtime_adapters.CLAUDE_CODE in runtime_adapters.available_runtimes()
    adapter = runtime_adapters.get_adapter(runtime_adapters.CLAUDE_CODE)
    assert adapter.availability(PRICED).supported is True
