#!/usr/bin/env python3
"""Thin wrapper used to prove the version-pinned skill contract."""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence

REQUIREMENT = "cowsay==6.1"


def main(argv: Sequence[str] | None = None) -> int:
    executable = shutil.which("cowsay")
    if executable is None:
        print(
            f"wrapper-pin-example: missing required CLI {REQUIREMENT}. "
            f"Install it with: python -m pip install {REQUIREMENT}",
            file=sys.stderr,
        )
        return 2

    completed = subprocess.run([executable, *(argv or sys.argv[1:])], check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
