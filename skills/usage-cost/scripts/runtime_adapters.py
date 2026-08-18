"""Runtime adapters that turn a coding agent's local records into usage rows.

Each runtime a subscription can be used through stores its own records in its own
place and its own format. An adapter hides that: it reports whether the runtime
can be read on this machine, and if so yields a normalized `UsageRecord` per
billable model request. Everything downstream — deduplication, pricing,
reporting — is runtime-agnostic.

Adding a runtime means adding one adapter and one registry entry. A runtime with
no readable local source is still registered, as an `UnavailableAdapter`, so the
command can say why rather than implying the runtime does not exist.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

CLAUDE_CODE = "claude-code"


class UnknownTimezone(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"unknown timezone {name!r}. Use an IANA name such as "
            "'America/Los_Angeles', or 'UTC', or 'local'."
        )
        self.name = name


class SystemLocal:
    """Marker for bucketing by the platform's own local rules.

    Used only when no IANA zone name can be discovered. Deliberately *not* a
    tzinfo: a correct tzinfo must keep `utcoffset`, `tzname`, `dst`, and
    `fromutc` mutually consistent for wall times on both sides of every
    transition, and a subtly wrong one silently shifts instants. Day bucketing
    needs none of that machinery — `time.localtime(stamp)` already applies the
    platform's historical rules per instant — so `day_of` calls it directly and
    no aware-datetime arithmetic ever happens in this zone.
    """

    def __repr__(self) -> str:
        return "SystemLocal()"


SYSTEM_LOCAL = SystemLocal()


def _local_zone_name() -> str | None:
    """The system's IANA zone name, or None when it cannot be determined.

    `datetime.now().astimezone().tzinfo` is deliberately not used: it freezes
    the offset in force *right now* into a fixed-offset zone with no transition
    table, so a summer machine buckets January records an hour off and can shift
    them across midnight. An IANA zone applies the correct historical offset per
    timestamp.
    """
    configured = os.environ.get("TZ")
    if configured:
        return configured
    localtime = Path("/etc/localtime")
    try:
        if localtime.is_symlink():
            target = localtime.resolve().as_posix()
            marker = "/zoneinfo/"
            if marker in target:
                return target.split(marker, 1)[1]
    except OSError:
        pass
    return None


def resolve_timezone(name: str | None) -> tzinfo | SystemLocal:
    """Resolve the zone whose calendar days the report buckets usage into.

    Records are stamped in UTC, but a day is a human unit: 'today' means the
    reader's today. Bucketing in UTC would file an evening's work in the US
    under tomorrow, so the local zone is the default and any other zone is
    explicit.
    """
    if name is None or name.lower() == "local":
        discovered = _local_zone_name()
        if discovered:
            try:
                return ZoneInfo(discovered)
            except (ZoneInfoNotFoundError, ValueError):
                pass
        # No IANA name to be found. Fall back to bucketing through the
        # platform's own per-instant rules, so a winter record keeps winter's
        # offset rather than today's.
        return SYSTEM_LOCAL
    if name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise UnknownTimezone(name) from None


def timezone_label(zone: tzinfo | SystemLocal) -> str:
    if isinstance(zone, ZoneInfo):
        return str(zone)
    if zone is timezone.utc:
        return "UTC"
    if isinstance(zone, SystemLocal):
        # No IANA name was discoverable. Near a transition even the current
        # abbreviation would be ambiguous, so the label claims exactly what is
        # known: days follow the platform's local rules.
        return "system local"
    name = datetime.now(zone).tzname() or "local"
    return f"{name} (fixed offset)"


def current_day(zone: tzinfo | SystemLocal) -> date:
    """Today's date in the report's bucketing zone."""
    if isinstance(zone, SystemLocal):
        parts = time.localtime()
        return date(parts.tm_year, parts.tm_mon, parts.tm_mday)
    return datetime.now(zone).date()


def _parse_instant(timestamp: object) -> datetime | None:
    """A record timestamp as an aware instant, or None when it is not one."""
    if not isinstance(timestamp, str) or len(timestamp) < 10:
        return None
    text = timestamp.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def day_of(timestamp: object, zone: tzinfo | SystemLocal) -> str | None:
    """The calendar day a record belongs to, in the requested zone."""
    if not isinstance(timestamp, str) or len(timestamp) < 10:
        return None
    moment = _parse_instant(timestamp)
    if moment is None:
        # Not a full ISO instant, so it cannot be shifted between zones. The
        # leading calendar date is used as written rather than dropped.
        return timestamp[:10]
    if isinstance(zone, SystemLocal):
        parts = time.localtime(moment.timestamp())
        return f"{parts.tm_year:04d}-{parts.tm_mon:02d}-{parts.tm_mday:02d}"
    return moment.astimezone(zone).date().isoformat()


@dataclass(frozen=True)
class UsageRecord:
    """One billable model request, normalized across runtimes."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    web_search_requests: int
    day: str | None
    dedup_key: tuple[str, ...]
    # Whether the cache-write lifetime allocation came from a split that
    # reconciled exactly, rather than being padded from a flat total. At equal
    # magnitudes an exact allocation outranks an approximated one.
    cache_split_exact: bool = True
    # The raw source timestamp, kept because it is the only evidence that can
    # order two copies whose token counts are identical but whose pricing
    # identity (model or speed) differs.
    timestamp: str | None = None


@dataclass
class CollectionResult:
    records: list[UsageRecord] = field(default_factory=list)
    files_scanned: int = 0
    unreadable_files: int = 0
    malformed_lines: int = 0
    duplicate_records: int = 0
    filtered_out: int = 0
    reconciled_records: int = 0
    invalid_records: int = 0
    unidentified_records: int = 0
    partial_cache_splits: int = 0
    cache_split_conflicts: int = 0
    conflicting_records: int = 0


@dataclass(frozen=True)
class Availability:
    supported: bool
    reason: str = ""


class RuntimeAdapter(Protocol):
    name: str

    def availability(self, data_root: Path | None) -> Availability: ...

    def resolve_data_root(self, data_root: Path | None) -> Path | None: ...

    def collect(
        self,
        data_root: Path | None,
        since: str | None,
        until: str | None,
        zone: tzinfo | SystemLocal,
    ) -> CollectionResult: ...


class UnavailableAdapter:
    """A runtime with no established local usage source.

    Returning an explicit unavailable state is the point: guessing a cost for a
    runtime whose records cannot be read would be worse than reporting nothing.
    """

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self._reason = reason

    def availability(self, data_root: Path | None) -> Availability:
        return Availability(supported=False, reason=self._reason)

    def resolve_data_root(self, data_root: Path | None) -> Path | None:
        return data_root

    def collect(
        self,
        data_root: Path | None,
        since: str | None,
        until: str | None,
        zone: tzinfo | SystemLocal,
    ) -> CollectionResult:
        raise RuntimeUnavailable(self.name, self._reason)


class RuntimeUnavailable(Exception):
    def __init__(self, runtime: str, reason: str) -> None:
        super().__init__(reason)
        self.runtime = runtime
        self.reason = reason


# Sentinel distinguishing a counter that is present but unusable from one that
# is simply absent. Only absence may contribute zero.
_MALFORMED = object()


def _count_of(container: object, key: str) -> int | object:
    """One raw counter: its value, 0 when absent, `_MALFORMED` when unusable.

    A counter that is present must be a non-negative integer. Mapping a present
    negative float, numeric string, boolean, or null to zero would make
    corruption indistinguishable from absence — the total quietly loses what
    the field claimed to hold. (Measured across 127,859 real usage blocks:
    every present counter is a non-negative integer, so strictness here cannot
    reject legitimate usage.)
    """
    if not isinstance(container, dict) or key not in container:
        return 0
    value = container[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return _MALFORMED
    return value


def _container_of(usage: dict, key: str) -> dict | None | object:
    """A nested counter container: its dict, None when absent, else `_MALFORMED`.

    Containers are held to the same present-versus-absent standard as the
    counters inside them: only an absent parent may make its counters absent.
    Reading a present scalar, list, or null parent as empty would silently
    erase whatever the container was supposed to hold — for `cache_creation`,
    the record's only cache-write evidence.
    """
    if key not in usage:
        return None
    value = usage[key]
    return value if isinstance(value, dict) else _MALFORMED


def _totals_of(record: UsageRecord) -> tuple[int, ...]:
    return (
        record.input_tokens,
        record.output_tokens,
        record.cache_read_tokens,
        record.cache_write_5m_tokens,
        record.cache_write_1h_tokens,
        record.web_search_requests,
    )


def _composition_of(totals: tuple[int, ...]) -> tuple[int, ...]:
    """Per-class counts with the two cache-write lifetimes combined."""
    input_tokens, output_tokens, cache_read, write_5m, write_1h, web_search = totals
    return (input_tokens, output_tokens, cache_read, write_5m + write_1h, web_search)


def _earlier_day(left: str | None, right: str | None) -> str | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)


@dataclass
class _RequestState:
    """Everything known about one logical request while its copies stream in.

    Copies of a streaming response grow toward the final counts, so the
    largest aggregate magnitude wins outright. Among copies tied at that
    magnitude, only one disagreement is decidable from content: an exact
    cache-lifetime split supersedes one padded from a flat total when model
    and every other class agree, because the padding is this adapter's own
    approximation artifact rather than a source claim. Every other
    disagreement between tied copies — token-class composition, model, or a
    speed variant — is a factual conflict about what to price, and a
    token-class ordering convention must not decide which price becomes fact.
    Those resolve only from source evidence: the uniquely latest usable
    timestamp among the contenders. `claims` therefore holds the copies tied
    at the maximal magnitude, keyed by what each claims the request to be,
    with the latest usable event time seen for that claim.
    """

    total: int
    first_totals: tuple[int, ...]
    claims: dict[tuple, tuple[datetime | None, UsageRecord]]

    def add(self, record: UsageRecord) -> None:
        totals = _totals_of(record)
        magnitude = sum(totals)
        if magnitude < self.total:
            return
        claim = (record.model, totals, record.cache_split_exact)
        instant = _parse_instant(record.timestamp)
        if magnitude > self.total:
            self.total = magnitude
            self.claims = {claim: (instant, record)}
            return
        known = self.claims.get(claim)
        if known is None or (
            instant is not None and (known[0] is None or instant > known[0])
        ):
            self.claims[claim] = (instant, record)

    def resolve(self) -> UsageRecord | None:
        """The canonical record, or None when no canonical choice exists.

        A single surviving claim is the ordinary case. With several, the
        uniquely latest-stamped claim is taken as the request's final state;
        when any contender lacks a usable timestamp or two claims share the
        latest one, there is no evidence to order them, and pricing the
        request by whichever copy was read first would be arbitrary — so no
        record is returned and the caller reports the drop. Resolution runs
        over the full retained set at the end, so the outcome is a function
        of the copies themselves, never of the order files were read in.
        """
        claims = self.claims
        if len(claims) > 1:
            dominated = [
                claim
                for claim in claims
                if not claim[2]
                and any(
                    other[2]
                    and other[0] == claim[0]
                    and _composition_of(other[1]) == _composition_of(claim[1])
                    for other in claims
                )
            ]
            if dominated:
                claims = {
                    claim: value
                    for claim, value in claims.items()
                    if claim not in dominated
                }
        if len(claims) == 1:
            return next(iter(claims.values()))[1]
        instants = [instant for instant, _ in claims.values()]
        if any(instant is None for instant in instants):
            return None
        latest = max(instants)
        winners = [record for instant, record in claims.values() if instant == latest]
        if len(winners) != 1:
            return None
        return winners[0]


class ClaudeCodeAdapter:
    """Read usage from Claude Code's per-session transcript files.

    Claude Code appends one JSON object per line to a transcript file per
    session. Assistant lines carry `message.model` and a `message.usage` block
    holding the same token counts the API returned for that request, which is
    what makes a local cost reconstruction possible at all.

    Two properties of the format drive the implementation:

    * The same request is written more than once, and **the copies are not
      identical**. A response is appended repeatedly as it streams, so earlier
      copies hold partial counts and later ones grow toward the final total.
      Summing every line overstates usage badly, while keeping the first copy
      understates it just as badly — on real transcripts the early snapshot of a
      diverging request can hold a small fraction of its final output tokens.
      Copies are therefore reconciled per request rather than deduplicated by
      first-seen: magnitude decides growth, and remaining disagreements resolve
      only from timestamp evidence or are dropped visibly (see `_RequestState`).
    * Cache creation is reported both as a flat total and, separately, split by
      cache lifetime. The split matters because the two lifetimes bill at
      different multiples of the input rate, so the split is preferred and the
      flat total is the fallback. When the two disagree in either direction the
      disagreement is counted rather than absorbed: a short split has its
      remainder carried at the cheaper tier, and a split exceeding the flat
      total — the aggregated multi-iteration record shape, where the legacy
      flat field lags — is used as stated.
    """

    name = CLAUDE_CODE

    def resolve_data_root(self, data_root: Path | None) -> Path | None:
        if data_root is not None:
            return data_root
        configured = os.environ.get("CLAUDE_CONFIG_DIR")
        base = Path(configured).expanduser() if configured else Path.home() / ".claude"
        return base / "projects"

    def availability(self, data_root: Path | None) -> Availability:
        root = self.resolve_data_root(data_root)
        if root is None or not root.is_dir():
            return Availability(
                supported=False,
                reason=(
                    f"no Claude Code transcript directory at {root}. Point --data-root "
                    "at the directory holding the .jsonl session transcripts, or set "
                    "CLAUDE_CONFIG_DIR."
                ),
            )
        # A directory existing is not a usage source. Without this check an
        # empty or wrong directory reports a confident zero, which reads as
        # "you used nothing" rather than "nothing was read".
        if not any(path.is_file() for path in root.rglob("*.jsonl")):
            return Availability(
                supported=False,
                reason=(
                    f"no .jsonl session transcripts under {root}. The directory exists "
                    "but holds no readable usage source, so no usage can be measured "
                    "from it — this is not the same as zero usage."
                ),
            )
        return Availability(supported=True)

    def collect(
        self,
        data_root: Path | None,
        since: str | None,
        until: str | None,
        zone: tzinfo | SystemLocal,
    ) -> CollectionResult:
        available = self.availability(data_root)
        if not available.supported:
            raise RuntimeUnavailable(self.name, available.reason)

        root = self.resolve_data_root(data_root)
        assert root is not None  # guaranteed by the availability check above
        result = CollectionResult()
        # Insertion-ordered so the output does not depend on filesystem order.
        by_request: dict[tuple[str, ...], _RequestState] = {}
        # The canonical day of a request is the earliest day any of its copies
        # carries — when the work was started. Taking it from the winning
        # snapshot instead would let a response that finished after midnight
        # move the whole request into the next day.
        earliest_day: dict[tuple[str, ...], str | None] = {}
        divergent: set[tuple[str, ...]] = set()

        for path in sorted(root.rglob("*.jsonl")):
            if not path.is_file():
                continue
            result.files_scanned += 1
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                result.unreadable_files += 1
                continue
            # Reconciliation happens before any window filter. Filtering first
            # would let the copies of one request fall on either side of
            # midnight and be counted as a request in *both* adjacent days,
            # with the earlier day priced from the partial snapshot.
            for record in self._parse_lines(lines, result, zone, path):
                key = record.dedup_key
                state = by_request.get(key)
                if state is None:
                    totals = _totals_of(record)
                    by_request[key] = _RequestState(
                        total=sum(totals),
                        first_totals=totals,
                        claims={
                            (record.model, totals, record.cache_split_exact): (
                                _parse_instant(record.timestamp),
                                record,
                            )
                        },
                    )
                    earliest_day[key] = record.day
                    continue
                result.duplicate_records += 1
                if _totals_of(record) != state.first_totals:
                    # Any copy differing from the first copy means the copies
                    # differ. Counted per request, not per copy: three
                    # differing snapshots of one request are one reconciled
                    # request.
                    divergent.add(key)
                state.add(record)
                earliest_day[key] = _earlier_day(earliest_day.get(key), record.day)

        if result.files_scanned and result.unreadable_files == result.files_scanned:
            raise RuntimeUnavailable(
                self.name,
                f"all {result.files_scanned} transcript file(s) under {root} were "
                "unreadable, so no usage could be measured — this is not the same as "
                "zero usage.",
            )

        result.reconciled_records = len(divergent)
        for key, state in by_request.items():
            record = state.resolve()
            if record is None:
                # Copies disagree about what to price this request as, and
                # nothing orders them. Dropping it visibly beats picking a
                # price by read order.
                result.conflicting_records += 1
                continue
            canonical = replace(record, day=earliest_day.get(key, record.day))
            if not _within(canonical.day, since, until):
                result.filtered_out += 1
                continue
            result.records.append(canonical)
        return result

    def _parse_lines(
        self,
        lines: Sequence[str],
        result: CollectionResult,
        zone: tzinfo | SystemLocal,
        path: Path,
    ) -> Iterator[UsageRecord]:
        for line_number, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                result.malformed_lines += 1
                continue
            if not isinstance(entry, dict):
                result.malformed_lines += 1
                continue
            record = self._to_record(entry, zone, result, path, line_number)
            if record is not None:
                yield record

    def _to_record(
        self,
        entry: dict,
        zone: tzinfo | SystemLocal,
        result: CollectionResult,
        path: Path,
        line_number: int,
    ) -> UsageRecord | None:
        message = entry.get("message")
        if not isinstance(message, dict):
            return None
        usage = message.get("usage")
        if not isinstance(usage, dict):
            return None
        model = message.get("model")
        if not isinstance(model, str) or not model:
            return None

        cache_creation = _container_of(usage, "cache_creation")
        server_tool_use = _container_of(usage, "server_tool_use")
        if cache_creation is _MALFORMED or server_tool_use is _MALFORMED:
            result.invalid_records += 1
            return None

        # Every raw counter is validated before any fallback or reconciliation
        # runs. Checking afterwards would let normalization mask corruption: a
        # negative flat total reads as zero cache usage, a negative split tier
        # gets padded back up to the flat total as though it were partial, and
        # a present non-integer collapses to zero exactly as if it were absent.
        counts = {
            "input": _count_of(usage, "input_tokens"),
            "output": _count_of(usage, "output_tokens"),
            "cache_read": _count_of(usage, "cache_read_input_tokens"),
            "flat_cache": _count_of(usage, "cache_creation_input_tokens"),
            "write_5m": _count_of(cache_creation, "ephemeral_5m_input_tokens"),
            "write_1h": _count_of(cache_creation, "ephemeral_1h_input_tokens"),
            "web_search": _count_of(server_tool_use, "web_search_requests"),
        }
        if any(value is _MALFORMED for value in counts.values()):
            # A malformed counter cannot be real usage, and mapping it to zero
            # would let a corrupt record shrink the total while exiting 0 as
            # though nothing were wrong.
            result.invalid_records += 1
            return None

        # Prefer the lifetime split, but reconcile it against the flat total.
        # An empty or partial `cache_creation` is still a dict, so trusting the
        # split whenever one is present discards whatever the flat total holds
        # beyond it — silently, and in the direction that lowers the bill.
        flat_present = "cache_creation_input_tokens" in usage
        flat_total = counts["flat_cache"]
        write_5m = counts["write_5m"]
        write_1h = counts["write_1h"]
        cache_split_exact = True
        if flat_present:
            residual = flat_total - (write_5m + write_1h)
            if residual > 0:
                # The split accounts for less than the flat total. Carry the
                # remainder at the 5-minute tier — the cheaper of the two, so
                # the record understates rather than inflates — and count it,
                # because a split that does not reconcile is a fact about the
                # source worth surfacing rather than absorbing.
                if write_5m or write_1h:
                    result.partial_cache_splits += 1
                write_5m += residual
                cache_split_exact = False
            elif residual < 0:
                # The split claims more than the flat total. On real
                # transcripts this is the aggregated multi-iteration record
                # shape, where the legacy flat field lags the lifetime split —
                # including reading 0 while the split is populated — so the
                # split is the more complete figure, not a corruption. It is
                # used as stated, and the disagreement is counted so it is
                # visible rather than absorbed.
                result.cache_split_conflicts += 1

        speed = usage.get("speed")
        identity = str(message.get("id") or entry.get("uuid") or "")
        request_id = str(entry.get("requestId") or "")
        if identity or request_id:
            dedup_key: tuple[str, ...] = (identity, request_id)
        else:
            # Nothing identifies this request, so it cannot be reconciled
            # against any other. A shared empty key would collapse unrelated
            # requests into one; a file/line key keeps each of them distinct.
            result.unidentified_records += 1
            dedup_key = ("", "", str(path), str(line_number))

        timestamp = entry.get("timestamp")
        return UsageRecord(
            model=f"{model}#{speed}" if isinstance(speed, str) and speed != "standard" else model,
            input_tokens=counts["input"],
            output_tokens=counts["output"],
            cache_read_tokens=counts["cache_read"],
            cache_write_5m_tokens=write_5m,
            cache_write_1h_tokens=write_1h,
            web_search_requests=counts["web_search"],
            day=day_of(timestamp, zone),
            dedup_key=dedup_key,
            cache_split_exact=cache_split_exact,
            timestamp=timestamp if isinstance(timestamp, str) else None,
        )


def _within(day: str | None, since: str | None, until: str | None) -> bool:
    if day is None:
        # A record with no usable timestamp cannot be placed in a window. It is
        # kept only when no window was requested, so a windowed total never
        # silently includes usage from outside the window.
        return since is None and until is None
    if since is not None and day < since:
        return False
    return until is None or day <= until


_ADAPTERS: dict[str, RuntimeAdapter] = {
    CLAUDE_CODE: ClaudeCodeAdapter(),
    "codex": UnavailableAdapter(
        "codex",
        "no local usage source is established for this runtime, so its usage "
        "cannot be measured from this machine.",
    ),
}


def available_runtimes() -> list[str]:
    return sorted(_ADAPTERS)


def get_adapter(name: str) -> RuntimeAdapter:
    try:
        return _ADAPTERS[name]
    except KeyError:
        raise KeyError(
            f"unknown runtime {name!r}; known runtimes: {', '.join(available_runtimes())}"
        ) from None
