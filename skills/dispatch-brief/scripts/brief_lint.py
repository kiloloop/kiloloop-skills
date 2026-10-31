#!/usr/bin/env python3
"""Lint a task brief for missing sections, undeclared side effects, and procedure."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


EXIT_CLEAN = 0
EXIT_FINDINGS = 1
EXIT_INPUT_ERROR = 2

SIZE_CAP = 3500
SIZE_TARGET = 2500
STEP_LIST_MIN = 3

SECTIONS = ("Goal", "Instruction", "Fence", "Done", "Context")
CANONICAL_SECTIONS = {section.casefold(): section for section in SECTIONS}
# The sections whose prose states what the work does. Context carries
# reference facts and the declaration itself, so it is not read for asks.
ASK_SECTIONS = frozenset({"Goal", "Instruction", "Fence", "Done"})
DECLARATION_RE = re.compile(r"^\s*[-*]\s+Side effects:(?P<value>.*)$", re.IGNORECASE)
NONE = "none"


@dataclass(frozen=True)
class Ask:
    effect: str
    triggers: str
    pattern: re.Pattern[str]


_WORDS = r"(?:[ \t]+[\w'-]+){0,3}?[ \t]+"
# One table, mirrored in SKILL.md ("Declaring side effects"): the wording that
# reads as an ask, and the side effect the brief must declare for it.
ASKS = (
    Ask(
        "commit",
        "commit, land or push a change",
        re.compile(
            r"\b(?:commit(?:s|ted|ting)?|land(?:s|ed|ing)?|push(?:es|ed|ing)?)\b",
            re.IGNORECASE,
        ),
    ),
    Ask(
        "pull request",
        "open, raise, create, update or submit a pull request; a pull-request link",
        re.compile(
            r"\b(?:open(?:s|ed|ing)?|rais(?:e|es|ed|ing)|creat(?:e|es|ed|ing)|"
            r"updat(?:e|es|ed|ing)|submit(?:s|ted|ting)?)\b" + _WORDS + r"(?:pull[ -]requests?|PRs?)\b"
            r"|\b(?:pull[ -]request|PR)[ \t]+(?:link|url)\b",
            re.IGNORECASE,
        ),
    ),
    Ask(
        "issue",
        "file, open, raise or create an issue",
        re.compile(
            r"\b(?:fil(?:e|es|ed|ing)|open(?:s|ed|ing)?|rais(?:e|es|ed|ing)|creat(?:e|es|ed|ing))\b"
            + _WORDS
            + r"issues?\b",
            re.IGNORECASE,
        ),
    ),
    Ask(
        "comment",
        "comment on something, or post a comment",
        re.compile(
            r"\bcomment(?:s|ed|ing)?[ \t]+on\b|\bpost(?:s|ed|ing)?\b" + _WORDS + r"comments?\b",
            re.IGNORECASE,
        ),
    ),
    Ask(
        "merge",
        "merge, or land it yourself; yours to land",
        re.compile(
            r"\bmerg(?:e|es|ed|ing)\b(?![ \t-]+(?:conflicts?|commits?|methods?|strateg(?:y|ies)|settings?|queues?|base)\b)"
            r"|\bland[ \t]+it[ \t]+yourself\b|\byours[ \t]+to[ \t]+land\b",
            re.IGNORECASE,
        ),
    ),
    Ask(
        "deploy",
        "deploy, or ship to production",
        re.compile(r"\bdeploy(?:s|ed|ing)?\b|\bship(?:s|ped|ping)?[ \t]+to[ \t]+production\b", re.IGNORECASE),
    ),
    Ask(
        "publish",
        "publish",
        re.compile(r"\bpublish(?:es|ed|ing)?\b", re.IGNORECASE),
    ),
)
EFFECTS = tuple(ask.effect for ask in ASKS)

# A negation governs the rest of its sub-clause: up to a comma or a contrast
# word. Followed by a colon ("Out of scope:") it governs its whole
# comma-separated list, still up to a contrast word. Inside a condition ("if
# no issues remain") it governs nothing past the condition. A heading, or a
# line ending in a colon, whose text is negated governs the block below it up
# to the first blank line or code fence; "skip" and "without" stay within
# their own clause.
NEGATION_TERMS = (
    r"no|not|never|do\s+not|does\s+not|don't|doesn't|"
    r"out\s+of\s+scope|exclude(?:s|d)?|avoid|refrain\s+from|prohibited|forbidden"
)
CLAUSE_NEGATION_RE = re.compile(rf"\b(?:{NEGATION_TERMS}|skip|without)\b", re.IGNORECASE)
BLOCK_NEGATION_RE = re.compile(rf"\b(?:{NEGATION_TERMS})\b", re.IGNORECASE)
# "yet" and "then" also read as adverbs ("do not yet publish"), so they end a
# scope only as conjunctions, after a comma or "and".
CONTRAST_RE = re.compile(
    r"\b(?:but|however|instead|though|although|whereas|except)\b|(?:,|\band\b)\s*(?:yet|then)\b",
    re.IGNORECASE,
)
CONDITION_RE = re.compile(r"\b(?:if|when|whenever|unless|once|until|whether)\b", re.IGNORECASE)
# A command negation heads the consequent even after a condition with no
# comma ("if tests fail do not publish"); only a descriptive one ("if no
# issues remain") sits inside the condition.
IMPERATIVE_NEGATION_RE = re.compile(r"(?:do\s+not|don't|never|avoid|refrain\s+from|skip)", re.IGNORECASE)
NEGATION_REACH = 160
HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]+|$)")
FENCE_RE = re.compile(r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
CLAUSE_MARKS = (".", "!", "?", ";", "—", "–")
STEP_RE = re.compile(r"^\s*\d+[.)]\s+\S")
CHECK_ORDER = ("sections", "declared", "procedure", "size")


@dataclass(frozen=True)
class Finding:
    check: str
    line: int | None
    message: str
    advisory: bool = False

    def render(self) -> str:
        where = f" L{self.line}" if self.line is not None else ""
        suffix = " (advisory)" if self.advisory else ""
        return f"[{self.check}]{where}: {self.message}{suffix}"


class InputError(Exception):
    pass


def governs(prefix: str, terms: re.Pattern[str], comma_ends_scope: bool, rest: str = "") -> bool:
    """Whether the last negation in ``prefix`` still governs the end of it.

    ``rest`` is the remainder of the clause after the prefix: a comma series
    that closes with ", or", ", and" or ", nor" is a list the negation spans.
    """
    last = None
    for last in terms.finditer(prefix):
        pass
    if last is None:
        return False
    after = prefix[last.end() :]
    if len(after) > NEGATION_REACH or CONTRAST_RE.search(after):
        return False
    is_list = re.match(r"\s*:", after) is not None or re.search(r",\s*(?:or|and|nor)\b", after + rest, re.IGNORECASE)
    if comma_ends_scope and not is_list and "," in after:
        return False
    if IMPERATIVE_NEGATION_RE.fullmatch(last.group(0)):
        return True
    before = prefix[: last.start()]
    return CONDITION_RE.search(before[before.rfind(",") + 1 :]) is None


def is_negated(lines: Sequence[str], index: int, column: int, fenced: set[int]) -> bool:
    line = lines[index]
    prefix = line[:column]
    clause = prefix[max(prefix.rfind(mark) for mark in CLAUSE_MARKS) + 1 :]
    ends = [position for mark in CLAUSE_MARKS if (position := line.find(mark, column)) >= 0]
    rest = line[column : min(ends) if ends else len(line)]
    if governs(clause, CLAUSE_NEGATION_RE, comma_ends_scope=True, rest=rest):
        return True
    for above in range(index - 1, -1, -1):
        stripped = lines[above].strip()
        if not stripped or above in fenced:
            return False
        if HEADING_RE.match(lines[above]) or stripped.endswith(":"):
            return governs(stripped, BLOCK_NEGATION_RE, comma_ends_scope=False)
    return False


def fenced_lines(lines: Sequence[str]) -> set[int]:
    """Indexes of lines inside backtick or tilde code fences, fences included."""
    fenced: set[int] = set()
    opening: tuple[int, str] | None = None
    for index, line in enumerate(lines):
        match = FENCE_RE.match(line)
        if opening is None:
            if match and not (match.group("fence")[0] == "`" and "`" in match.group("info")):
                opening = (index, match.group("fence"))
            continue
        fence = opening[1]
        if match and match.group("fence")[0] == fence[0] and len(match.group("fence")) >= len(fence) and not match.group("info").strip():
            fenced.update(range(opening[0], index + 1))
            opening = None
    if opening is not None:
        fenced.update(range(opening[0], len(lines)))
    return fenced


def read_declaration(lines: Sequence[str], rows: Sequence[int], findings: list[Finding]) -> set[str] | None:
    if not rows:
        findings.append(Finding("declared", None, 'no "- Side effects:" line in ## Context; write "none" when there are none'))
        return None
    if len(rows) > 1:
        findings.append(Finding("declared", rows[1] + 1, '"Side effects:" declared twice'))
    index = rows[0]
    match = DECLARATION_RE.match(lines[index])
    assert match is not None
    items = [item.strip().lower() for item in re.split(r"[·,]", match.group("value")) if item.strip()]
    if not items:
        findings.append(Finding("declared", index + 1, 'empty "Side effects:" line; write "none" when there are none'))
        return None
    declared: set[str] = set()
    for item in items:
        if item == NONE:
            continue
        if item not in EFFECTS:
            findings.append(Finding("declared", index + 1, f'unknown side effect "{item}"; use {", ".join(EFFECTS)} or none'))
            continue
        declared.add(item)
    if NONE in items and len(items) > 1:
        findings.append(Finding("declared", index + 1, '"none" beside other side effects'))
    return declared


def check_asks(
    lines: Sequence[str],
    readable: Sequence[bool],
    fenced: set[int],
    declared: set[str] | None,
    declaration_line: int | None,
    findings: list[Finding],
) -> None:
    asked: set[str] = set()
    for index, line in enumerate(lines):
        if not readable[index]:
            continue
        for ask in ASKS:
            for match in ask.pattern.finditer(line):
                if is_negated(lines, index, match.start(), fenced):
                    continue
                asked.add(ask.effect)
                if declared is not None and ask.effect not in declared:
                    findings.append(Finding(
                        "declared", index + 1,
                        f'"{match.group(0)}" asks for {ask.effect}, which Side effects does not declare',
                    ))
                break
    for effect in sorted(declared or set(), key=EFFECTS.index):
        if effect not in asked:
            findings.append(Finding(
                "declared", declaration_line,
                f'"{effect}" is declared but the body never asks for it', True,
            ))


def check_procedure(lines: Sequence[str], readable: Sequence[bool], section_at: Sequence[str | None], findings: list[Finding]) -> None:
    run_start: int | None = None
    run_length = 0
    for index in range(len(lines) + 1):
        is_step = index < len(lines) and readable[index] and bool(STEP_RE.match(lines[index]))
        if is_step:
            if run_start is None:
                run_start = index
            run_length += 1
            continue
        if index < len(lines) and not lines[index].strip():
            continue  # a loose list keeps its run across blank lines
        if run_start is not None and run_length >= STEP_LIST_MIN:
            findings.append(Finding(
                "procedure", run_start + 1,
                f"a {run_length}-step list in ## {section_at[run_start]}; state the outcome and let the receiver own the steps",
                True,
            ))
        run_start, run_length = None, 0


def lint(text: str) -> list[Finding]:
    findings: list[Finding] = []
    lines = text.split("\n")
    fenced = fenced_lines(lines)

    section_at: list[str | None] = []
    heading_lines: set[int] = set()
    titles: set[str] = set()
    current: str | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^##[ \t]+(.+?)[ \t]*$", line)
        if match and index not in fenced:
            # One canonical name serves both the presence check and the scan.
            current = CANONICAL_SECTIONS.get(match.group(1).casefold(), match.group(1))
            heading_lines.add(index)
            titles.add(current)
        section_at.append(current)

    for section in SECTIONS:
        if section not in titles:
            findings.append(Finding("sections", None, f"missing: ## {section}"))

    rows = [
        index
        for index, line in enumerate(lines)
        if section_at[index] == "Context" and index not in fenced and DECLARATION_RE.match(line)
    ]
    declared = read_declaration(lines, rows, findings)
    readable = [
        index not in fenced and index not in heading_lines and section_at[index] in ASK_SECTIONS
        for index in range(len(lines))
    ]
    check_asks(lines, readable, fenced, declared, rows[0] + 1 if rows else None, findings)
    check_procedure(lines, readable, section_at, findings)

    size = len(text.rstrip("\n"))
    if size > SIZE_CAP:
        findings.append(Finding("size", None, f"{size:,} chars (cap {SIZE_CAP:,})"))
    elif size > SIZE_TARGET:
        findings.append(Finding("size", None, f"{size:,} chars, past the {SIZE_TARGET:,} target", True))

    return sorted(findings, key=lambda finding: (finding.line or 0, CHECK_ORDER.index(finding.check)))


def summary(findings: Sequence[Finding]) -> str:
    count = sum(not finding.advisory for finding in findings)
    advisories = len(findings) - count
    head = "clean" if count == 0 else f"{count} finding{'s' if count != 1 else ''}"
    if advisories:
        head += f", {advisories} {'advisory' if advisories == 1 else 'advisories'}"
    return head


def read_brief(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise InputError(f"cannot read {path}: {error}") from error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("briefs", nargs="+", type=Path, help="Markdown task brief files")
    args = parser.parse_args(argv)
    try:
        texts = [(path, read_brief(path)) for path in args.briefs]
    except InputError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    status = EXIT_CLEAN
    for path, text in texts:
        findings = lint(text)
        if len(texts) > 1:
            print(f"== {path}")
        for finding in findings:
            print(finding.render())
        print(summary(findings))
        if any(not finding.advisory for finding in findings):
            status = EXIT_FINDINGS
    return status


if __name__ == "__main__":
    raise SystemExit(main())
