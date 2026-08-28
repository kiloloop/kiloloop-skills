from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "proof_before_done.py"
DATA = Path(__file__).resolve().parent / "data"
SEEDED_CLAIMS = DATA / "claims.json"
TIMESTAMP = re.compile(r"timestamp=\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def run_checker(claims_file: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments, str(claims_file)],
        cwd=SKILL_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def run_git(repo: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout.strip()


def test_seeded_receipts_expose_false_and_unverified_claims() -> None:
    completed = run_checker(SEEDED_CLAIMS)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    receipts = completed.stdout.splitlines()
    assert len(receipts) == 5
    assert all(TIMESTAMP.search(receipt) for receipt in receipts)

    assert receipts[0].startswith('PASS · claim="command exits 0" · command=EXEC ')
    assert '· exit_code=0 · observed="stdout=ready\\n; stderr=<empty>"' in receipts[0]

    assert receipts[1].startswith('FAIL · claim="tests pass" · command=EXEC ')
    assert '· exit_code=1 ' in receipts[1]
    assert "stderr=intentional failure" in receipts[1]

    assert receipts[2].startswith('PASS · claim="fixture file exists"')
    assert 'command=STATIC file_exists(path="present.txt")' in receipts[2]
    assert 'observed="regular_file=true"' in receipts[2]

    assert receipts[3].startswith(
        'PASS · claim="fixture contains the verified marker"'
    )
    assert 'command=STATIC string_present(path="present.txt",string="verification-ready")' in receipts[3]
    assert 'observed="string_present=true"' in receipts[3]

    assert receipts[4].startswith(
        'UNVERIFIED · claim="reviewed head is current" · command=NOT_EXECUTED'
    )
    assert 'observed="no predicate supplied"' in receipts[4]


def test_git_ref_contains_commit_uses_git_ancestry(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-q")
    run_git(repo, "config", "user.name", "Fixture Author")
    run_git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "evidence.txt").write_text("evidence\n", encoding="utf-8")
    run_git(repo, "add", "evidence.txt")
    run_git(repo, "commit", "-qm", "fixture commit")
    commit = run_git(repo, "rev-parse", "HEAD")

    claims_file = repo / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "git ref contains commit",
                        "type": "git_ref_contains",
                        "repo": ".",
                        "commit": commit,
                        "ref": "HEAD",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = run_checker(claims_file)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.startswith('PASS · claim="git ref contains commit"')
    assert '"merge-base","--is-ancestor"' in completed.stdout
    assert '· exit_code=0 ' in completed.stdout


def test_missing_command_is_unverified_not_silently_passed(tmp_path: Path) -> None:
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "tests pass",
                        "type": "tests_pass",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = run_checker(claims_file)

    assert completed.returncode == 1
    assert completed.stdout.startswith('UNVERIFIED · claim="tests pass"')
    assert "tests_pass requires a non-empty command array" in completed.stdout


def test_missing_runner_is_unverified_with_the_attempted_command(
    tmp_path: Path,
) -> None:
    missing_runner = tmp_path / "missing-runner"
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "tests pass",
                        "type": "tests_pass",
                        "command": [str(missing_runner), "--check"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = run_checker(claims_file)

    assert completed.returncode == 1
    assert completed.stdout.startswith('UNVERIFIED · claim="tests pass"')
    assert 'command=EXEC ["' in completed.stdout
    assert str(missing_runner) in completed.stdout
    assert ',"--check"]' in completed.stdout
    assert '· exit_code=N/A ' in completed.stdout
    assert "could_not_execute=" in completed.stdout


def test_unreadable_utf8_is_unverified_with_the_static_predicate(
    tmp_path: Path,
) -> None:
    binary_file = tmp_path / "binary.dat"
    binary_file.write_bytes(b"\xff")
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "marker is present",
                        "type": "string_present",
                        "path": "binary.dat",
                        "string": "ready",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = run_checker(claims_file)

    assert completed.returncode == 1
    assert completed.stdout.startswith('UNVERIFIED · claim="marker is present"')
    assert (
        'command=STATIC string_present(path="binary.dat",string="ready")'
        in completed.stdout
    )
    assert '· exit_code=N/A ' in completed.stdout
    assert "could_not_read=" in completed.stdout


def test_timeout_is_fail_without_an_invented_exit_code(tmp_path: Path) -> None:
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "slow command finishes",
                        "type": "command_exit_zero",
                        "command": [
                            sys.executable,
                            "-c",
                            "import time; time.sleep(1)",
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = run_checker(claims_file, "--timeout-seconds", "0.01")

    assert completed.returncode == 1
    assert completed.stdout.startswith('FAIL · claim="slow command finishes"')
    assert '· exit_code=N/A ' in completed.stdout
    assert "timed_out_after=0.01s" in completed.stdout


def test_invalid_claims_document_has_distinct_input_exit(tmp_path: Path) -> None:
    claims_file = tmp_path / "claims.json"
    claims_file.write_text('{"claims": []}\n', encoding="utf-8")

    completed = run_checker(claims_file)

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "claims JSON must contain a non-empty claims array" in completed.stderr


def test_untraversable_path_is_unverified_for_both_static_predicates(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX directory permissions are needed to make a path untraversable")
    if os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions, so the path stays traversable")

    locked = tmp_path / "locked"
    locked.mkdir()
    target = locked / "target.txt"
    target.write_text("verification-ready\n", encoding="utf-8")
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "file exists",
                        "type": "file_exists",
                        "path": "locked/target.txt",
                    },
                    {
                        "claim": "marker is present",
                        "type": "string_present",
                        "path": "locked/target.txt",
                        "string": "verification-ready",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    locked.chmod(0)
    try:
        try:
            target.stat()
        except PermissionError:
            pass
        else:
            pytest.skip("this filesystem did not enforce directory permissions")
        completed = run_checker(claims_file)
    finally:
        locked.chmod(0o700)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    receipts = completed.stdout.splitlines()
    assert len(receipts) == 2
    assert receipts[0].startswith(
        'UNVERIFIED · claim="file exists"'
        ' · command=STATIC file_exists(path="locked/target.txt") · exit_code=N/A '
    )
    assert "could_not_stat=" in receipts[0]
    assert receipts[1].startswith(
        'UNVERIFIED · claim="marker is present"'
        ' · command=STATIC string_present(path="locked/target.txt",string="verification-ready")'
        " · exit_code=N/A "
    )
    assert "could_not_read=" in receipts[1]


def test_absent_path_stays_fail_not_unverified(tmp_path: Path) -> None:
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "artifact exists",
                        "type": "file_exists",
                        "path": "missing/artifact.json",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = run_checker(claims_file)

    assert completed.returncode == 1
    assert completed.stdout.startswith('FAIL · claim="artifact exists"')
    assert 'command=STATIC file_exists(path="missing/artifact.json")' in completed.stdout
    assert 'observed="regular_file=false"' in completed.stdout


def test_path_with_embedded_nul_is_unverified_for_both_static_predicates(
    tmp_path: Path,
) -> None:
    claims_file = tmp_path / "claims.json"
    claims_file.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "claim": "file exists",
                        "type": "file_exists",
                        "path": "nul\u0000byte.txt",
                    },
                    {
                        "claim": "marker is present",
                        "type": "string_present",
                        "path": "nul\u0000byte.txt",
                        "string": "ready",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    completed = run_checker(claims_file)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    receipts = completed.stdout.splitlines()
    assert len(receipts) == 2
    assert receipts[0].startswith('UNVERIFIED · claim="file exists"')
    assert "could_not_stat=" in receipts[0]
    assert receipts[1].startswith('UNVERIFIED · claim="marker is present"')
    assert "could_not_read=" in receipts[1]
