"""Report local agent token usage and what it would cost at API list rates.

A subscription bills a flat fee, so the work done under it has no per-request
price attached. The tokens are recorded locally anyway, and published list rates
are a matter of record, so the two can be combined into a single number: what
this period's usage would have cost had it been billed per token through the
API. That number is an equivalence, not a bill — see SKILL.md.

Exit codes:
    0  report produced, every model priced
    2  usage, configuration, or rate-table error
    3  report produced, but at least one model id had no rate (tokens counted,
       cost omitted for those rows and the total marked incomplete)
    4  the requested runtime has no readable local usage source
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from decimal import Decimal, DecimalException
from pathlib import Path

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Imported after the path insert above so the skill directory can be copied and
# run from anywhere without installing anything.
from runtime_adapters import (
    CLAUDE_CODE,
    CODEX,
    Bound,
    CollectionResult,
    SystemLocal,
    RuntimeUnavailable,
    UnknownTimezone,
    UsageRecord,
    available_runtimes,
    current_day,
    get_adapter,
    resolve_timezone,
    timezone_label,
)

EXIT_OK = 0
EXIT_ERROR = 2
EXIT_INCOMPLETE = 3
EXIT_RUNTIME_UNAVAILABLE = 4

DEFAULT_RATES = Path(__file__).resolve().parent / "rates.json"
PER_MILLION = Decimal(1_000_000)
CENTS = Decimal("0.01")
MICRO = Decimal("0.000001")
CACHE_TIERS = ("read", "write_5m", "write_1h")
# Server tools bill per call, so their rates cannot ride the per-million-token
# model rows. Only one unit is supported, and it is named in the table rather
# than assumed, so a future per-hour tool cannot be silently read as per-call.
SERVER_TOOL_UNIT = "per_request"
# Tools whose request counts the adapters record. A tool counted here with no
# rate row is reported as unpriced rather than dropped.
COUNTED_SERVER_TOOLS = ("web_search",)


class RateTableError(Exception):
    pass


def _rate(field_name: str, value: object) -> Decimal:
    """Parse one rate-table number, or fail with the documented error.

    Every numeric value in the table goes through here. A malformed one used to
    escape as an uncaught `decimal.InvalidOperation` — a traceback and exit 1 —
    rather than the controlled rate-table error the exit codes promise. NaN and
    infinity parse happily as Decimals and would poison every total downstream,
    so they are rejected too, along with negatives.
    """
    try:
        parsed = Decimal(str(value))
    except DecimalException:
        raise RateTableError(f"{field_name} is not a number: {value!r}") from None
    if not parsed.is_finite():
        raise RateTableError(f"{field_name} must be a finite number, got {value!r}")
    if parsed < 0:
        raise RateTableError(f"{field_name} must not be negative, got {value!r}")
    return parsed


@dataclass(frozen=True)
class ModelRates:
    display_name: str
    input: Decimal
    output: Decimal
    cache_read: Decimal
    cache_write_5m: Decimal
    cache_write_1h: Decimal


@dataclass(frozen=True)
class ServerToolRates:
    display_name: str
    per_request: Decimal


class RateTable:
    """Model-id to per-million-token rates, loaded from a versioned data file."""

    def __init__(self, payload: dict) -> None:
        try:
            self.version = str(payload["rate_table_version"])
            self.currency = str(payload["currency"])
            multipliers = payload["cache_multipliers"]
            self._multipliers = {
                tier: _rate(f"cache_multipliers.{tier}", multipliers[tier])
                for tier in CACHE_TIERS
            }
            models = payload["models"]
        except (KeyError, TypeError) as error:
            raise RateTableError(f"rate table is missing required data: {error}") from None
        if not isinstance(models, dict):
            raise RateTableError("rate table 'models' must be an object")
        self._models = models
        non_billable = payload.get("non_billable_models", [])
        if not isinstance(non_billable, list) or not all(
            isinstance(name, str) for name in non_billable
        ):
            raise RateTableError(
                "rate table 'non_billable_models' must be a list of model id strings"
            )
        self.non_billable = frozenset(non_billable)
        self.server_tools = self._load_server_tools(payload.get("server_tools"))
        # Every entry is validated now, not when a lookup happens to reach it.
        # A model *listed* in the table with a corrupt or missing rate is a
        # configuration error (exit 2), and it is a different situation from a
        # model id the table does not know (exit 3): deferring the check let
        # the former surface mid-report as an uncaught traceback, or worse,
        # read as merely "unpriced".
        for model_id, entry in models.items():
            self._validate_model(str(model_id), entry)

    @staticmethod
    def _load_server_tools(block: object) -> dict[str, ServerToolRates]:
        """Per-request rates for server-side tools, keyed by tool name.

        Validated eagerly for the same reason the model rows are: a tool
        *listed* in the table with a corrupt rate is a configuration error
        (exit 2), and that is a different situation from a tool the table does
        not price (exit 3, counted and marked). Deferring the check would let
        the former read as merely unpriced, which is the one reading that
        silently understates a cost.
        """
        if block is None:
            return {}
        if not isinstance(block, dict):
            raise RateTableError(
                "rate table 'server_tools' must be an object keyed by tool name"
            )
        tools: dict[str, ServerToolRates] = {}
        for raw_name, entry in block.items():
            name = f"server_tools.{raw_name}"
            if not isinstance(entry, dict):
                raise RateTableError(f"{name} must be an object with 'unit' and 'rate'")
            unit = entry.get("unit")
            if unit != SERVER_TOOL_UNIT:
                raise RateTableError(
                    f"{name}.unit must be {SERVER_TOOL_UNIT!r} - server tools bill per "
                    f"call, not per token; got {unit!r}"
                )
            if "rate" not in entry:
                raise RateTableError(f"{name} is missing the required 'rate'")
            display = entry.get("display_name", raw_name)
            if not isinstance(display, str) or not display.strip():
                raise RateTableError(f"{name}.display_name must be a non-empty string")
            tools[str(raw_name)] = ServerToolRates(
                display_name=display,
                per_request=_rate(f"{name}.rate", entry["rate"]),
            )
        return tools

    @staticmethod
    def _validate_rates(name: str, rates: dict) -> None:
        for field_name in ("input", "output"):
            if field_name not in rates:
                raise RateTableError(f"{name} is missing the required {field_name!r} rate")
            _rate(f"{name}.{field_name}", rates[field_name])

    @staticmethod
    def _validate_multipliers(name: str, entry: dict) -> None:
        """Check a per-model `cache_multipliers` block, when one is present.

        The table's multipliers are the default; an entry may override any tier
        for itself. The override is validated like every other rate: a table
        that lists a model with a corrupt override is a configuration error,
        not a model that quietly prices at the default.
        """
        overrides = entry.get("cache_multipliers")
        if overrides is None:
            return
        if not isinstance(overrides, dict):
            raise RateTableError(
                f"{name}.cache_multipliers must be an object with any of "
                f"{', '.join(CACHE_TIERS)}"
            )
        for tier, value in overrides.items():
            if tier not in CACHE_TIERS:
                raise RateTableError(
                    f"{name}.cache_multipliers.{tier} is not a cache tier; expected one of "
                    f"{', '.join(CACHE_TIERS)}"
                )
            _rate(f"{name}.cache_multipliers.{tier}", value)

    @classmethod
    def _validate_model(cls, model_id: str, entry: object) -> None:
        name = f"models.{model_id}"
        if not isinstance(entry, dict):
            raise RateTableError(f"{name} must be an object with 'input' and 'output' rates")
        cls._validate_rates(name, entry)
        cls._validate_multipliers(name, entry)
        speeds = entry.get("speeds")
        if speeds is None:
            return
        if not isinstance(speeds, dict):
            raise RateTableError(f"{name}.speeds must be an object")
        for variant, variant_entry in speeds.items():
            variant_name = f"{name}.speeds.{variant}"
            if not isinstance(variant_entry, dict):
                raise RateTableError(f"{variant_name} must be an object")
            # The variant is validated on its own, not merged over the base
            # entry first: a variant exists because it prices differently, so
            # one missing its rates silently inheriting the standard price is
            # exactly the wrong-rate confident figure this table refuses to
            # produce.
            cls._validate_rates(variant_name, variant_entry)
            cls._validate_multipliers(variant_name, variant_entry)

    @classmethod
    def load(cls, path: Path) -> RateTable:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise RateTableError(f"cannot read rate table {path}: {error}") from None
        except json.JSONDecodeError as error:
            raise RateTableError(f"rate table {path} is not valid JSON: {error}") from None
        if not isinstance(payload, dict):
            raise RateTableError(f"rate table {path} must contain a JSON object")
        return cls(payload)

    def lookup(self, model_id: str) -> ModelRates | None:
        """Resolve a model id to its rates, or None when the id is unknown.

        Resolution is deliberately narrow: an exact match, then the same id with
        a trailing release-date suffix removed. Nothing is matched by prefix or
        similarity, because a near-miss would produce a confident cost figure at
        the wrong rate — worse than reporting the id as unpriced.
        """
        base, _, variant = model_id.partition("#")
        entry = self._models.get(base)
        if entry is None:
            trimmed = _strip_date_suffix(base)
            if trimmed != base:
                entry = self._models.get(trimmed)
                base = trimmed
        if not isinstance(entry, dict):
            return None

        rates = entry
        layers: list[object] = [entry.get("cache_multipliers")]
        if variant:
            speeds = entry.get("speeds")
            variant_rates = speeds.get(variant) if isinstance(speeds, dict) else None
            if not isinstance(variant_rates, dict):
                return None
            rates = {**entry, **variant_rates}
            layers.append(variant_rates.get("cache_multipliers"))

        # Loading validated every listed entry, so a known model's rates are
        # guaranteed present and parseable here.
        input_rate = _rate(f"models.{base}.input", rates["input"])
        output_rate = _rate(f"models.{base}.output", rates["output"])

        # Cache tiers are multiples of the input rate. The table's multipliers
        # apply unless the model — or, on top of that, the variant — overrides
        # a tier for itself; each layer overrides only the tiers it names.
        multipliers = dict(self._multipliers)
        for layer in layers:
            if isinstance(layer, dict):
                for tier, value in layer.items():
                    multipliers[tier] = _rate(f"models.{base}.cache_multipliers.{tier}", value)

        display = str(entry.get("display_name", base))
        if variant:
            display = f"{display} ({variant})"
        return ModelRates(
            display_name=display,
            input=input_rate,
            output=output_rate,
            cache_read=input_rate * multipliers["read"],
            cache_write_5m=input_rate * multipliers["write_5m"],
            cache_write_1h=input_rate * multipliers["write_1h"],
        )


def _strip_date_suffix(model_id: str) -> str:
    head, sep, tail = model_id.rpartition("-")
    if sep and len(tail) == 8 and tail.isdigit():
        return head
    return model_id


@dataclass
class ModelTotals:
    model_id: str
    display_name: str
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_5m_tokens: int = 0
    cache_write_1h_tokens: int = 0
    web_search_requests: int = 0
    cost: Decimal | None = None
    priced: bool = True
    billable: bool = True

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_5m_tokens
            + self.cache_write_1h_tokens
        )


@dataclass
class ServerToolTotals:
    """One server-side tool's recorded calls, and their cost if priced.

    A server tool contributes calls and money but no tokens, so it is kept
    apart from the model rows rather than folded into one: adding a per-call
    charge to a token count would make the token column stop meaning tokens.
    """

    tool: str
    display_name: str
    requests: int = 0
    cost: Decimal | None = None
    priced: bool = True


@dataclass
class SummaryRow:
    """One at-a-glance period: today, yesterday, this week."""

    key: str
    label: str
    since: str
    until: str
    requests: int
    tokens: int
    cost: Decimal
    complete: bool


@dataclass
class Report:
    runtime: str
    rate_table_version: str
    currency: str
    models: list[ModelTotals] = field(default_factory=list)
    total_cost: Decimal = Decimal(0)
    complete: bool = True
    unpriced_models: list[str] = field(default_factory=list)
    non_billable_models: list[str] = field(default_factory=list)
    collection: CollectionResult | None = None
    since: str | None = None
    until: str | None = None
    timezone: str = "UTC"
    summary: list[SummaryRow] = field(default_factory=list)
    server_tools: list[ServerToolTotals] = field(default_factory=list)
    unpriced_server_tools: list[str] = field(default_factory=list)
    # Set only when at least one window edge was given as an instant. The
    # header then reports the window as resolved instants, because that is the
    # question a sub-day window asks; a date-only window keeps reporting the
    # days it was given.
    window_by_instant: bool = False
    window_since_instant: str | None = None
    window_until_instant: str | None = None

    @property
    def total_tokens(self) -> int:
        return sum(model.total_tokens for model in self.models)

    @property
    def total_requests(self) -> int:
        return sum(model.requests for model in self.models)

    @property
    def priced_tokens(self) -> int:
        """Tokens the cost figure actually covers.

        The token total counts everything observed, including rows that are
        unpriced or non-billable; the cost total covers only priced rows. When
        the two scopes differ the reader is told so explicitly, because a token
        count and a cost sitting on one line imply the cost is the price of
        those tokens.
        """
        return sum(
            model.total_tokens
            for model in self.models
            if model.billable and model.priced
        )

    @property
    def priced_requests(self) -> int:
        return sum(
            model.requests for model in self.models if model.billable and model.priced
        )

    @property
    def total_web_search_requests(self) -> int:
        return sum(model.web_search_requests for model in self.models)

    @property
    def total_server_tool_requests(self) -> int:
        return sum(tool.requests for tool in self.server_tools)

    @property
    def total_server_tool_cost(self) -> Decimal:
        return sum(
            (tool.cost for tool in self.server_tools if tool.cost is not None),
            Decimal(0),
        )


def build_report(
    records: Iterable[UsageRecord],
    rates: RateTable,
    runtime: str,
    collection: CollectionResult | None = None,
    since: str | None = None,
    until: str | None = None,
) -> Report:
    report = Report(
        runtime=runtime,
        rate_table_version=rates.version,
        currency=rates.currency,
        collection=collection,
        since=since,
        until=until,
    )
    totals: dict[str, ModelTotals] = {}
    resolved: dict[str, ModelRates | None] = {}

    for record in records:
        entry = totals.get(record.model)
        if entry is None:
            billable = record.model not in rates.non_billable
            model_rates = rates.lookup(record.model) if billable else None
            resolved[record.model] = model_rates
            entry = ModelTotals(
                model_id=record.model,
                display_name=(
                    model_rates.display_name if model_rates is not None else record.model
                ),
                priced=model_rates is not None,
                billable=billable,
                cost=Decimal(0) if model_rates is not None else None,
            )
            totals[record.model] = entry

        entry.requests += 1
        entry.input_tokens += record.input_tokens
        entry.output_tokens += record.output_tokens
        entry.cache_read_tokens += record.cache_read_tokens
        entry.cache_write_5m_tokens += record.cache_write_5m_tokens
        entry.cache_write_1h_tokens += record.cache_write_1h_tokens
        entry.web_search_requests += record.web_search_requests

        model_rates = resolved[record.model]
        if model_rates is not None:
            assert entry.cost is not None
            entry.cost += _cost_of(record, model_rates)

    for entry in totals.values():
        if not entry.billable:
            report.non_billable_models.append(entry.model_id)
        elif not entry.priced:
            report.complete = False
            report.unpriced_models.append(entry.model_id)
        else:
            assert entry.cost is not None
            entry.cost = entry.cost.quantize(MICRO)
            report.total_cost += entry.cost

    report.models = sorted(
        totals.values(), key=lambda item: (-item.total_tokens, item.model_id)
    )
    _attach_server_tools(report, rates)
    report.total_cost = report.total_cost.quantize(MICRO)
    return report


def _attach_server_tools(report: Report, rates: RateTable) -> None:
    """Price the server-side tool calls the records carry.

    A tool with recorded calls and no rate row behaves exactly like an
    unpriced model: its calls stay visible in the table and the run reports
    itself incomplete, rather than the calls vanishing or being valued at
    zero. A tool with no calls produces no row at all, so a report that never
    touched one is unchanged.
    """
    counted = {"web_search": report.total_web_search_requests}
    for tool in COUNTED_SERVER_TOOLS:
        requests = counted.get(tool, 0)
        if not requests:
            continue
        tool_rates = rates.server_tools.get(tool)
        entry = ServerToolTotals(
            tool=tool,
            display_name=tool_rates.display_name if tool_rates is not None else tool,
            requests=requests,
            priced=tool_rates is not None,
            cost=(
                (tool_rates.per_request * requests).quantize(MICRO)
                if tool_rates is not None
                else None
            ),
        )
        report.server_tools.append(entry)
        if entry.cost is None:
            report.complete = False
            report.unpriced_server_tools.append(tool)
        else:
            report.total_cost += entry.cost


def _cost_of(record: UsageRecord, rates: ModelRates) -> Decimal:
    return (
        Decimal(record.input_tokens) * rates.input
        + Decimal(record.output_tokens) * rates.output
        + Decimal(record.cache_read_tokens) * rates.cache_read
        + Decimal(record.cache_write_5m_tokens) * rates.cache_write_5m
        + Decimal(record.cache_write_1h_tokens) * rates.cache_write_1h
    ) / PER_MILLION


def _money(value: Decimal) -> str:
    return f"{value.quantize(CENTS):,}"


def _thousands(value: int) -> str:
    return f"{value:,}"


def _abbrev(value: int) -> str:
    """Round a token count to B/M/K for the at-a-glance block.

    The detail table below keeps exact counts — that is the checkable artifact.
    Up here the question is 'how much', not 'exactly how many'.
    """
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if abs(value) >= limit:
            return f"{value / limit:.1f}{suffix}"
    return str(value)


def summary_periods(today: date) -> list[tuple[str, str, str, str]]:
    """The three at-a-glance windows, as (key, label, since, until).

    The week runs Monday to today rather than as a rolling seven days, so the
    figure matches what a person means by 'this week'. On a Monday it therefore
    equals today, which is correct rather than a bug.
    """
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    return [
        ("today", f"Today ({today.isoformat()})", today.isoformat(), today.isoformat()),
        (
            "yesterday",
            f"Yesterday ({yesterday.isoformat()})",
            yesterday.isoformat(),
            yesterday.isoformat(),
        ),
        (
            "this_week",
            f"This week (from {week_start.isoformat()})",
            week_start.isoformat(),
            today.isoformat(),
        ),
    ]


def build_summary(
    records: Sequence[UsageRecord],
    rates: RateTable,
    runtime: str,
    today: date,
) -> list[SummaryRow]:
    rows: list[SummaryRow] = []
    for key, label, since, until in summary_periods(today):
        subset = [
            record
            for record in records
            if record.day is not None and since <= record.day <= until
        ]
        period = build_report(subset, rates, runtime)
        rows.append(
            SummaryRow(
                key=key,
                label=label,
                since=since,
                until=until,
                requests=period.total_requests,
                tokens=period.total_tokens,
                cost=period.total_cost,
                complete=period.complete,
            )
        )
    return rows


def render_table(report: Report) -> str:
    window = "all recorded usage"
    if report.window_by_instant:
        # A sub-day window is asked precisely, so it is answered precisely:
        # the instants the figure actually covers, rather than the days they
        # fall in. The upper edge is exclusive and says so.
        start = report.window_since_instant or "earliest"
        end = report.window_until_instant
        window = f"{start} to {end} (exclusive)" if end else f"{start} to latest"
    elif report.since or report.until:
        window = f"{report.since or 'earliest'} to {report.until or 'latest'}"

    lines = [
        f"Runtime: {report.runtime}    Window: {window}",
        (
            f"Rate table: {report.rate_table_version} ({report.currency}, list rates)"
            f"    Days: {report.timezone}"
        ),
        "",
    ]

    meter = report.collection.rate_limits if report.collection is not None else None
    if meter is not None:
        window_name = "weekly" if meter.window_minutes == 10080 else "rolling"
        lines.extend([
            f"Codex account rate limit — server snapshot at {meter.timestamp}",
            f"Used: {meter.used_percent}%    Window: {meter.window_minutes:,} minutes ({window_name})",
            f"Resets: {meter.resets_at_local} ({report.timezone})",
            "This server figure is not derived from the local token total.",
            "",
        ])

    if report.summary:
        summary_header = f"{'Period':<30}{'Requests':>10}{'Tokens':>12}{'Cost':>14}"
        lines.append(summary_header)
        lines.append("-" * len(summary_header))
        for row in report.summary:
            marker = "" if row.complete else " *"
            lines.append(
                f"{row.label[:28]:<30}"
                f"{_thousands(row.requests):>10}"
                f"{_abbrev(row.tokens):>12}"
                f"{_money(row.cost) + marker:>14}"
            )
        if any(not row.complete for row in report.summary):
            lines.append("")
            lines.append("* partial: this period contains a model with no published rate.")
        lines.append("")
        lines.append("By model, over the full window:")
        lines.append("")

    header = f"{'Model':<34}{'Requests':>10}{'Tokens':>16}{'Cost':>14}"
    lines.append(header)
    lines.append("-" * len(header))
    for model in report.models:
        if not model.billable:
            cost = "not billable"
        elif model.priced:
            assert model.cost is not None
            cost = _money(model.cost)
        else:
            cost = "UNPRICED"
        lines.append(
            f"{model.display_name[:32]:<34}"
            f"{_thousands(model.requests):>10}"
            f"{_thousands(model.total_tokens):>16}"
            f"{cost:>14}"
        )
    for tool in report.server_tools:
        if tool.priced:
            assert tool.cost is not None
            tool_cost = _money(tool.cost)
        else:
            tool_cost = "UNPRICED"
        lines.append(
            f"{tool.display_name[:32]:<34}"
            f"{_thousands(tool.requests):>10}"
            # A server tool bills per call and carries no tokens of its own, so
            # the token cell is empty rather than zero: zero would read as a
            # measured count.
            f"{'-':>16}"
            f"{tool_cost:>14}"
        )
    lines.append("-" * len(header))
    lines.append(
        f"{'Total':<34}"
        f"{_thousands(report.total_requests + report.total_server_tool_requests):>10}"
        f"{_thousands(report.total_tokens):>16}"
        f"{_money(report.total_cost):>14}"
    )
    lines.append("")

    if report.priced_tokens != report.total_tokens:
        lines.append(
            f"Cost covers {_thousands(report.priced_tokens)} of the "
            f"{_thousands(report.total_tokens)} token(s) shown. The rest belong to "
            "rows marked UNPRICED or not billable, which are counted in the token "
            "total but carry no cost."
        )
        lines.append("")
    if report.unpriced_models:
        lines.append(
            "INCOMPLETE: no rate is published in the rate table for "
            f"{', '.join(sorted(report.unpriced_models))}. Their tokens appear in the "
            "token total above but are excluded from the cost total. Add the model "
            "to rates.json to price them."
        )
        lines.append("")
    if report.non_billable_models:
        lines.append(
            "Excluded as non-billable: "
            f"{', '.join(sorted(report.non_billable_models))}."
        )
        lines.append("")
    if any(tool.priced for tool in report.server_tools):
        lines.append(
            "Server-tool rows are billed per call rather than per token. Their "
            "calls and cost are included in the totals above; they contribute no "
            "tokens, so the token total is unchanged by them."
        )
        lines.append("")
    if report.unpriced_server_tools:
        lines.append(
            "INCOMPLETE: no per-call rate is published in the rate table for "
            f"{', '.join(sorted(report.unpriced_server_tools))}. Those calls are "
            "counted above but excluded from the cost total. Add the tool to the "
            "server_tools block in rates.json to price them."
        )
        lines.append("")

    collection = report.collection
    if collection is not None:
        if report.runtime == CODEX:
            lines.append(
                f"Read {_thousands(collection.files_scanned)} rollout file(s); "
                f"ignored {_thousands(collection.duplicate_records)} repeated cumulative snapshot(s); "
                f"started {_thousands(collection.counter_resets)} new counter segment(s)."
            )
        else:
            lines.append(
                f"Read {_thousands(collection.files_scanned)} transcript file(s); "
                f"merged {_thousands(collection.duplicate_records)} repeated record(s) "
                "into their requests."
            )
        if collection.invalid_rate_limits:
            lines.append(f"Ignored {collection.invalid_rate_limits} malformed rate-limit snapshot(s).")
        if collection.unstamped_rate_limits:
            lines.append(
                f"Ignored {collection.unstamped_rate_limits} rate-limit snapshot(s) "
                "with missing or unparseable timestamps."
            )
        if collection.reconciled_records:
            lines.append(
                f"{_thousands(collection.reconciled_records)} request(s) had copies "
                "whose counts differed; the most complete snapshot of each was used."
            )
        if collection.filtered_out:
            lines.append(
                f"{_thousands(collection.filtered_out)} request(s) fell outside the window."
            )
        if collection.partial_cache_splits:
            lines.append(
                f"{_thousands(collection.partial_cache_splits)} record(s) reported a "
                "cache-lifetime split that did not add up to their cache-write total; "
                "the remainder was carried at the 5-minute rate."
            )
        if collection.malformed_lines or collection.unreadable_files:
            lines.append(
                f"Ignored {_thousands(collection.malformed_lines)} unparseable line(s) "
                f"and {_thousands(collection.unreadable_files)} unreadable file(s)."
            )
        if collection.invalid_records:
            lines.append(
                f"Skipped {_thousands(collection.invalid_records)} record(s) carrying "
                "negative or malformed token counts."
            )
        if collection.cache_split_conflicts:
            lines.append(
                f"{_thousands(collection.cache_split_conflicts)} record(s) reported a "
                "cache-lifetime split larger than their flat cache-write total; the "
                "split — the more specific figure — was used."
            )
        if collection.conflicting_records:
            lines.append(
                f"Dropped {_thousands(collection.conflicting_records)} request(s) "
                "whose copies disagreed on what to price — token-class composition, "
                "model, or speed — at equal magnitude with nothing to order them; "
                "pricing those by read order would be arbitrary."
            )
        if collection.unidentified_records:
            lines.append(
                f"{_thousands(collection.unidentified_records)} record(s) carried no "
                "request identity and were counted individually rather than merged."
            )

    return "\n".join(lines).rstrip() + "\n"


def _window_payload(report: Report) -> dict:
    """The window block: what was asked for, plus what it resolved to.

    `resolved` appears only for an instant window. A date-only window resolves
    to the days it already names, so adding the key there would be noise in
    every existing consumer's payload for no new information.
    """
    payload: dict = {"since": report.since, "until": report.until}
    if report.window_by_instant:
        payload["resolved"] = {
            "since": report.window_since_instant,
            "until": report.window_until_instant,
            "until_exclusive": True,
        }
    return payload


def render_json(report: Report) -> str:
    payload = {
        "runtime": report.runtime,
        "rate_table_version": report.rate_table_version,
        "currency": report.currency,
        "window": _window_payload(report),
        "timezone": report.timezone,
        "summary": [
            {
                "key": row.key,
                "label": row.label,
                "since": row.since,
                "until": row.until,
                "requests": row.requests,
                "tokens": row.tokens,
                "tokens_display": _abbrev(row.tokens),
                "cost": str(row.cost),
                "complete": row.complete,
            }
            for row in report.summary
        ],
        "complete": report.complete,
        "unpriced_models": sorted(report.unpriced_models),
        "non_billable_models": sorted(report.non_billable_models),
        "totals": {
            "requests": report.total_requests,
            "tokens": report.total_tokens,
            "priced_requests": report.priced_requests,
            "priced_tokens": report.priced_tokens,
            "cost": str(report.total_cost),
            "web_search_requests": report.total_web_search_requests,
        },
        "models": [
            {
                "model_id": model.model_id,
                "display_name": model.display_name,
                "priced": model.priced,
                "billable": model.billable,
                "requests": model.requests,
                "tokens": {
                    "input": model.input_tokens,
                    "output": model.output_tokens,
                    "cache_read": model.cache_read_tokens,
                    "cache_write_5m": model.cache_write_5m_tokens,
                    "cache_write_1h": model.cache_write_1h_tokens,
                    "total": model.total_tokens,
                },
                "web_search_requests": model.web_search_requests,
                "cost": None if model.cost is None else str(model.cost),
            }
            for model in report.models
        ],
    }
    if report.server_tools:
        payload["server_tools"] = [
            {
                "tool": tool.tool,
                "display_name": tool.display_name,
                "priced": tool.priced,
                "requests": tool.requests,
                "cost": None if tool.cost is None else str(tool.cost),
            }
            for tool in report.server_tools
        ]
        payload["unpriced_server_tools"] = sorted(report.unpriced_server_tools)
        payload["totals"]["server_tool_requests"] = report.total_server_tool_requests
        payload["totals"]["server_tool_cost"] = str(report.total_server_tool_cost)
    if report.collection is not None:
        payload["collection"] = {
            "files_scanned": report.collection.files_scanned,
            "unreadable_files": report.collection.unreadable_files,
            "malformed_lines": report.collection.malformed_lines,
            "duplicate_records": report.collection.duplicate_records,
            "reconciled_records": report.collection.reconciled_records,
            "invalid_records": report.collection.invalid_records,
            "unidentified_records": report.collection.unidentified_records,
            "partial_cache_splits": report.collection.partial_cache_splits,
            "cache_split_conflicts": report.collection.cache_split_conflicts,
            "conflicting_records": report.collection.conflicting_records,
            "filtered_out": report.collection.filtered_out,
        }
        if report.runtime == CODEX:
            payload["collection"]["counter_resets"] = report.collection.counter_resets
            payload["collection"]["invalid_rate_limits"] = report.collection.invalid_rate_limits
            payload["collection"]["unstamped_rate_limits"] = report.collection.unstamped_rate_limits
        meter = report.collection.rate_limits
        if meter is not None:
            payload["rate_limits"] = {
                "source": "server_snapshot",
                "timestamp": meter.timestamp,
                "primary": {
                    "used_percent": meter.used_percent,
                    "window_minutes": meter.window_minutes,
                    "resets_at": meter.resets_at,
                    "resets_at_local": meter.resets_at_local,
                },
            }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _valid_day(value: str) -> str:
    """Accept only a real calendar day in canonical YYYY-MM-DD form.

    The shape check alone let `2026-99-99` and `2026-02-30` through, and days
    are compared as strings, so an impossible date produced a confident empty
    report rather than an error. The shape check still runs first, because
    `date.fromisoformat` also accepts forms this tool does not use.
    """
    parts = value.split("-")
    if len(parts) != 3 or [len(part) for part in parts] != [4, 2, 2]:
        raise argparse.ArgumentTypeError(f"expected a YYYY-MM-DD date, got {value!r}")
    if not all(part.isdigit() for part in parts):
        raise argparse.ArgumentTypeError(f"expected a YYYY-MM-DD date, got {value!r}")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a real calendar date: {value!r}") from None
    return value


def _parse_edge_datetime(value: str) -> datetime | None:
    """An ISO 8601 datetime as written, naive or aware, or None.

    Deliberately not `runtime_adapters._parse_instant`: that one reads a naive
    record timestamp as UTC, which is right for a record written by a machine
    and wrong for a window edge typed by a person, who means their own clock.
    Whether an offset was written is the thing that must survive here.
    """
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _valid_window_edge(value: str) -> str:
    """Accept a calendar day or an ISO 8601 datetime, returned verbatim.

    The two forms mean different things — a whole day in the report zone
    versus one instant — so which was given has to survive argument parsing.
    The text is interpreted once the zone is known, because a naive datetime
    means the reader's own clock and `--tz` is what says which clock that is.
    """
    if "T" not in value and " " not in value:
        return _valid_day(value)
    moment = _parse_edge_datetime(value)
    if moment is None:
        raise argparse.ArgumentTypeError(
            "expected YYYY-MM-DD or an ISO 8601 datetime such as "
            f"2026-08-11T17:20 or 2026-08-11T17:20:00-07:00, got {value!r}"
        )
    return value


def _attach_zone(naive: datetime, zone: tzinfo | SystemLocal) -> datetime:
    """Read a naive wall time in the report zone."""
    if isinstance(zone, SystemLocal):
        # No IANA name was discoverable, so let the platform apply its own
        # historical rules: `astimezone` on a naive datetime reads it as local
        # wall time and attaches that date's offset, not today's.
        return naive.astimezone()
    return naive.replace(tzinfo=zone)


def _resolve_bound(text: str | None, zone: tzinfo | SystemLocal) -> Bound | None:
    """One `--since`/`--until` value as the window edge it denotes."""
    if text is None:
        return None
    if "T" not in text and " " not in text:
        return Bound(text=text, day=text)
    moment = _parse_edge_datetime(text)
    assert moment is not None  # argparse already accepted this shape
    if moment.tzinfo is None:
        moment = _attach_zone(moment, zone)
    return Bound(text=text, instant=moment)


def _edge_instant(
    bound: Bound | None, zone: tzinfo | SystemLocal, *, upper: bool
) -> datetime | None:
    """A window edge as one comparable instant, for ordering checks only.

    A day edge is half-open like an instant edge once resolved: `--since D`
    starts at D 00:00 and `--until D` ends at D+1 00:00, which is what makes
    "the whole of D" and "up to but not including D+1" the same window.
    """
    if bound is None:
        return None
    if bound.instant is not None:
        return bound.instant
    day = date.fromisoformat(bound.day)
    if upper:
        if day == date.max:
            # The last representable day has no next midnight to be exclusive
            # of. Ordering is the only thing this instant is used for, and
            # every instant that can exist falls on or before the end of that
            # day, so it carries the same meaning here without overflowing.
            return _attach_zone(datetime.combine(day, time.max), zone)
        day = day + timedelta(days=1)
    return _attach_zone(datetime(day.year, day.month, day.day), zone)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="usage_cost.py",
        description=(
            "Report locally recorded agent token usage and what that usage would "
            "cost at published API list rates."
        ),
    )
    parser.add_argument(
        "--runtime",
        default=CLAUDE_CODE,
        choices=available_runtimes(),
        help="which runtime's local records to read (default: %(default)s)",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        help="override the directory holding the runtime's records",
    )
    parser.add_argument(
        "--since",
        type=_valid_window_edge,
        help=(
            "start of the window: a day (YYYY-MM-DD, the whole day) or an ISO "
            "8601 datetime (inclusive instant). A datetime without an offset is "
            "read in --tz"
        ),
    )
    parser.add_argument(
        "--until",
        type=_valid_window_edge,
        help=(
            "end of the window: a day (YYYY-MM-DD, through the end of that day) "
            "or an ISO 8601 datetime, which is exclusive so adjacent windows do "
            "not both count the record on the boundary"
        ),
    )
    parser.add_argument(
        "--rates",
        type=Path,
        default=DEFAULT_RATES,
        help="path to the rate table (default: the table shipped with this skill)",
    )
    parser.add_argument(
        "--tz",
        default="local",
        help=(
            "zone whose calendar days usage is bucketed into: 'local' "
            "(default), 'UTC', or an IANA name such as America/Los_Angeles"
        ),
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output format (default: %(default)s)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        rates = RateTable.load(args.rates)
    except RateTableError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_ERROR

    try:
        zone = resolve_timezone(args.tz)
    except UnknownTimezone as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_ERROR

    # The window is resolved only now: a naive datetime edge means the
    # reader's own clock, and --tz is what says which clock that is.
    since = _resolve_bound(args.since, zone)
    until = _resolve_bound(args.until, zone)
    lower = _edge_instant(since, zone, upper=False)
    upper = _edge_instant(until, zone, upper=True)
    if lower is not None and upper is not None and lower >= upper:
        print(
            f"ERROR: --since {args.since} is after --until {args.until}.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    adapter = get_adapter(args.runtime)
    try:
        collection = adapter.collect(args.data_root, since, until, zone)
    except RuntimeUnavailable as error:
        print(
            f"ERROR: usage is unavailable for runtime {error.runtime!r}: {error.reason}",
            file=sys.stderr,
        )
        return EXIT_RUNTIME_UNAVAILABLE

    try:
        report = build_report(
            collection.records,
            rates,
            runtime=args.runtime,
            collection=collection,
            since=args.since,
            until=args.until,
        )
        if since is not None and since.instant is not None or (
            until is not None and until.instant is not None
        ):
            report.window_by_instant = True
            report.window_since_instant = lower.isoformat() if lower is not None else None
            report.window_until_instant = upper.isoformat() if upper is not None else None
        if args.since is None and args.until is None:
            # The at-a-glance block answers "how much lately". An explicit
            # window is already that question asked precisely, so the block
            # would only compete with the answer the caller asked for.
            report.summary = build_summary(
                collection.records,
                rates,
                args.runtime,
                current_day(zone),
            )
    except RateTableError as error:
        # Loading already validated every table entry, so this should be
        # unreachable — but a rate problem must never escape as a traceback,
        # whatever path it arrives by.
        print(f"ERROR: {error}", file=sys.stderr)
        return EXIT_ERROR
    report.timezone = timezone_label(zone)

    renderer = render_json if args.format == "json" else render_table
    sys.stdout.write(renderer(report))
    return EXIT_OK if report.complete else EXIT_INCOMPLETE


if __name__ == "__main__":
    raise SystemExit(main())
