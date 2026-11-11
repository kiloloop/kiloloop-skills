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

# Where the installed distribution keeps the package, found with neither
# PYTHONPATH nor the working directory on the path: run with -E, and the `-c`
# entry for the working directory is dropped before anything else is imported.
# A source checkout carrying its own build metadata (egg-info, dist-info) is
# therefore never mistaken for the installation.
_INSTALLED_PROBE = """\
import os
import sys

cwd = os.getcwd()
sys.path[:] = [entry for entry in sys.path if entry not in ("", ".", cwd)]

from importlib.metadata import PackageNotFoundError, distribution

try:
    installed = distribution(sys.argv[1])
except PackageNotFoundError:
    sys.exit(f"no {sys.argv[1]} is installed in this interpreter's environment")
if installed.version != sys.argv[2]:
    sys.exit(f"the installed {sys.argv[1]} is {installed.version}")
print(os.path.realpath(installed.locate_file("memory_lint/__init__.py")))
"""

# Runs with the delegated command's interpreter, environment and working
# directory, so it resolves `memory_lint` as that command will. The module is
# located without being imported, and nothing is imported until it is the
# installed file: a copy earlier on the import path is refused before any of
# its code runs.
_RESOLVED_PROBE = """\
import os
import sys
from importlib.util import find_spec

expected = sys.argv[1]
spec = find_spec("memory_lint")
origin = spec.origin if spec is not None else None
actual = os.path.realpath(origin) if origin else "a location with no file"
if actual != expected:
    sys.exit(
        f"memory_lint resolves to {actual}, not the installed {sys.argv[2]} at "
        f"{expected}; run without PYTHONPATH or from another directory"
    )

import memory_lint.__main__
"""


def _dependency_error(detail: str) -> int:
    print(
        f"memory-lint skill: {detail}. Install it with: {INSTALL_COMMAND}",
        file=sys.stderr,
    )
    return 2


def _probe_failure(completed: subprocess.CompletedProcess[str]) -> str:
    last_line = next(
        (line.strip() for line in reversed(completed.stderr.splitlines()) if line.strip()),
        "",
    )
    if last_line:
        return last_line
    if completed.returncode < 0:
        return f"import probe terminated by signal {-completed.returncode}"
    return f"import probe exited with status {completed.returncode}"


def _module_import_error() -> str | None:
    installed = subprocess.run(
        [sys.executable, "-E", "-c", _INSTALLED_PROBE, DISTRIBUTION, REQUIRED_VERSION],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    expected = installed.stdout.strip()
    if installed.returncode != 0 or not expected:
        return _probe_failure(installed)

    resolved = subprocess.run(
        [sys.executable, "-c", _RESOLVED_PROBE, expected, REQUIREMENT],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if resolved.returncode == 0:
        return None
    return _probe_failure(resolved)


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
