from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_pinned_cli_runs_through_wrapper_in_clean_environment() -> None:
    clean_python = Path(os.environ["SKILL_CLEAN_PYTHON"])
    skill_root = Path(os.environ["SKILL_ROOT"])
    wrapper = skill_root / "scripts" / "cowsay_wrapper.py"

    completed = subprocess.run(
        [str(clean_python), str(wrapper), "--help"],
        cwd=skill_root,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "CLI tool to display text in ASCII art" in completed.stdout
