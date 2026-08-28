#!/usr/bin/env python3
"""Check a render report for coverage gaps, unbacked findings, and measured overflow."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


EXIT_CLEAN = 0
EXIT_NOT_CLEAN = 1
EXIT_INPUT_ERROR = 2

_SIGNATURES: tuple[tuple[str, bytes], ...] = (
    ("PNG", b"\x89PNG\r\n\x1a\n"),
    ("JPEG", b"\xff\xd8\xff"),
    ("GIF", b"GIF87a"),
    ("GIF", b"GIF89a"),
)
_SUFFIXES: dict[str, frozenset[str]] = {
    "PNG": frozenset({".png"}),
    "JPEG": frozenset({".jpg", ".jpeg"}),
    "GIF": frozenset({".gif"}),
    "WEBP": frozenset({".webp"}),
}
_SEPARATOR = " · "
# Characters a receipt line must never carry raw: C0 and C1 controls (Cc,
# which includes NEL), the line separator (Zl), and the paragraph separator
# (Zp) are everything str.splitlines() breaks on; lone surrogates (Cs) cannot
# be encoded as UTF-8 at all, so printing one or opening a path with one
# raises instead of producing a receipt.
_UNPRINTABLE_CATEGORIES = frozenset({"Cc", "Zl", "Zp", "Cs"})


@dataclass(frozen=True)
class Cell:
    viewport: str
    theme: str
    state: str

    def render(self) -> str:
        return f"{self.viewport}/{self.theme}/{self.state}"


@dataclass(frozen=True)
class Capture:
    id: str
    cell: Cell
    screenshot: str
    viewport_width: int | None
    scroll_width: int | None
    problem: str | None

    @property
    def overshoot(self) -> int | None:
        if self.viewport_width is None or self.scroll_width is None:
            return None
        if self.scroll_width <= self.viewport_width:
            return None
        return self.scroll_width - self.viewport_width

    def metrics_text(self) -> str:
        if self.viewport_width is None and self.scroll_width is None:
            return "metrics=absent"
        return (
            f"viewport_width={_optional_text(self.viewport_width)}"
            f" · scroll_width={_optional_text(self.scroll_width)}"
        )


@dataclass(frozen=True)
class Finding:
    id: str
    summary: str
    evidence: str
    capture: str | None


def _is_unprintable(char: str) -> bool:
    return unicodedata.category(char) in _UNPRINTABLE_CATEGORIES


def _quote(value: str) -> str:
    """Encode a free-text field so it occupies exactly one physical line."""
    encoded = json.dumps(value, ensure_ascii=False)
    # json escapes controls below U+0020; DEL, the C1 range, the Unicode
    # line/paragraph separators, and lone surrogates pass through, so escape
    # those the same way.
    return "".join(
        "\\u" + format(ord(char), "04x") if _is_unprintable(char) else char
        for char in encoded
    )


def _identifier(value: object, what: str) -> str:
    """Validate a report-provided string that a receipt line prints unquoted."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{what} must be a non-empty string")
    if any(_is_unprintable(char) for char in value):
        raise ValueError(
            f"{what} {value!r} contains a control, line-break, or surrogate character"
        )
    if _SEPARATOR in value:
        raise ValueError(f"{what} {value!r} contains the receipt separator {_SEPARATOR!r}")
    return value


def _optional_text(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def _resolved_path(base: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else base / path


def _screenshot_problem(path: Path) -> str | None:
    """Return why the file is not usable screenshot evidence, or None."""
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except FileNotFoundError:
        return "screenshot file does not exist"
    except OSError as error:
        return f"screenshot could not be read: {error}"
    if not head:
        return "screenshot is empty"

    detected = next((name for name, signature in _SIGNATURES if head.startswith(signature)), None)
    if detected is None and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        detected = "WEBP"
    if detected is None:
        return "screenshot is not PNG, JPEG, GIF, or WebP image bytes"

    suffix = path.suffix.lower()
    if suffix not in _SUFFIXES[detected]:
        naming = f"is named {suffix}" if suffix else "has no suffix"
        return f"screenshot bytes are {detected} but the file {naming}"
    return None


def _names(raw: object, field: str) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"matrix.{field} must be a non-empty array")
    names: list[str] = []
    for entry in raw:
        value = entry.get("name") if isinstance(entry, Mapping) else entry
        name = _identifier(value, f"matrix.{field} entry")
        if name in names:
            raise ValueError(f"matrix.{field} repeats {name!r}")
        names.append(name)
    return names


def _viewport_widths(raw: object) -> dict[str, int | None]:
    """Map each declared viewport name to its declared width, when given."""
    widths: dict[str, int | None] = {}
    for name in _names(raw, "viewports"):
        widths[name] = None
    assert isinstance(raw, list)
    for entry in raw:
        if not isinstance(entry, Mapping) or entry.get("width") is None:
            continue
        width = entry.get("width")
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            raise ValueError(
                f"matrix.viewports width for {entry.get('name')!r} must be a positive integer"
            )
        widths[str(entry.get("name"))] = width
    return widths


def _load_matrix(
    payload: Mapping[str, Any],
) -> tuple[list[Cell], dict[str, int | None]]:
    matrix = payload.get("matrix")
    if not isinstance(matrix, Mapping):
        raise ValueError("report JSON must contain a matrix object")
    widths = _viewport_widths(matrix.get("viewports"))
    themes = _names(matrix.get("themes"), "themes")
    states = _names(matrix.get("states"), "states")
    cells = [
        Cell(viewport, theme, state)
        for viewport in widths
        for theme in themes
        for state in states
    ]
    return cells, widths


def _metric(raw: object, field: str, capture_id: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(
            f"capture {capture_id!r}: metrics.{field} must be a non-negative integer"
        )
    return raw


def _load_captures(
    payload: Mapping[str, Any],
    *,
    base: Path,
    declared: Sequence[Cell],
    widths: Mapping[str, int | None],
) -> list[Capture]:
    raw_captures = payload.get("captures", [])
    if not isinstance(raw_captures, list):
        raise ValueError("captures must be an array")

    declared_cells = set(declared)
    captures: list[Capture] = []
    seen: set[str] = set()
    captured_by: dict[Cell, str] = {}
    for index, raw in enumerate(raw_captures):
        if not isinstance(raw, Mapping):
            raise ValueError(f"captures[{index}] must be an object")
        capture_id = _identifier(raw.get("id"), f"captures[{index}].id")
        if capture_id in seen:
            raise ValueError(f"capture id {capture_id!r} is repeated")
        seen.add(capture_id)

        cell = Cell(
            *(
                _identifier(raw.get(field), f"capture {capture_id!r} {field}")
                for field in ("viewport", "theme", "state")
            )
        )
        if cell not in declared_cells:
            raise ValueError(
                f"capture {capture_id!r} cites undeclared cell {cell.render()};"
                " add it to the matrix or drop the capture"
            )
        earlier = captured_by.get(cell)
        if earlier is not None:
            raise ValueError(
                f"capture {capture_id!r} repeats cell {cell.render()} already captured by"
                f" {earlier!r}; keep one capture per cell"
            )
        captured_by[cell] = capture_id

        screenshot = _identifier(raw.get("screenshot"), f"capture {capture_id!r} screenshot")
        metrics = raw.get("metrics", {})
        if not isinstance(metrics, Mapping):
            raise ValueError(f"capture {capture_id!r}: metrics must be an object")

        viewport_width = _metric(metrics.get("viewport_width"), "viewport_width", capture_id)
        problem = _screenshot_problem(_resolved_path(base, screenshot))
        declared_width = widths.get(cell.viewport)
        if (
            problem is None
            and declared_width is not None
            and viewport_width is not None
            and viewport_width != declared_width
        ):
            problem = (
                f"viewport_width {viewport_width} does not match the declared"
                f" width {declared_width} for viewport {cell.viewport}"
            )
        captures.append(
            Capture(
                id=capture_id,
                cell=cell,
                screenshot=Path(screenshot).as_posix(),
                viewport_width=viewport_width,
                scroll_width=_metric(metrics.get("scroll_width"), "scroll_width", capture_id),
                problem=problem,
            )
        )
    return captures


def _load_findings(payload: Mapping[str, Any]) -> list[Finding]:
    raw_findings = payload.get("findings", [])
    if not isinstance(raw_findings, list):
        raise ValueError("findings must be an array")

    findings: list[Finding] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_findings):
        if not isinstance(raw, Mapping):
            raise ValueError(f"findings[{index}] must be an object")
        finding_id = _identifier(raw.get("id"), f"findings[{index}].id")
        if finding_id in seen:
            raise ValueError(f"finding id {finding_id!r} is repeated")
        seen.add(finding_id)
        summary = raw.get("summary")
        if not isinstance(summary, str) or not summary:
            raise ValueError(f"finding {finding_id!r} requires a non-empty summary")
        evidence = raw.get("evidence")
        capture = raw.get("capture")
        findings.append(
            Finding(
                id=finding_id,
                summary=summary,
                evidence=evidence if isinstance(evidence, str) else "",
                capture=(
                    None
                    if capture is None or capture == ""
                    else _identifier(capture, f"finding {finding_id!r} capture")
                ),
            )
        )
    return findings


def _load_report(path: Path) -> Mapping[str, Any]:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read report JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("report JSON must be an object")
    return payload


def evaluate(payload: Mapping[str, Any], *, base: Path) -> tuple[list[str], int]:
    """Return the receipt lines and the exit code for one report."""
    cells, widths = _load_matrix(payload)
    captures = _load_captures(payload, base=base, declared=cells, widths=widths)
    findings = _load_findings(payload)

    # _load_captures guarantees at most one capture per cell, so every capture
    # the report carries is evaluated and printed; none can hide behind another.
    by_cell = {capture.cell: capture for capture in captures}
    by_id = {capture.id: capture for capture in captures}

    lines: list[str] = []
    covered = missing = overflow = rendered = rejected = inference = 0

    for cell in cells:
        capture = by_cell.get(cell)
        if capture is None:
            missing += 1
            lines.append(f"MISSING · cell={cell.render()} · reason={_quote('no capture')}")
            continue
        if capture.problem is not None:
            missing += 1
            lines.append(
                f"MISSING · cell={cell.render()} · capture={capture.id}"
                f" · screenshot={capture.screenshot} · reason={_quote(capture.problem)}"
            )
            continue

        covered += 1
        lines.append(
            f"COVERED · cell={cell.render()} · capture={capture.id}"
            f" · screenshot={capture.screenshot} · {capture.metrics_text()}"
        )
        if capture.overshoot is not None:
            overflow += 1
            lines.append(
                f"OVERFLOW · cell={cell.render()} · capture={capture.id}"
                f" · viewport_width={capture.viewport_width}"
                f" · scroll_width={capture.scroll_width}"
                f" · overshoot={capture.overshoot}"
            )

    for finding in findings:
        if finding.evidence == "source-only":
            inference += 1
            lines.append(
                f"INFERENCE · finding={finding.id} · summary={_quote(finding.summary)}"
            )
            continue

        reason: str | None
        if finding.evidence != "rendered":
            reason = 'evidence must be "rendered" or "source-only"'
        elif finding.capture is None:
            reason = "rendered evidence cites no capture"
        elif finding.capture not in by_id:
            reason = f"capture not in report: {finding.capture}"
        elif by_id[finding.capture].problem is not None:
            reason = f"capture {finding.capture}: {by_id[finding.capture].problem}"
        else:
            reason = None

        if reason is not None:
            rejected += 1
            lines.append(
                f"REJECTED · finding={finding.id} · reason={_quote(reason)}"
                f" · summary={_quote(finding.summary)}"
            )
            continue

        capture = by_id[finding.capture or ""]
        rendered += 1
        lines.append(
            f"RENDERED · finding={finding.id} · cell={capture.cell.render()}"
            f" · capture={capture.id} · screenshot={capture.screenshot}"
            f" · summary={_quote(finding.summary)}"
        )

    exit_code = EXIT_CLEAN if not (missing or overflow or rejected) else EXIT_NOT_CLEAN
    lines.append(
        f"SUMMARY · cells={len(cells)} · covered={covered} · missing={missing}"
        f" · overflow={overflow} · rendered={rendered} · rejected={rejected}"
        f" · inference={inference} · exit={exit_code}"
    )
    return lines, exit_code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="JSON render report to check")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report_path = Path(args.report)
    try:
        payload = _load_report(report_path)
        lines, exit_code = evaluate(payload, base=report_path.resolve().parent)
    except ValueError as error:
        print(f"render-check: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    for line in lines:
        print(line)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
