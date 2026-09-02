"""Fixture claims for refresh_rates.py, the rate-table maintenance script.

The shipped table is compared with a vendored slice of the models.dev catalog
pinned at one commit (`data/catalog/SOURCE.md` names it), and with synthetic
catalogs built in-test, so every claim here runs offline and deterministically.
"""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
CATALOG = Path(__file__).resolve().parent / "data" / "catalog"
ENTRYPOINT = SCRIPTS / "refresh_rates.py"
# Any full SHA will do for provenance in a synthetic catalog; only its shape is checked.
SYNTHETIC_COMMIT = "0123456789abcdef0123456789abcdef01234567"

sys.path.insert(0, str(SCRIPTS))

# Imported after the path insert above: the skill ships as a plain directory.
import refresh_rates
import usage_cost


def run(*arguments: str) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(ENTRYPOINT), *arguments]
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def assert_refused(completed: subprocess.CompletedProcess[str], label: str) -> None:
    """The contract for every bad input: exit 2, a message, no traceback, no report."""
    assert completed.returncode == refresh_rates.EXIT_ERROR, f"{label}: {completed.stderr}"
    assert completed.stderr.strip(), label
    assert "Traceback" not in completed.stderr, f"{label}: {completed.stderr}"
    assert completed.stdout == "", label


def _toml(
    input_rate: object,
    output_rate: object,
    cache_read: object = None,
    cache_write: object = None,
    name: str | None = None,
    base_model: str | None = None,
    fast: tuple[object, object] | None = None,
) -> str:
    lines: list[str] = []
    if name:
        lines.append(f'name = "{name}"')
    if base_model:
        lines.append(f'base_model = "{base_model}"')
    lines += ["[cost]", f"input = {input_rate}", f"output = {output_rate}"]
    if cache_read is not None:
        lines.append(f"cache_read = {cache_read}")
    if cache_write is not None:
        lines.append(f"cache_write = {cache_write}")
    if fast:
        lines += [
            "[experimental.modes.fast]",
            f"cost = {{ input = {fast[0]}, output = {fast[1]} }}",
        ]
    return "\n".join(lines) + "\n"


def _write_catalog(
    root: Path, provider_files: dict[str, str], base_files: dict[str, str] | None = None
) -> Path:
    models_dir = root / "providers" / "anthropic" / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    for model_id, text in provider_files.items():
        (models_dir / f"{model_id}.toml").write_text(text, encoding="utf-8")
    for base_model, text in (base_files or {}).items():
        path = root / "models" / f"{base_model}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _table(**models: dict) -> dict:
    return {
        "rate_table_version": "2026-01-01",
        "currency": "USD",
        "source_checked": "2026-01-01",
        "cache_multipliers": {"read": "0.1", "write_5m": "1.25", "write_1h": "2.0"},
        "models": models,
        "non_billable_models": ["<synthetic>"],
    }


def test_shipped_table_agrees_with_the_pinned_catalog_slice() -> None:
    """The invariant the maintenance script exists to hold.

    Every model the pinned catalog prices, the shipped table prices the same
    way; models the catalog does not carry are reported and left alone.
    """
    completed = run("--catalog-dir", str(CATALOG), "--rates", str(SCRIPTS / "rates.json"))

    assert completed.returncode == refresh_rates.EXIT_AGREES, completed.stderr + completed.stdout
    assert "every catalog row agrees with the table" in completed.stdout
    assert "\n  added " not in completed.stdout
    assert "\n  changed " not in completed.stdout
    assert "\n  note " not in completed.stdout
    # Limited-availability models the catalog does not list stay hand-entered.
    assert "absent upstream, left as-is: claude-mythos-5, claude-mythos-5-1" in completed.stdout
    # Dated aliases resolve to their base id in the table, so they add nothing.
    assert "claude-haiku-4-5-20251001" in completed.stdout


def test_new_and_changed_rows_are_reported_then_merged_on_write(tmp_path: Path) -> None:
    rates = tmp_path / "rates.json"
    rates.write_text(
        json.dumps(
            _table(
                **{
                    "claude-opus-5": {
                        "display_name": "Claude Opus 5",
                        "input": "5.00",
                        "output": "25.00",
                        "speeds": {"fast": {"input": "10.00", "output": "50.00"}},
                    },
                    "claude-sonnet-5": {
                        "display_name": "Claude Sonnet 5",
                        "input": "2.00",
                        "output": "10.00",
                    },
                    "claude-mythos-5": {
                        "display_name": "Claude Mythos 5",
                        "input": "10.00",
                        "output": "50.00",
                    },
                }
            ),
            indent=2,
        )
    )
    catalog = _write_catalog(
        tmp_path / "catalog",
        {
            # Agrees on price; disagrees on the single cache-write figure and on
            # the fast variant, both of which are reported and never written.
            "claude-opus-5": _toml(
                5, 25, 0.5, 7, base_model="anthropic/claude-opus-5", fast=(12, 60)
            ),
            "claude-sonnet-5": _toml(2, 12, 0.2, 2.5, name="Claude Sonnet 5"),
            "claude-fable-5-1": _toml(10, 50, 0.25, 12.5, base_model="anthropic/claude-fable-5-1"),
            "claude-haiku-4-5": _toml(1, 5, 0.1, 1.25, name="Claude Haiku 4.5"),
            "claude-haiku-4-5-20251001": _toml(1, 5, 0.1, 1.25, name="Claude Haiku 4.5"),
        },
        {
            "anthropic/claude-opus-5": 'name = "Claude Opus 5"\n',
            "anthropic/claude-fable-5-1": 'name = "Claude Fable 5.1"\n',
        },
    )

    dry = run("--catalog-dir", str(catalog), "--rates", str(rates))
    assert dry.returncode == refresh_rates.EXIT_DRIFT, dry.stderr
    assert (
        "added    claude-fable-5-1  in 10.00  out 50.00  cache read x0.025 (table default x0.1)"
        in dry.stdout
    )
    assert "added    claude-haiku-4-5  in 1.00  out 5.00  cache read x0.1" in dry.stdout
    assert "changed  claude-sonnet-5.output  10.00 -> 12.00" in dry.stdout
    assert (
        "claude-opus-5: catalog cache_write 7.00 differs from the table's 5-minute write 6.25"
        in dry.stdout
    )
    assert (
        "claude-opus-5: fast variant prices 10.00/50.00 in the table, 12.00/60.00 upstream"
        in dry.stdout
    )
    assert "absent upstream, left as-is: claude-mythos-5" in dry.stdout
    assert "dated aliases covered by their base id: claude-haiku-4-5-20251001" in dry.stdout
    assert "not written" in dry.stderr
    # A dry run leaves the table exactly as it was.
    assert json.loads(rates.read_text())["rate_table_version"] == "2026-01-01"

    written = run(
        "--catalog-dir", str(catalog), "--rates", str(rates),
        "--write", "--today", "2026-09-02", "--commit", SYNTHETIC_COMMIT,
    )
    assert written.returncode == refresh_rates.EXIT_AGREES, written.stderr
    payload = json.loads(rates.read_text())
    assert payload["rate_table_version"] == "2026-09-02"
    # The date a person last read the published page is not the script's to move.
    assert payload["source_checked"] == "2026-01-01"
    keys = list(payload)
    assert keys[keys.index("source_checked") + 1] == "catalog_cross_check"
    assert payload["catalog_cross_check"] == {
        "source": "https://github.com/anomalyco/models.dev",
        "provider": "anthropic",
        "commit": SYNTHETIC_COMMIT,
        "checked": "2026-09-02",
    }
    assert payload["cache_multipliers"] == {"read": "0.1", "write_5m": "1.25", "write_1h": "2.0"}

    models = payload["models"]
    assert models["claude-fable-5-1"] == {
        "display_name": "Claude Fable 5.1",
        "input": "10.00",
        "output": "50.00",
        "cache_multipliers": {"read": "0.025"},
    }
    assert models["claude-haiku-4-5"] == {
        "display_name": "Claude Haiku 4.5",
        "input": "1.00",
        "output": "5.00",
    }
    assert "claude-haiku-4-5-20251001" not in models
    assert models["claude-sonnet-5"]["output"] == "12.00"
    assert models["claude-mythos-5"] == {
        "display_name": "Claude Mythos 5",
        "input": "10.00",
        "output": "50.00",
    }
    assert models["claude-opus-5"]["speeds"] == {"fast": {"input": "10.00", "output": "50.00"}}

    # The written table loads through the report's own validator and prices
    # the new row at its own read multiplier.
    table = usage_cost.RateTable.load(rates)
    fable = table.lookup("claude-fable-5-1")
    assert fable is not None
    assert fable.cache_read == Decimal("0.25")
    assert fable.cache_write_1h == Decimal("20.00")

    # A second pass has nothing left to report.
    again = run("--catalog-dir", str(catalog), "--rates", str(rates))
    assert again.returncode == refresh_rates.EXIT_AGREES, again.stderr
    assert "every catalog row agrees with the table" in again.stdout


def test_cache_read_drift_moves_the_model_override_never_the_table_default(
    tmp_path: Path,
) -> None:
    rates = tmp_path / "rates.json"
    rates.write_text(
        json.dumps(
            _table(
                **{
                    # Priced at the default 0.1x, but the catalog now says 0.025x.
                    "claude-opus-5": {"display_name": "Claude Opus 5", "input": "5.00", "output": "25.00"},
                    # Carries an override the catalog no longer supports.
                    "claude-sonnet-5": {
                        "display_name": "Claude Sonnet 5",
                        "input": "2.00",
                        "output": "10.00",
                        "cache_multipliers": {"read": "0.05", "write_1h": "3.0"},
                    },
                }
            )
        )
    )
    catalog = _write_catalog(
        tmp_path / "catalog",
        {
            "claude-opus-5": _toml(5, 25, 0.125, 6.25, name="Claude Opus 5"),
            "claude-sonnet-5": _toml(2, 10, 0.2, 2.5, name="Claude Sonnet 5"),
        },
    )

    dry = run("--catalog-dir", str(catalog), "--rates", str(rates))
    assert dry.returncode == refresh_rates.EXIT_DRIFT
    assert "changed  claude-opus-5.cache_read  0.50 -> 0.125" in dry.stdout
    assert "changed  claude-sonnet-5.cache_read  0.10 -> 0.20" in dry.stdout

    # A write with no commit to record is refused before the table is touched:
    # provenance is never written empty.
    before = rates.read_bytes()
    unpinned = run("--catalog-dir", str(catalog), "--rates", str(rates), "--write", "--today", "2026-09-02")
    assert_refused(unpinned, "write without --commit")
    assert "--write needs --commit" in unpinned.stderr
    assert rates.read_bytes() == before

    written = run(
        "--catalog-dir", str(catalog), "--rates", str(rates),
        "--write", "--today", "2026-09-02", "--commit", SYNTHETIC_COMMIT,
    )
    assert written.returncode == refresh_rates.EXIT_AGREES, written.stderr
    payload = json.loads(rates.read_text())
    assert payload["cache_multipliers"]["read"] == "0.1"
    assert payload["models"]["claude-opus-5"]["cache_multipliers"] == {"read": "0.025"}
    # Only the read tier is the catalog's to settle; the 1-hour override stays.
    assert payload["models"]["claude-sonnet-5"]["cache_multipliers"] == {"write_1h": "3.0"}
    assert payload["catalog_cross_check"]["commit"] == SYNTHETIC_COMMIT


def test_the_catalog_commit_is_a_full_sha_never_a_ref(tmp_path: Path) -> None:
    """Provenance must name one immutable catalog.

    A branch or tag names whatever the repository holds today and a short SHA
    can be ambiguous, so none of them may reach a fetch or the written table.
    The grammar is enumerated: every shape that is not exactly forty hex
    digits is refused, on the dry-run path and the write path alike.
    """
    rates = tmp_path / "rates.json"
    rates.write_bytes((SCRIPTS / "rates.json").read_bytes())
    before = rates.read_bytes()
    refused = {
        "branch": "main",
        "tag": "v1.0.0",
        "symbolic ref": "HEAD",
        "short sha": SYNTHETIC_COMMIT[:7],
        "twelve-char sha": SYNTHETIC_COMMIT[:12],
        "thirty-nine chars": SYNTHETIC_COMMIT[:39],
        "forty-one chars": SYNTHETIC_COMMIT + "0",
        "forty chars, not hex": SYNTHETIC_COMMIT[:39] + "g",
        "sha with whitespace": " " + SYNTHETIC_COMMIT[:39],
        "empty": "",
    }
    for label, value in refused.items():
        for extra in ((), ("--write", "--today", "2026-09-02")):
            completed = run("--catalog-dir", str(CATALOG), "--rates", str(rates), "--commit", value, *extra)
            assert_refused(completed, f"{label} {extra}")
            assert "not a full 40-character commit SHA" in completed.stderr, label
    assert rates.read_bytes() == before

    # A full SHA in either case is accepted and recorded in its canonical lowercase form.
    written = run(
        "--catalog-dir", str(CATALOG), "--rates", str(rates),
        "--write", "--today", "2026-09-02", "--commit", SYNTHETIC_COMMIT.upper(),
    )
    assert written.returncode == refresh_rates.EXIT_AGREES, written.stderr
    assert json.loads(rates.read_text())["catalog_cross_check"]["commit"] == SYNTHETIC_COMMIT


def test_malformed_catalog_entries_are_errors_not_guesses(tmp_path: Path) -> None:
    """Every shape the table cannot use exits 2 with a message, never a traceback.

    The provider file grammar is enumerated key by key: the top-level scalars,
    the [cost] table and its prices, and every level of the nested
    [experimental.modes.fast.cost] path, each given a value of the wrong type
    or a required part left out.
    """
    rates = SCRIPTS / "rates.json"
    priced = "[cost]\ninput = 1\noutput = 5\n"
    malformed = {
        "not-toml": "[cost\ninput = 1\n",
        "no-cost": 'name = "Broken"\n',
        "cost-is-a-string": 'cost = "cheap"\n',
        "cost-is-an-array": "cost = [1, 5]\n",
        "missing-output": "[cost]\ninput = 1\n",
        "string-price": '[cost]\ninput = "ten"\noutput = 5\n',
        "negative-price": "[cost]\ninput = -1\noutput = 5\n",
        "boolean-price": "[cost]\ninput = true\noutput = 5\n",
        "nan-price": "[cost]\ninput = nan\noutput = 5\n",
        "infinite-price": "[cost]\ninput = inf\noutput = 5\n",
        "string-cache-read": '[cost]\ninput = 1\noutput = 5\ncache_read = "x"\n',
        "array-cache-write": "[cost]\ninput = 1\noutput = 5\ncache_write = [1]\n",
        "name-is-a-number": "name = 5\n" + priced,
        "name-is-empty": 'name = ""\n' + priced,
        "base-model-is-a-number": "base_model = 7\n" + priced,
        "experimental-is-a-string": 'experimental = "not a table"\n' + priced,
        "experimental-is-an-array": "experimental = [1]\n" + priced,
        "modes-is-a-number": priced + "[experimental]\nmodes = 3\n",
        "fast-is-a-string": priced + '[experimental.modes]\nfast = "x"\n',
        "fast-cost-is-a-number": priced + "[experimental.modes.fast]\ncost = 1\n",
        "fast-without-cost": priced + "[experimental.modes.fast]\nprovider = {}\n",
        "fast-cost-missing-output": priced + "[experimental.modes.fast]\ncost = { input = 1 }\n",
        "fast-cost-missing-input": priced + "[experimental.modes.fast]\ncost = { output = 1 }\n",
        "fast-price-is-a-string": priced + '[experimental.modes.fast]\ncost = { input = "x", output = 1 }\n',
    }
    for label, text in malformed.items():
        catalog = _write_catalog(tmp_path / label, {"claude-broken": text})
        completed = run("--catalog-dir", str(catalog), "--rates", str(rates))
        assert_refused(completed, label)
        assert "claude-broken" in completed.stderr, label

    # Bytes that are not UTF-8 are a read failure, not a traceback.
    catalog = _write_catalog(tmp_path / "bad-utf8", {})
    (catalog / "providers" / "anthropic" / "models" / "claude-broken.toml").write_bytes(b"\xff\xfe" + priced.encode())
    assert_refused(run("--catalog-dir", str(catalog), "--rates", str(rates)), "bad-utf8")

    # Controls: the same nested path with a complete fast cost, and an
    # [experimental] table that simply has no fast mode, both parse.
    accepted = {
        "claude-fast": priced + "[experimental.modes.fast]\ncost = { input = 2, output = 10 }\n",
        "claude-other-experiment": priced + "[experimental]\nother = true\n",
        "claude-empty-modes": priced + "[experimental.modes]\n",
    }
    catalog = _write_catalog(tmp_path / "accepted", accepted)
    parsed = refresh_rates.load_catalog_from_dir(catalog, "anthropic")
    assert parsed["claude-fast"].fast_input == Decimal("2")
    assert parsed["claude-other-experiment"].fast_input is None
    assert parsed["claude-empty-modes"].fast_input is None


def test_a_base_model_reference_must_resolve_to_a_named_file(tmp_path: Path) -> None:
    """A row whose display name comes from `base_model` fails loudly when it cannot.

    A missing file, an unreadable one, one without a name, and a reference
    that is not a catalog path are all errors: a display name is not
    something to fall back on silently.
    """
    rates = SCRIPTS / "rates.json"
    priced = "[cost]\ninput = 1\noutput = 5\n"
    cases = {
        "missing-file": ('base_model = "anthropic/absent"\n' + priced, {}),
        "file-not-toml": (
            'base_model = "anthropic/broken"\n' + priced,
            {"anthropic/broken": "name = \n"},
        ),
        "file-without-name": (
            'base_model = "anthropic/nameless"\n' + priced,
            {"anthropic/nameless": "family = \"x\"\n"},
        ),
        "file-name-not-a-string": (
            'base_model = "anthropic/numeric"\n' + priced,
            {"anthropic/numeric": "name = 5\n"},
        ),
        "traversing-reference": ('base_model = "../escape"\n' + priced, {}),
        "absolute-reference": ('base_model = "/etc/hosts"\n' + priced, {}),
        "empty-segment": ('base_model = "anthropic//x"\n' + priced, {}),
    }
    for label, (text, base_files) in cases.items():
        catalog = _write_catalog(tmp_path / label, {"claude-broken": text}, base_files)
        assert_refused(run("--catalog-dir", str(catalog), "--rates", str(rates)), label)

    # Control: a resolvable reference names the row after its base model.
    catalog = _write_catalog(
        tmp_path / "resolves",
        {"claude-named": 'base_model = "anthropic/claude-named"\n' + priced},
        {"anthropic/claude-named": 'name = "Claude Named"\n'},
    )
    assert refresh_rates.load_catalog_from_dir(catalog, "anthropic")["claude-named"].display_name == "Claude Named"

    # An empty provider directory is an error too, not a table with nothing to do.
    empty = tmp_path / "empty" / "providers" / "anthropic" / "models"
    empty.mkdir(parents=True)
    completed = run("--catalog-dir", str(tmp_path / "empty"), "--rates", str(rates))
    assert completed.returncode == refresh_rates.EXIT_ERROR
    assert "no model files" in completed.stderr

    # And a table the report itself would refuse is refused here, before any fetch.
    broken = tmp_path / "rates.json"
    broken.write_text('{"rate_table_version": "x"}')
    completed = run("--catalog-dir", str(CATALOG), "--rates", str(broken))
    assert completed.returncode == refresh_rates.EXIT_ERROR
    assert "missing required data" in completed.stderr
