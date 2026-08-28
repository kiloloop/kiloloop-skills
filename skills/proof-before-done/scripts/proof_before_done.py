#!/usr/bin/env python3
"""Execute declared completion checks and print paste-ready receipts."""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


EXIT_VERIFIED = 0
EXIT_CLAIM_NOT_VERIFIED = 1
EXIT_INPUT_ERROR = 2


@dataclass(frozen=True)
class Receipt:
    status: str
    claim: str
    command: str
    exit_code: str
    observed: str
    timestamp: str

    def render(self) -> str:
        return (
            f"{self.status}"
            f" · claim={json.dumps(self.claim, ensure_ascii=False)}"
            f" · command={self.command}"
            f" · exit_code={self.exit_code}"
            f" · observed={json.dumps(self.observed, ensure_ascii=False)}"
            f" · timestamp={self.timestamp}"
        )


def _timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _command_text(arguments: Sequence[str]) -> str:
    return "EXEC " + json.dumps(list(arguments), ensure_ascii=False, separators=(",", ":"))


def _static_text(name: str, **arguments: str) -> str:
    encoded = ",".join(
        f"{key}={json.dumps(value, ensure_ascii=False)}"
        for key, value in arguments.items()
    )
    return f"STATIC {name}({encoded})"


def _flatten_output(value: str) -> str:
    if not value:
        return "<empty>"
    return value


def _unverified(
    claim: str,
    reason: str,
    *,
    command: str = "NOT_EXECUTED",
) -> Receipt:
    return Receipt(
        status="UNVERIFIED",
        claim=claim,
        command=command,
        exit_code="N/A",
        observed=reason,
        timestamp=_timestamp(),
    )


def _command_arguments(raw: object) -> list[str] | None:
    if not isinstance(raw, list) or not raw:
        return None
    if not all(isinstance(value, str) and value for value in raw):
        return None
    return list(raw)


def _run_command(
    claim: str,
    arguments: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: float,
) -> Receipt:
    command = _command_text(arguments)
    try:
        completed = subprocess.run(
            arguments,
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout or ""
        stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr or ""
        observed = (
            f"timed_out_after={timeout_seconds:g}s; "
            f"stdout={_flatten_output(stdout)}; stderr={_flatten_output(stderr)}"
        )
        return Receipt("FAIL", claim, command, "N/A", observed, _timestamp())
    except OSError as error:
        return _unverified(
            claim,
            f"could_not_execute={error}",
            command=command,
        )

    observed = (
        f"stdout={_flatten_output(completed.stdout)}; "
        f"stderr={_flatten_output(completed.stderr)}"
    )
    status = "PASS" if completed.returncode == 0 else "FAIL"
    return Receipt(
        status,
        claim,
        command,
        str(completed.returncode),
        observed,
        _timestamp(),
    )


def _display_path(raw_path: str) -> str:
    return Path(raw_path).as_posix()


def _resolved_path(base: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else base / path


def _check_file_exists(claim: str, raw_path: object, *, base: Path) -> Receipt:
    if not isinstance(raw_path, str) or not raw_path:
        return _unverified(claim, "file_exists requires a non-empty path")
    display_path = _display_path(raw_path)
    path = _resolved_path(base, raw_path)
    command = _static_text("file_exists", path=display_path)
    try:
        mode = path.stat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        exists = False
    except (OSError, ValueError) as error:
        return _unverified(
            claim,
            f"could_not_stat={error}",
            command=command,
        )
    else:
        exists = stat.S_ISREG(mode)
    return Receipt(
        "PASS" if exists else "FAIL",
        claim,
        command,
        "N/A",
        f"regular_file={str(exists).lower()}",
        _timestamp(),
    )


def _check_string_present(
    claim: str,
    raw_path: object,
    raw_string: object,
    *,
    base: Path,
) -> Receipt:
    if not isinstance(raw_path, str) or not raw_path:
        return _unverified(claim, "string_present requires a non-empty path")
    if not isinstance(raw_string, str):
        return _unverified(claim, "string_present requires a string value")

    display_path = _display_path(raw_path)
    path = _resolved_path(base, raw_path)
    command = _static_text("string_present", path=display_path, string=raw_string)
    try:
        contents = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        return _unverified(
            claim,
            f"could_not_read={error}",
            command=command,
        )

    present = raw_string in contents
    return Receipt(
        "PASS" if present else "FAIL",
        claim,
        command,
        "N/A",
        f"string_present={str(present).lower()}",
        _timestamp(),
    )


def _check_git_ref_contains(
    claim: str,
    item: Mapping[str, object],
    *,
    base: Path,
    timeout_seconds: float,
) -> Receipt:
    commit = item.get("commit")
    ref = item.get("ref")
    raw_repo = item.get("repo", ".")
    if not isinstance(commit, str) or not commit:
        return _unverified(claim, "git_ref_contains requires a non-empty commit")
    if not isinstance(ref, str) or not ref:
        return _unverified(claim, "git_ref_contains requires a non-empty ref")
    if not isinstance(raw_repo, str) or not raw_repo:
        return _unverified(claim, "git_ref_contains requires a non-empty repo path")

    repo = _resolved_path(base, raw_repo)
    arguments = [
        "git",
        "-C",
        str(repo),
        "merge-base",
        "--is-ancestor",
        commit,
        ref,
    ]
    return _run_command(
        claim,
        arguments,
        cwd=base,
        timeout_seconds=timeout_seconds,
    )


def _evaluate_claim(
    raw_item: object,
    *,
    base: Path,
    timeout_seconds: float,
) -> Receipt:
    if not isinstance(raw_item, Mapping):
        return _unverified("<missing claim>", "claim entry must be an object")

    raw_claim = raw_item.get("claim")
    claim = raw_claim if isinstance(raw_claim, str) and raw_claim else "<missing claim>"
    predicate = raw_item.get("type")
    if not isinstance(predicate, str) or not predicate:
        return _unverified(claim, "no predicate supplied")

    if predicate in {"tests_pass", "command_exit_zero"}:
        arguments = _command_arguments(raw_item.get("command"))
        if arguments is None:
            return _unverified(claim, f"{predicate} requires a non-empty command array")
        return _run_command(
            claim,
            arguments,
            cwd=base,
            timeout_seconds=timeout_seconds,
        )
    if predicate == "file_exists":
        return _check_file_exists(claim, raw_item.get("path"), base=base)
    if predicate == "string_present":
        return _check_string_present(
            claim,
            raw_item.get("path"),
            raw_item.get("string"),
            base=base,
        )
    if predicate == "git_ref_contains":
        return _check_git_ref_contains(
            claim,
            raw_item,
            base=base,
            timeout_seconds=timeout_seconds,
        )
    return _unverified(claim, f"unsupported predicate type: {predicate}")


def _load_claims(path: Path) -> list[object]:
    if not path.is_file():
        raise ValueError(f"claims file is not a regular file: {path}")
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"could not read claims JSON: {error}") from error
    if not isinstance(payload, Mapping):
        raise ValueError("claims JSON must be an object")
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError("claims JSON must contain a non-empty claims array")
    return claims


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claims_file", help="JSON file containing completion claims")
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=60.0,
        help="maximum runtime for each external command (default: 60)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.timeout_seconds <= 0:
        print("proof-before-done: --timeout-seconds must be greater than zero", file=sys.stderr)
        return EXIT_INPUT_ERROR

    claims_file = Path(args.claims_file)
    try:
        claims = _load_claims(claims_file)
    except ValueError as error:
        print(f"proof-before-done: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    base = claims_file.resolve().parent
    receipts = [
        _evaluate_claim(
            item,
            base=base,
            timeout_seconds=args.timeout_seconds,
        )
        for item in claims
    ]
    for receipt in receipts:
        print(receipt.render())
    return (
        EXIT_VERIFIED
        if all(receipt.status == "PASS" for receipt in receipts)
        else EXIT_CLAIM_NOT_VERIFIED
    )


if __name__ == "__main__":
    raise SystemExit(main())
