#!/usr/bin/env python3
"""Run the exact memory-lint release declared by this skill."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from importlib.metadata import PackageNotFoundError, version
from importlib.util import find_spec

DISTRIBUTION = "memory-lint"
MODULE = "memory_lint"
REQUIRED_VERSION = "0.1.0"
REQUIREMENT = f"{DISTRIBUTION}=={REQUIRED_VERSION}"
INSTALL_COMMAND = f"python -m pip install {REQUIREMENT}"


def _dependency_error(detail: str) -> int:
    print(
        f"memory-lint skill: {detail}. Install it with: {INSTALL_COMMAND}",
        file=sys.stderr,
    )
    return 2


def _module_import_error() -> str | None:
    completed = subprocess.run(
        [sys.executable, "-c", f"import {MODULE}.__main__"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode == 0:
        return None

    last_line = next(
        (line.strip() for line in reversed(completed.stderr.splitlines()) if line.strip()),
        "",
    )
    if last_line:
        return last_line
    if completed.returncode < 0:
        return f"import probe terminated by signal {-completed.returncode}"
    return f"import probe exited with status {completed.returncode}"


def main(argv: Sequence[str] | None = None) -> int:
    try:
        installed_version = version(DISTRIBUTION)
    except PackageNotFoundError:
        return _dependency_error(f"missing required CLI {REQUIREMENT}")

    if installed_version != REQUIRED_VERSION:
        return _dependency_error(
            f"installed {DISTRIBUTION}=={installed_version}; required {REQUIREMENT}"
        )

    if find_spec(MODULE) is None:
        return _dependency_error(
            f"required module {MODULE} is not importable for {REQUIREMENT}"
        )
    import_error = _module_import_error()
    if import_error is not None:
        return _dependency_error(
            f"required module {MODULE} failed its import probe for {REQUIREMENT}: "
            f"{import_error}"
        )

    arguments = list(sys.argv[1:] if argv is None else argv)
    completed = subprocess.run(
        [sys.executable, "-m", MODULE, *arguments],
        check=False,
    )
    if completed.returncode < 0:
        print(
            f"memory-lint skill: delegated CLI terminated by signal "
            f"{-completed.returncode}",
            file=sys.stderr,
        )
        return 2
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
