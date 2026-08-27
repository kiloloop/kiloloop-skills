from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


SKILL_ROOT = Path(__file__).parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "anchored_count.py"
CORPUS = Path(__file__).with_name("data") / "claims.md"


def run_counter(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=SKILL_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_full_line_count_excludes_phantom_occurrences() -> None:
    completed = run_counter(
        "--pattern",
        "## Verified",
        "--format",
        "json",
        str(CORPUS.relative_to(SKILL_ROOT)),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["match_method"] == "full_line"
    assert payload["count_unit"] == "matching_lines"
    assert payload["count"] == 2
    assert [match["line"] for match in payload["matches"]] == [1, 4]
    assert all(match["text"] == "## Verified" for match in payload["matches"])


def test_seeded_corpus_distinguishes_full_line_from_substring_count() -> None:
    pattern = re.compile("## Verified")
    lines = CORPUS.read_text(encoding="utf-8").splitlines()

    assert sum(pattern.fullmatch(line) is not None for line in lines) == 2
    assert sum(pattern.search(line) is not None for line in lines) == 4


def test_only_lf_and_crlf_delimit_evidence_lines(tmp_path: Path) -> None:
    corpus = tmp_path / "line-separators.md"
    corpus.write_bytes(b"## Verified\x0c## Verified\n## Verified\r\n")

    completed = run_counter(
        "--pattern",
        "## Verified",
        "--format",
        "json",
        str(corpus),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["line_separator"] == "LF (CRLF normalized)"
    assert payload["count"] == 1
    assert [match["line"] for match in payload["matches"]] == [2]


def test_bare_cr_remains_content_within_an_lf_delimited_line(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "bare-cr.md"
    corpus.write_bytes(b"## Verified\rnot-a-match\n## Verified\r\n")

    completed = run_counter(
        "--pattern",
        "## Verified",
        "--format",
        "json",
        str(corpus),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["line_separator"] == "LF (CRLF normalized)"
    assert payload["count"] == 1
    assert [match["line"] for match in payload["matches"]] == [2]


def test_utf8_bom_is_normalized(tmp_path: Path) -> None:
    corpus = tmp_path / "bom.md"
    corpus.write_bytes(b"\xef\xbb\xbf## Verified\n## Verified\n")

    completed = run_counter(
        "--pattern",
        "## Verified",
        "--format",
        "json",
        str(corpus),
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["count"] == 2
    assert [match["line"] for match in payload["matches"]] == [1, 2]


def test_duplicate_file_alias_is_rejected() -> None:
    relative_corpus = str(CORPUS.relative_to(SKILL_ROOT))
    completed = run_counter(
        "--pattern",
        "## Verified",
        relative_corpus,
        str(CORPUS.resolve()),
    )

    assert completed.returncode == 2
    assert "input resolves to a duplicate file" in completed.stderr
