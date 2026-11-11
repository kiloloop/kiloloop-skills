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
    absent_line = next(
        line for line in completed.stdout.splitlines() if "absent upstream, left as-is:" in line
    )
    assert {"claude-mythos-5", "claude-mythos-5-1"} <= set(absent_line.split(": ", 1)[1].split(", "))
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
    parsed, unpriced = refresh_rates.load_catalog_from_dir(catalog, "anthropic")
    assert unpriced == []
    assert parsed["claude-fast"].fast_input == Decimal("2")
    assert parsed["claude-other-experiment"].fast_input is None
    assert parsed["claude-empty-modes"].fast_input is None


# Schema-2 files. The shapes follow real models.dev provider files (an
# Anthropic model with a fast mode, an OpenAI model whose fast mode runs on the
# priority tier, an OpenAI model whose fast mode carries its own [cost]); the
# names and some prices are synthetic.
V2_FAST_BY_MULTIPLIER = """schema = 2
name = "Claude V2"

[cost.text.input]
price = 4
cache = { read = 0.2, write = { "5m" = 5, "1h" = 8 } }

[[cost.text.input.overrides]]
when = { context = { gt = 200_000 } }
price = 8

[cost.text.output]
price = 20

[cost.web_search]
price = 10

[[cost.multipliers]]
when = { service_tier = { equals = "fast" } }
factor = { text = 2 }

[[cost.multipliers]]
when = { region = { equals = "us" } }
factor = { text = 1.1 }

[experimental.modes.fast]
body = { speed = "fast" }
"""

V2_FAST_ON_ANOTHER_TIER = """schema = 2
name = "GPT V2"

[cost.text.input]
price = 10
cache = { read = 1, write = { "12h" = 25, "30m" = 12.5 } }

[cost.text.output]
price = 50

[[cost.multipliers]]
when = { service_tier = { equals = "priority" } }
factor = { text = 2 }

[[cost.multipliers]]
when = { service_tier = { equals = "ultrafast" } }
factor = { text = 6 }

[[cost.multipliers]]
when = { service_tier = { equals = "flex" } }
factor = { text = 0.5 }

[experimental.modes.fast]
service_tier = "priority"
body = { service_tier = "priority" }
"""

V2_FAST_WITH_OWN_COST = """schema = 2
name = "o-mini V2"

[cost.text.input]
price = 1.1
cache = { read = 0.275 }

[cost.text.output]
price = 4.4

[[cost.multipliers]]
when = { service_tier = { equals = "priority" } }
factor = 9

[experimental.modes.fast]
service_tier = "priority"

[experimental.modes.fast.cost.text.input]
price = 2

[experimental.modes.fast.cost.text.output]
price = 8
"""


def _v2(multipliers: str = "", modes: str = "[experimental.modes.fast]\n", cache: str = "") -> str:
    """A schema-2 file at $1 / $5 with the given multipliers, modes and cache."""
    return (
        'schema = 2\n[cost.text.input]\nprice = 1\n'
        + cache
        + "[cost.text.output]\nprice = 5\n"
        + multipliers
        + modes
    )


def test_schema_2_entries_map_onto_the_fields_schema_1_carried(tmp_path: Path) -> None:
    """The catalog moved to schema 2 with no change in what the table compares.

    Base prices come from [cost.text.input] and [cost.text.output]; the
    compared cache write is the shortest-lived tier, which is what a schema-1
    file's single `cache_write` always was; fast mode is priced by its own
    [cost] or by every multiplier on the service tier it runs on. Multipliers
    that condition on anything else, and long-context overrides, are not read.
    """
    catalog = _write_catalog(
        tmp_path / "catalog",
        {
            "claude-v2": V2_FAST_BY_MULTIPLIER,
            "gpt-v2": V2_FAST_ON_ANOTHER_TIER,
            "o-mini-v2": V2_FAST_WITH_OWN_COST,
            "claude-v1": _toml(5, 25, cache_read=0.5, cache_write=6.25),
        },
    )
    parsed, unpriced = refresh_rates.load_catalog_from_dir(catalog, "anthropic")
    assert unpriced == []

    claude = parsed["claude-v2"]
    assert (claude.display_name, claude.input, claude.output) == ("Claude V2", Decimal("4"), Decimal("20"))
    assert claude.cache_read == Decimal("0.2")
    assert claude.cache_write == Decimal("5")  # 5m, not 1h
    # The fast tier doubles text; the US-region multiplier and the >200K override do not apply.
    assert (claude.fast_input, claude.fast_output) == (Decimal("8"), Decimal("40"))

    gpt = parsed["gpt-v2"]
    assert gpt.cache_write == Decimal("12.5")  # 30m, not 12h, whichever is written first
    # The mode names its tier; only the priority multiplier prices it.
    assert (gpt.fast_input, gpt.fast_output) == (Decimal("20"), Decimal("100"))

    # A mode's own [cost] replaces the base prices and outranks any multiplier.
    own = parsed["o-mini-v2"]
    assert (own.fast_input, own.fast_output) == (Decimal("2"), Decimal("8"))
    assert own.cache_read == Decimal("0.275")
    assert own.cache_write is None

    # A schema-1 file in the same directory still reads the flat layout.
    assert (parsed["claude-v1"].input, parsed["claude-v1"].cache_write) == (Decimal("5"), Decimal("6.25"))


def test_schema_2_fast_multipliers_follow_the_factor_grammar(tmp_path: Path) -> None:
    """Each factor and condition shape the schema allows, against $1 / $5 base prices."""
    fast_tier = 'when = { service_tier = { equals = "fast" } }\n'
    cases = {
        # A bare number scales every price, text included.
        "bare-number": (f"[[cost.multipliers]]\n{fast_tier}factor = 3\n", (3, 15)),
        # A table scales only what it names; text left out stays at the base price.
        "names-another-category": (f"[[cost.multipliers]]\n{fast_tier}factor = {{ audio = 4 }}\n", (1, 5)),
        "splits-text-by-side": (
            f"[[cost.multipliers]]\n{fast_tier}factor = {{ text = {{ input = 2, output = 3 }} }}\n",
            (2, 15),
        ),
        "names-one-side-only": (
            f"[[cost.multipliers]]\n{fast_tier}factor = {{ text = {{ input = 2 }} }}\n",
            (2, 5),
        ),
        # Every matching multiplier applies, so two on the tier compound.
        "two-on-the-tier": (
            f"[[cost.multipliers]]\n{fast_tier}factor = {{ text = 2 }}\n"
            f"[[cost.multipliers]]\n{fast_tier}factor = 1.5\n",
            (3, 15),
        ),
        "tier-in-a-list": (
            '[[cost.multipliers]]\nwhen = { service_tier = { in = ["priority", "fast"] } }\nfactor = 2\n',
            (2, 10),
        ),
        # A condition on more than the tier does not price the list rate.
        "tier-and-region": (
            '[[cost.multipliers]]\nwhen = { service_tier = { equals = "fast" }, region = { equals = "us" } }\n'
            "factor = 2\n",
            None,
        ),
        # A zero factor is a factor like any other; it is never read as "unset".
        "zero-then-two": (
            f"[[cost.multipliers]]\n{fast_tier}factor = 0\n[[cost.multipliers]]\n{fast_tier}factor = 2\n",
            (0, 0),
        ),
        "two-then-zero": (
            f"[[cost.multipliers]]\n{fast_tier}factor = 2\n[[cost.multipliers]]\n{fast_tier}factor = 0\n",
            (0, 0),
        ),
        "another-tier": ('[[cost.multipliers]]\nwhen = { service_tier = { equals = "flex" } }\nfactor = 0.5\n', None),
        "no-multipliers": ("", None),
    }
    for label, (multipliers, expected) in cases.items():
        catalog = _write_catalog(tmp_path / label, {"claude-x": _v2(multipliers)})
        parsed, _ = refresh_rates.load_catalog_from_dir(catalog, "anthropic")
        entry = parsed["claude-x"]
        if expected is None:
            # A mode nothing prices states no fast rate.
            assert (entry.fast_input, entry.fast_output) == (None, None), label
        else:
            assert (entry.fast_input, entry.fast_output) == tuple(Decimal(v) for v in expected), label

    # Cache writes: one price for every lifetime, or a table of lifetimes.
    for cache, expected_write in (
        ('cache = { read = 0.1, write = 1.25 }\n', Decimal("1.25")),
        ('cache = { write = { "1h" = 2, "90s" = 1.1, "1d" = 3 } }\n', Decimal("1.1")),
        ("cache = { read = 0.1 }\n", None),
    ):
        catalog = _write_catalog(tmp_path / f"cache-{expected_write}", {"claude-x": _v2(cache=cache)})
        parsed, _ = refresh_rates.load_catalog_from_dir(catalog, "anthropic")
        assert parsed["claude-x"].cache_write == expected_write, cache


def test_schema_2_entries_without_a_text_price_are_skipped(tmp_path: Path) -> None:
    """Audio and image models price no text token, so the table has no row to compare."""
    catalog = _write_catalog(
        tmp_path / "catalog",
        {
            "claude-x": _v2(),
            # Its base model is not in the catalog: a skipped entry's name is
            # never resolved, so the dangling reference cannot stop the run.
            "claude-speech": (
                'schema = 2\nbase_model = "anthropic/absent"\n'
                "[cost.audio.input]\nprice = 6\n[cost.audio.output]\nprice = 24\n"
            ),
            "claude-tts": "schema = 2\n[cost.text.input]\nprice = 15\n",
            "claude-image": "schema = 2\n[cost.image.input]\nprice = 5\n",
        },
    )
    row = {"display_name": "Claude X", "input": "1.00", "output": "5.00"}
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps(_table(**{"claude-x": row})), encoding="utf-8")

    completed = run("--catalog-dir", str(catalog), "--rates", str(rates))
    assert completed.returncode == refresh_rates.EXIT_AGREES, completed.stderr + completed.stdout
    assert (
        "no text-token price upstream, skipped: claude-image, claude-speech, claude-tts"
        in completed.stdout
    )


# Every unit the schema-2 grammar allows besides tokens (models.dev CostUnitV2).
NON_TOKEN_UNITS = ("character", "image", "megapixel", "second", "minute")


def test_schema_2_prices_in_other_units_are_refused_never_written(tmp_path: Path) -> None:
    """A text price per character, second or other unit is not a per-token price.

    Copied into this per-million-token table it would be a confident wrong
    figure, so a priced text side with any unit but `token` exits 2 before the
    table is touched — on either side, in the base cost or in a fast mode's own
    cost. An omitted unit, or an explicit `token`, is a token price.
    """
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps(_table()), encoding="utf-8")
    before = rates.read_bytes()

    def base(input_unit: str = "", output_unit: str = "") -> str:
        return (
            'schema = 2\nname = "Example Text Model"\n'
            f"[cost.text.input]\n{input_unit}price = 1\n"
            f"[cost.text.output]\n{output_unit}price = 5\n"
        )

    def mode(input_unit: str = "", output_unit: str = "") -> str:
        return (
            base()
            + f"[experimental.modes.fast.cost.text.input]\n{input_unit}price = 2\n"
            + f"[experimental.modes.fast.cost.text.output]\n{output_unit}price = 10\n"
        )

    refused = {"reviewer-repro": base('unit = "character"\n', 'unit = "second"\n')}
    for unit in NON_TOKEN_UNITS:
        line = f'unit = "{unit}"\n'
        refused[f"base-input-{unit}"] = base(input_unit=line)
        refused[f"base-output-{unit}"] = base(output_unit=line)
        refused[f"mode-input-{unit}"] = mode(input_unit=line)
        refused[f"mode-output-{unit}"] = mode(output_unit=line)
    refused["unit-is-a-number"] = base(input_unit="unit = 5\n")

    for label, text in refused.items():
        catalog = _write_catalog(tmp_path / label, {"example-text-model": text})
        completed = run(
            "--catalog-dir", str(catalog), "--rates", str(rates),
            "--write", "--today", "2026-11-11", "--commit", SYNTHETIC_COMMIT,
        )
        assert_refused(completed, label)
        assert "example-text-model" in completed.stderr and ".unit is " in completed.stderr, label
        assert rates.read_bytes() == before, label

    # Controls: an omitted unit and an explicit `token` are token prices, in
    # both places, and a write adds the row.
    for label, text in {
        "omitted": mode(),
        "explicit-token": mode('unit = "token"\n', 'unit = "token"\n').replace(
            "price = 1\n", 'unit = "token"\nprice = 1\n'
        ),
    }.items():
        catalog = _write_catalog(tmp_path / f"accepted-{label}", {"example-text-model": text})
        parsed, _ = refresh_rates.load_catalog_from_dir(catalog, "anthropic")
        entry = parsed["example-text-model"]
        assert (entry.input, entry.output, entry.fast_input, entry.fast_output) == (
            Decimal("1"), Decimal("5"), Decimal("2"), Decimal("10")
        ), label

    # A side with no price is skipped before its unit is read: a text-to-speech
    # model priced per input character with no text output (OpenAI's tts-1
    # shape) is listed as skipped, not refused, so a real catalog still runs.
    catalog = _write_catalog(
        tmp_path / "tts",
        {
            "claude-x": _v2(),
            "example-tts": 'schema = 2\n[cost.text.input]\nunit = "character"\nprice = 15\n',
        },
    )
    rates.write_text(
        json.dumps(_table(**{"claude-x": {"display_name": "Claude X", "input": "1.00", "output": "5.00"}})),
        encoding="utf-8",
    )
    completed = run("--catalog-dir", str(catalog), "--rates", str(rates))
    assert completed.returncode == refresh_rates.EXIT_AGREES, completed.stderr + completed.stdout
    assert "no text-token price upstream, skipped: example-tts" in completed.stdout


def test_a_schema_2_catalog_is_compared_and_merged_like_schema_1(tmp_path: Path) -> None:
    """End to end: agreement, a reported change, and a write, on schema-2 input."""
    catalog = _write_catalog(tmp_path / "catalog", {"claude-v2": V2_FAST_BY_MULTIPLIER})
    row = {
        "display_name": "Claude V2",
        "input": "4.00",
        "output": "20.00",
        "cache_multipliers": {"read": "0.05"},
        "speeds": {"fast": {"input": "8.00", "output": "40.00"}},
    }
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps(_table(**{"claude-v2": row})), encoding="utf-8")

    completed = run("--catalog-dir", str(catalog), "--rates", str(rates))
    assert completed.returncode == refresh_rates.EXIT_AGREES, completed.stderr + completed.stdout
    assert "every catalog row agrees with the table" in completed.stdout
    assert "\n  note " not in completed.stdout

    raised = _write_catalog(
        tmp_path / "raised",
        {"claude-v2": V2_FAST_BY_MULTIPLIER.replace("price = 20", "price = 25")},
    )
    completed = run("--catalog-dir", str(raised), "--rates", str(rates))
    assert completed.returncode == refresh_rates.EXIT_DRIFT, completed.stderr + completed.stdout
    assert "changed  claude-v2.output  20.00 -> 25.00" in completed.stdout
    # The doubled fast output now differs from the table's, and is reported, not written.
    assert "claude-v2: fast variant prices 8.00/40.00 in the table, 8.00/50.00 upstream" in completed.stdout

    written = run(
        "--catalog-dir", str(raised), "--rates", str(rates),
        "--write", "--today", "2026-11-11", "--commit", SYNTHETIC_COMMIT,
    )
    assert written.returncode == refresh_rates.EXIT_AGREES, written.stderr
    assert json.loads(rates.read_text())["models"]["claude-v2"]["output"] == "25.00"


def test_malformed_schema_2_entries_are_errors_not_guesses(tmp_path: Path) -> None:
    """The schema-2 grammar, enumerated the same way: every shape it cannot use exits 2."""
    rates = SCRIPTS / "rates.json"
    fast_tier = 'when = { service_tier = { equals = "fast" } }\n'
    malformed = {
        "unknown-schema": "schema = 3\n[cost]\ninput = 1\noutput = 5\n",
        "schema-is-a-string": 'schema = "2"\n[cost]\ninput = 1\noutput = 5\n',
        "schema-is-a-boolean": "schema = true\n[cost]\ninput = 1\noutput = 5\n",
        "text-is-a-string": 'schema = 2\n[cost]\ntext = "cheap"\n',
        "input-is-an-array": "schema = 2\n[cost.text]\ninput = [1]\noutput = { price = 5 }\n",
        "string-price": 'schema = 2\n[cost.text.input]\nprice = "one"\n[cost.text.output]\nprice = 5\n',
        "negative-price": "schema = 2\n[cost.text.input]\nprice = -1\n[cost.text.output]\nprice = 5\n",
        "cache-is-a-number": _v2(cache="cache = 0.1\n"),
        "string-cache-read": _v2(cache='cache = { read = "x" }\n'),
        "empty-write-table": _v2(cache="cache = { write = {} }\n"),
        "unreadable-lifetime": _v2(cache='cache = { write = { "5 minutes" = 1.25 } }\n'),
        "string-write-price": _v2(cache='cache = { write = { "5m" = "x" } }\n'),
        "multipliers-is-a-table": _v2("[cost.multipliers]\nfactor = 2\n"),
        "multiplier-is-a-number": (
            "schema = 2\n[cost]\nmultipliers = [2]\n[cost.text.input]\nprice = 1\n"
            "[cost.text.output]\nprice = 5\n[experimental.modes.fast]\n"
        ),
        "multiplier-without-factor": _v2(f"[[cost.multipliers]]\n{fast_tier}"),
        "multiplier-without-when": _v2("[[cost.multipliers]]\nfactor = 2\n"),
        "negative-factor": _v2(f"[[cost.multipliers]]\n{fast_tier}factor = -2\n"),
        "string-factor": _v2(f'[[cost.multipliers]]\n{fast_tier}factor = {{ text = "x" }}\n'),
        "factor-finer-than-a-side": _v2(
            f"[[cost.multipliers]]\n{fast_tier}factor = {{ text = {{ input = {{ cache = 2 }} }} }}\n"
        ),
        "mode-tier-is-a-number": _v2(modes="[experimental.modes.fast]\nservice_tier = 5\n"),
        "mode-is-a-string": _v2(modes='[experimental.modes]\nfast = "x"\n'),
        "mode-cost-is-a-number": _v2(modes="[experimental.modes.fast]\ncost = 1\n"),
        "mode-cost-without-output": _v2(
            modes="[experimental.modes.fast.cost.text.input]\nprice = 2\n"
        ),
    }
    for label, text in malformed.items():
        catalog = _write_catalog(tmp_path / label, {"claude-broken": text})
        completed = run("--catalog-dir", str(catalog), "--rates", str(rates))
        assert_refused(completed, label)
        assert "claude-broken" in completed.stderr, label


def test_an_entry_without_a_cost_table_is_skipped_unless_the_table_prices_it(
    tmp_path: Path,
) -> None:
    """A catalog entry that prices nothing is skipped by id, not a reason to stop.

    A provider directory can carry entries with no token rates at all, and one
    of them used to abort the whole comparison before any row was read. The
    skip is narrow: when the table has a row for that id, a missing upstream
    price is a discrepancy, and the run exits 2 naming it.
    """
    catalog = _write_catalog(
        tmp_path / "catalog",
        {"claude-x": _toml(1, 5), "claude-image-latest": 'name = "Image"\n'},
    )
    row = {"display_name": "Claude X", "input": "1.00", "output": "5.00"}
    rates = tmp_path / "rates.json"
    rates.write_text(json.dumps(_table(**{"claude-x": row})), encoding="utf-8")

    completed = run("--catalog-dir", str(catalog), "--rates", str(rates))
    assert completed.returncode == refresh_rates.EXIT_AGREES, completed.stderr + completed.stdout
    assert "no text-token price upstream, skipped: claude-image-latest" in completed.stdout
    assert "every catalog row agrees with the table" in completed.stdout

    # A write adds no row for the skipped id.
    written = run(
        "--catalog-dir", str(catalog), "--rates", str(rates),
        "--write", "--today", "2026-09-23", "--commit", SYNTHETIC_COMMIT,
    )
    assert written.returncode == refresh_rates.EXIT_AGREES, written.stderr
    assert sorted(json.loads(rates.read_text())["models"]) == ["claude-x"]

    # The same entry fails loudly once the table has a row for it.
    asked = tmp_path / "asked.json"
    asked.write_text(
        json.dumps(_table(**{"claude-x": row, "claude-image-latest": {**row, "display_name": "Image"}})),
        encoding="utf-8",
    )
    before = asked.read_bytes()
    for extra in ((), ("--write", "--today", "2026-09-23", "--commit", SYNTHETIC_COMMIT)):
        completed = run("--catalog-dir", str(catalog), "--rates", str(asked), *extra)
        assert_refused(completed, f"priced id without a cost table {extra}")
        assert "claude-image-latest: has no text-token price upstream" in completed.stderr
    assert asked.read_bytes() == before

    # A catalog in which nothing is priced has nothing to compare.
    unpriced_only = _write_catalog(tmp_path / "unpriced-only", {"claude-image-latest": 'name = "Image"\n'})
    completed = run("--catalog-dir", str(unpriced_only), "--rates", str(rates))
    assert_refused(completed, "unpriced-only catalog")
    assert "no model files with a [cost] table" in completed.stderr


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
    parsed, _ = refresh_rates.load_catalog_from_dir(catalog, "anthropic")
    assert parsed["claude-named"].display_name == "Claude Named"

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
