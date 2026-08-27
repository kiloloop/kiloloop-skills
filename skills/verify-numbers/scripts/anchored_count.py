#!/usr/bin/env python3
"""Count full-line regex matches in explicitly named UTF-8 files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class LineMatch:
    path: str
    line: int
    text: str


def _split_lf_lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _parse_files(raw_paths: Sequence[str]) -> list[tuple[str, Path]]:
    files: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for raw_path in raw_paths:
        path = Path(raw_path)
        if not path.is_file():
            raise ValueError(f"input is not a regular file: {raw_path}")
        resolved = path.resolve()
        if resolved in seen:
            raise ValueError(f"input resolves to a duplicate file: {raw_path}")
        seen.add(resolved)
        files.append((path.as_posix(), path))
    return sorted(files, key=lambda item: item[0])


def collect_matches(
    files: Sequence[tuple[str, Path]],
    pattern: re.Pattern[str],
) -> list[LineMatch]:
    matches: list[LineMatch] = []
    for display_path, path in files:
        try:
            with path.open(encoding="utf-8-sig", newline="") as input_file:
                text = input_file.read()
        except UnicodeDecodeError as error:
            raise ValueError(f"input is not valid UTF-8: {display_path}: {error}") from error
        except OSError as error:
            raise ValueError(f"could not read input: {display_path}: {error}") from error
        lines = _split_lf_lines(text)
        for line_number, text in enumerate(lines, start=1):
            if pattern.fullmatch(text) is not None:
                matches.append(
                    LineMatch(path=display_path, line=line_number, text=text)
                )
    return matches


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="+",
        help="explicit UTF-8 files to count; directories and globs are not expanded",
    )
    parser.add_argument(
        "--pattern",
        required=True,
        help="Python regular expression applied to each complete line",
    )
    parser.add_argument(
        "--ignore-case",
        action="store_true",
        help="compile the pattern with case-insensitive matching",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="evidence output format (default: text)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    flags = re.IGNORECASE if args.ignore_case else 0
    try:
        pattern = re.compile(args.pattern, flags)
        files = _parse_files(args.files)
        matches = collect_matches(files, pattern)
    except (re.error, ValueError) as error:
        print(f"verify-numbers: {error}", file=sys.stderr)
        return 2

    if args.format == "json":
        payload = {
            "pattern": args.pattern,
            "ignore_case": args.ignore_case,
            "match_method": "full_line",
            "count_unit": "matching_lines",
            "line_separator": "LF (CRLF normalized)",
            "files": [display_path for display_path, _path in files],
            "count": len(matches),
            "matches": [asdict(match) for match in matches],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for match in matches:
        print(f"{match.path}:{match.line}:{match.text}")
    print(f"Measured matching lines: {len(matches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
