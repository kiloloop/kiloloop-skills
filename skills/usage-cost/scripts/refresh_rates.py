#!/usr/bin/env python3
"""Compare, and optionally refresh, rates.json against the models.dev catalog.

The shipped rate table is a hand-verified, versioned, offline artifact, and the
report never fetches anything. This maintenance script is the one place the
skill touches the network. A maintainer runs it when a model launches; the
report does not run it. It reads one provider's model files from the models.dev
repository at an exact commit, compares them with the table, and either prints
the differences or writes them back.

It proposes; it does not decide. Three things the catalog cannot express stay
hand-maintained from the published pricing page: the 1-hour cache-write rate
(the catalog carries a single cache-write price, which is compared against the
5-minute tier and never written), models the catalog does not list, and the
`server_tools` block. The catalog is a per-model index and prices tokens only —
it carries no per-call rate for a server-side tool such as web search, and its
`tool_call` field is a capability flag, not a price — so this script neither
reads nor writes that block. Rows the catalog lacks are left untouched, and
`source_checked` — the date the published page was last read by a person — is
never moved by this script.

Exit codes:
    0  the table agrees with the catalog, or --write brought it into agreement
    1  differences found and not written
    2  the catalog or the table could not be read or is malformed, or --commit
       is not a full commit SHA

Provenance is a full 40-character commit SHA and nothing else. A branch, a
tag, or an abbreviated SHA names whatever the repository holds today, so a
table refreshed "from main" could not say what it was refreshed from; the
script refuses them before reading anything, and --write always needs one so
the provenance block is never written empty.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Imported after the path insert above so the skill directory can be copied and
# run from anywhere without installing anything.
from usage_cost import RateTable, RateTableError  # noqa: E402

EXIT_AGREES = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

DEFAULT_REPO = "anomalyco/models.dev"
DEFAULT_PROVIDER = "anthropic"
DEFAULT_RATES = Path(__file__).resolve().parent / "rates.json"
USER_AGENT = "usage-cost-refresh-rates"
TIMEOUT_SECONDS = 30
_COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40}")
_PATH_SEGMENT = re.compile(r"[A-Za-z0-9._-]+")


class CatalogError(Exception):
    pass


@dataclass(frozen=True)
class CatalogEntry:
    model_id: str
    display_name: str
    input: Decimal
    output: Decimal
    cache_read: Decimal | None
    cache_write: Decimal | None
    fast_input: Decimal | None = None
    fast_output: Decimal | None = None


@dataclass
class Diff:
    added: list[tuple[CatalogEntry, Decimal | None]] = field(default_factory=list)
    changed: list[tuple[str, str, str, str]] = field(default_factory=list)
    read_overrides: dict[str, Decimal | None] = field(default_factory=dict)
    write_notes: list[str] = field(default_factory=list)
    fast_notes: list[str] = field(default_factory=list)
    absent_upstream: list[str] = field(default_factory=list)
    aliases_skipped: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed)


# --- Catalog loading ---------------------------------------------------------


def _decimal(name: str, value: object) -> Decimal:
    """Turn a catalog number into an exact Decimal.

    The catalog stores prices as TOML numbers, which parse to floats. `str()`
    of a float is its shortest round-trip form, so `12.5` comes back as
    "12.5" and `0.025` as "0.025" — the text that was written, not a binary
    approximation of it. Anything else is refused rather than guessed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CatalogError(f"{name} must be a number, got {value!r}")
    try:
        parsed = Decimal(str(value))
    except DecimalException:
        raise CatalogError(f"{name} is not a number: {value!r}") from None
    if not parsed.is_finite():
        raise CatalogError(f"{name} must be a finite number, got {value!r}")
    if parsed < 0:
        raise CatalogError(f"{name} must not be negative, got {value!r}")
    return parsed


def _optional_decimal(name: str, table: dict, key: str) -> Decimal | None:
    if key not in table:
        return None
    return _decimal(f"{name}.{key}", table[key])


def _table(name: str, parent: dict, key: str) -> dict | None:
    """Return parent[key] when it is a table, None when absent; refuse anything else.

    Every nested object is checked before it is descended into, so a key that
    should be a table but is a string, a number, or an array is a CatalogError
    and never an AttributeError with a traceback.
    """
    if key not in parent:
        return None
    value = parent[key]
    if not isinstance(value, dict):
        raise CatalogError(f"{name}.{key} must be a table, got {type(value).__name__}")
    return value


def _optional_string(name: str, parent: dict, key: str) -> str | None:
    if key not in parent:
        return None
    value = parent[key]
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{name}.{key} must be a non-empty string, got {value!r}")
    return value


def _base_model_ref(model_id: str, value: str) -> str:
    """A base_model is a relative catalog path like `anthropic/claude-opus-5`."""
    segments = value.split("/")
    if any(
        segment in ("", ".", "..") or not _PATH_SEGMENT.fullmatch(segment)
        for segment in segments
    ):
        raise CatalogError(f"{model_id}.base_model is not a catalog path: {value!r}")
    return value


def parse_entry(model_id: str, text: str, base_name: Callable[[str], str]) -> CatalogEntry:
    """Parse one provider model file into the fields the rate table needs.

    Every shape the table cannot use is a CatalogError, never a guess: a key
    that should be a table but is not, a price that is not a number, a fast
    mode that does not price both input and output. `base_name` resolves a
    `base_model` reference to that model's display name and raises
    CatalogError when the referenced file is missing or unreadable.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(f"{model_id}: not valid TOML: {error}") from None

    cost = _table(model_id, data, "cost")
    if cost is None:
        raise CatalogError(f"{model_id}: has no [cost] table; a priced row cannot be derived")
    for required in ("input", "output"):
        if required not in cost:
            raise CatalogError(f"{model_id}: [cost] is missing {required!r}")

    display_name = _optional_string(model_id, data, "name")
    if display_name is None:
        base_model = _optional_string(model_id, data, "base_model")
        if base_model is None:
            display_name = model_id
        else:
            display_name = base_name(_base_model_ref(model_id, base_model))

    fast_input = fast_output = None
    experimental = _table(model_id, data, "experimental")
    modes = fast = None
    if experimental is not None:
        modes = _table(f"{model_id}.experimental", experimental, "modes")
    if modes is not None:
        fast = _table(f"{model_id}.experimental.modes", modes, "fast")
    if fast is not None:
        fast_cost = _table(f"{model_id}.experimental.modes.fast", fast, "cost")
        if fast_cost is None or "input" not in fast_cost or "output" not in fast_cost:
            raise CatalogError(
                f"{model_id}: [experimental.modes.fast] must price both input and output"
            )
        fast_input = _decimal(f"{model_id}.experimental.modes.fast.cost.input", fast_cost["input"])
        fast_output = _decimal(f"{model_id}.experimental.modes.fast.cost.output", fast_cost["output"])

    return CatalogEntry(
        model_id=model_id,
        display_name=display_name,
        input=_decimal(f"{model_id}.cost.input", cost["input"]),
        output=_decimal(f"{model_id}.cost.output", cost["output"]),
        cache_read=_optional_decimal(f"{model_id}.cost", cost, "cache_read"),
        cache_write=_optional_decimal(f"{model_id}.cost", cost, "cache_write"),
        fast_input=fast_input,
        fast_output=fast_output,
    )


def _base_name_from_text(base_model: str, text: str) -> str:
    label = f"models/{base_model}.toml"
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise CatalogError(f"{label}: not valid TOML: {error}") from None
    name = _optional_string(label, data, "name")
    if name is None:
        raise CatalogError(f"{label}: has no name to display")
    return name


def load_catalog_from_dir(root: Path, provider: str) -> dict[str, CatalogEntry]:
    """Read a checkout (or a vendored slice of one) laid out like the repository."""
    models_dir = root / "providers" / provider / "models"
    if not models_dir.is_dir():
        raise CatalogError(f"{models_dir} is not a directory")

    def base_name(base_model: str) -> str:
        path = root / "models" / f"{base_model}.toml"
        if not path.is_file():
            raise CatalogError(f"base_model {base_model!r} has no models/{base_model}.toml")
        return _base_name_from_text(base_model, path.read_text(encoding="utf-8"))

    catalog: dict[str, CatalogEntry] = {}
    for path in sorted(models_dir.glob("*.toml")):
        catalog[path.stem] = parse_entry(path.stem, path.read_text(encoding="utf-8"), base_name)
    if not catalog:
        raise CatalogError(f"{models_dir} holds no model files")
    return catalog


def _http_get(url: str, token: str | None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        raise CatalogError(f"GET {url} failed: HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise CatalogError(f"GET {url} failed: {error}") from None


def fetch_catalog_from_commit(
    repo: str, commit: str, provider: str, token: str | None = None
) -> dict[str, CatalogEntry]:
    """Read one provider's model files from the repository at an exact commit.

    A commit is the only pinnable form of the catalog: the published JSON
    endpoint carries no version or timestamp, so a table refreshed from it
    could not say what it was refreshed from.
    """
    listing_url = (
        f"https://api.github.com/repos/{repo}/contents/providers/{provider}/models?ref={commit}"
    )
    try:
        listing = json.loads(_http_get(listing_url, token))
    except json.JSONDecodeError as error:
        raise CatalogError(f"{listing_url}: not valid JSON: {error}") from None
    if not isinstance(listing, list):
        raise CatalogError(f"{listing_url}: expected a directory listing")

    raw_base = f"https://raw.githubusercontent.com/{repo}/{commit}"

    def base_name(base_model: str) -> str:
        text = _http_get(f"{raw_base}/models/{base_model}.toml", token).decode("utf-8")
        return _base_name_from_text(base_model, text)

    catalog: dict[str, CatalogEntry] = {}
    for item in sorted(listing, key=lambda entry: str(entry.get("name", ""))):
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.endswith(".toml"):
            continue
        model_id = name[: -len(".toml")]
        text = _http_get(f"{raw_base}/providers/{provider}/models/{name}", token).decode("utf-8")
        catalog[model_id] = parse_entry(model_id, text, base_name)
    if not catalog:
        raise CatalogError(f"{listing_url}: no model files found")
    return catalog


# --- Comparison --------------------------------------------------------------


def _strip_date_suffix(model_id: str) -> str:
    head, sep, tail = model_id.rpartition("-")
    if sep and len(tail) == 8 and tail.isdigit():
        return head
    return model_id


def _money(value: Decimal) -> str:
    """Format a per-million rate the way the table stores it: at least two decimals."""
    text = format(value, "f")
    whole, _, fraction = text.partition(".")
    fraction = fraction.rstrip("0")
    return f"{whole}.{fraction.ljust(2, '0')}"


def _multiplier(value: Decimal) -> str:
    return format(value.normalize(), "f")


def compare(payload: dict, table: RateTable, catalog: dict[str, CatalogEntry]) -> Diff:
    diff = Diff()
    default_read = Decimal(str(payload["cache_multipliers"]["read"]))
    models = payload["models"]

    for model_id, entry in catalog.items():
        undated = _strip_date_suffix(model_id)
        if undated != model_id and undated in catalog:
            # The table resolves a dated id to its undated twin, so the alias
            # would only duplicate a row that already prices it.
            diff.aliases_skipped.append(model_id)
            continue

        read_override: Decimal | None = None
        if entry.cache_read is not None and entry.input > 0:
            derived = entry.cache_read / entry.input
            read_override = None if derived == default_read else derived

        current = table.lookup(model_id)
        if current is None or model_id not in models:
            diff.added.append((entry, read_override))
            diff.read_overrides[model_id] = read_override
            continue

        for field_name, old, new in (
            ("input", current.input, entry.input),
            ("output", current.output, entry.output),
        ):
            if old != new:
                diff.changed.append((model_id, field_name, _money(old), _money(new)))
        if entry.cache_read is not None and current.cache_read != entry.cache_read:
            diff.changed.append(
                (model_id, "cache_read", _money(current.cache_read), _money(entry.cache_read))
            )
            diff.read_overrides[model_id] = read_override
        if entry.cache_write is not None and current.cache_write_5m != entry.cache_write:
            diff.write_notes.append(
                f"{model_id}: catalog cache_write {_money(entry.cache_write)} differs from the "
                f"table's 5-minute write {_money(current.cache_write_5m)} (reported, not written)"
            )

        speeds = models[model_id].get("speeds") if isinstance(models[model_id], dict) else None
        fast = speeds.get("fast") if isinstance(speeds, dict) else None
        if entry.fast_input is not None and fast is None:
            diff.fast_notes.append(f"{model_id}: catalog lists a fast variant the table lacks")
        elif entry.fast_input is None and fast is not None:
            diff.fast_notes.append(f"{model_id}: table's fast variant is absent upstream")
        elif entry.fast_input is not None and fast is not None:
            table_fast = table.lookup(f"{model_id}#fast")
            if table_fast is not None and (
                table_fast.input != entry.fast_input or table_fast.output != entry.fast_output
            ):
                diff.fast_notes.append(
                    f"{model_id}: fast variant prices {_money(table_fast.input)}/"
                    f"{_money(table_fast.output)} in the table, "
                    f"{_money(entry.fast_input)}/{_money(entry.fast_output)} upstream"
                )

    diff.absent_upstream = sorted(
        model_id for model_id in models if model_id not in catalog
    )
    return diff


def apply(payload: dict, diff: Diff, today: date, provenance: dict) -> bool:
    """Merge the differences into the table payload. Returns True when a row moved."""
    models = payload["models"]
    added = {entry.model_id: entry for entry, _ in diff.added}
    changed_models = added.keys() | {model_id for model_id, *_ in diff.changed}

    for model_id, entry in added.items():
        models[model_id] = {
            "display_name": entry.display_name,
            "input": _money(entry.input),
            "output": _money(entry.output),
        }
    for model_id, field_name, _, new in diff.changed:
        if field_name in ("input", "output"):
            models[model_id][field_name] = new

    for model_id, override in diff.read_overrides.items():
        row = models[model_id]
        multipliers = row.get("cache_multipliers")
        if override is None:
            if isinstance(multipliers, dict):
                multipliers.pop("read", None)
                if not multipliers:
                    del row["cache_multipliers"]
        else:
            if not isinstance(multipliers, dict):
                multipliers = row["cache_multipliers"] = {}
            multipliers["read"] = _multiplier(override)

    if changed_models:
        payload["rate_table_version"] = today.isoformat()

    # Provenance sits beside source_checked so the two dates read together.
    rebuilt: dict = {}
    for key, value in payload.items():
        if key == "catalog_cross_check":
            continue
        rebuilt[key] = value
        if key == "source_checked":
            rebuilt["catalog_cross_check"] = provenance
    if "catalog_cross_check" not in rebuilt:
        rebuilt["catalog_cross_check"] = provenance
    payload.clear()
    payload.update(rebuilt)
    return bool(changed_models)


def render(diff: Diff, payload: dict, source_label: str, default_read: Decimal) -> str:
    lines = [f"{source_label} vs rates.json (rate_table_version {payload['rate_table_version']})"]
    if diff.added:
        for entry, override in diff.added:
            read = (
                f"cache read x{_multiplier(override)} (table default x{_multiplier(default_read)})"
                if override is not None
                else f"cache read x{_multiplier(default_read)}"
            )
            lines.append(
                f"  added    {entry.model_id}  in {_money(entry.input)}  "
                f"out {_money(entry.output)}  {read}"
            )
    for model_id, field_name, old, new in diff.changed:
        lines.append(f"  changed  {model_id}.{field_name}  {old} -> {new}")
    for note in diff.write_notes:
        lines.append(f"  note     {note}")
    for note in diff.fast_notes:
        lines.append(f"  note     {note}")
    if diff.absent_upstream:
        lines.append(
            "  absent upstream, left as-is: " + ", ".join(diff.absent_upstream)
        )
    if diff.aliases_skipped:
        lines.append("  dated aliases covered by their base id: " + ", ".join(diff.aliases_skipped))
    if not diff.has_changes:
        lines.append("  every catalog row agrees with the table")
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------


def _parse_today(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a YYYY-MM-DD date") from None


def _parse_commit(value: str) -> str:
    """Accept only a full commit SHA: the one form of the catalog that cannot move."""
    if not _COMMIT_SHA.fullmatch(value):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a full 40-character commit SHA; branches, tags and "
            "abbreviated SHAs are refused because they do not name one catalog"
        )
    return value.lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare rates.json with the models.dev catalog at a pinned commit."
    )
    parser.add_argument(
        "--commit",
        type=_parse_commit,
        help="Full 40-character commit SHA of the models.dev repository to read; recorded "
        "as provenance. Branches, tags and abbreviated SHAs are refused.",
    )
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        help="Read a local checkout (or vendored slice) laid out like the repository instead "
        "of fetching. With --commit, the commit is recorded as provenance only; --write "
        "still needs it.",
    )
    parser.add_argument("--rates", type=Path, default=DEFAULT_RATES, help="Rate table to compare.")
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--repo", default=DEFAULT_REPO, help="GitHub owner/name of the catalog.")
    parser.add_argument(
        "--write", action="store_true", help="Apply additions and changes to the rate table."
    )
    parser.add_argument(
        "--today",
        type=_parse_today,
        default=datetime.now(timezone.utc).date(),
        help="Date stamped on a refreshed table (default: today, UTC).",
    )
    args = parser.parse_args(argv)

    if args.commit is None and args.catalog_dir is None:
        parser.error("one of --commit or --catalog-dir is required")
    if args.write and args.commit is None:
        parser.error("--write needs --commit: a refreshed table records the exact catalog commit")

    try:
        table = RateTable.load(args.rates)
        payload = json.loads(args.rates.read_text(encoding="utf-8"))
        if args.catalog_dir is not None:
            catalog = load_catalog_from_dir(args.catalog_dir, args.provider)
            source_label = f"{args.catalog_dir} ({args.provider})"
        else:
            token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            catalog = fetch_catalog_from_commit(args.repo, args.commit, args.provider, token)
            source_label = f"{args.repo}@{args.commit[:12]} ({args.provider})"
    except (
        RateTableError, CatalogError, OSError, json.JSONDecodeError, UnicodeDecodeError
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_ERROR

    default_read = Decimal(str(payload["cache_multipliers"]["read"]))
    diff = compare(payload, table, catalog)
    print(render(diff, payload, source_label, default_read))

    if not args.write:
        if diff.has_changes:
            print("not written; pass --write to apply", file=sys.stderr)
            return EXIT_DRIFT
        return EXIT_AGREES

    provenance = {
        "source": f"https://github.com/{args.repo}",
        "provider": args.provider,
        "commit": args.commit,
        "checked": args.today.isoformat(),
    }
    moved = apply(payload, diff, args.today, provenance)
    try:
        # The merged table must load through the same validator the report
        # uses; a refresh that produced an unloadable table is worse than none.
        RateTable(payload)
    except RateTableError as error:
        print(f"error: refreshed table would not load: {error}", file=sys.stderr)
        return EXIT_ERROR
    args.rates.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {args.rates}"
        + (f" (rate_table_version {payload['rate_table_version']})" if moved else " (provenance only)")
    )
    return EXIT_AGREES


if __name__ == "__main__":
    sys.exit(main())
