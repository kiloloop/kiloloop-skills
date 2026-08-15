from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_skill_fixtures.py"
WRAPPER_SKILL = ROOT / "tests" / "fixtures" / "wrapper-skill"


def run_harness(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )


def make_skill(root: Path, name: str = "example") -> Path:
    skill = root / "skills" / name
    (skill / "scripts").mkdir(parents=True)
    (skill / "fixtures").mkdir()
    (skill / "SKILL.md").write_text("# Example\n", encoding="utf-8")
    (skill / "scripts" / "claim.py").write_text("VALUE = 4\n", encoding="utf-8")
    return skill


def copy_wrapper_skill(root: Path) -> Path:
    destination = root / "skills" / "wrapper-pin-example"
    destination.parent.mkdir(parents=True)
    shutil.copytree(WRAPPER_SKILL, destination)
    return destination


def test_empty_skills_directory_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()

    result = run_harness(tmp_path)

    assert result.returncode == 0
    assert "empty fixture gate passes" in result.stdout


def test_valid_skill_fixture_is_discovered_and_run(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    (skill / "fixtures" / "test_claim.py").write_text(
        "def test_embedded_claim():\n    assert 2 + 2 == 4\n",
        encoding="utf-8",
    )

    result = run_harness(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Running 1 fixture file(s) from 1 skill(s)." in result.stdout
    assert "1 passed" in result.stdout


def test_missing_embedded_script_fails_structure_gate(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    (skill / "scripts" / "claim.py").unlink()
    (skill / "scripts" / ".gitkeep").touch()
    (skill / "fixtures" / "test_claim.py").write_text(
        "def test_claim():\n    assert True\n",
        encoding="utf-8",
    )

    result = run_harness(tmp_path)

    assert result.returncode == 1
    assert "scripts/ must contain an embedded implementation" in result.stderr


def test_failing_fixture_propagates_failure(tmp_path: Path) -> None:
    skill = make_skill(tmp_path)
    (skill / "fixtures" / "test_claim.py").write_text(
        "def test_claim():\n    assert False\n",
        encoding="utf-8",
    )

    result = run_harness(tmp_path)

    assert result.returncode != 0
    assert "1 failed" in result.stdout


def test_wrapper_form_resolves_exact_pin_and_runs_clean_fixture(tmp_path: Path) -> None:
    copy_wrapper_skill(tmp_path)

    result = run_harness(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Running 1 fixture file(s) from 1 skill(s)." in result.stdout
    assert "missing-dependency probe passed" in result.stdout
    assert "resolved cowsay==6.1" in result.stdout
    assert "wrapper smoke passed" in result.stdout
    assert "1 passed" in result.stdout


def test_wrapper_form_rejects_floating_requirement_before_install(
    tmp_path: Path,
) -> None:
    skill = copy_wrapper_skill(tmp_path)
    skill_file = skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace("cowsay==6.1", "cowsay>=6.1"),
        encoding="utf-8",
    )

    result = run_harness(tmp_path)

    assert result.returncode == 1
    assert "must be an exact pin like package==1.2.3" in result.stderr
    assert (
        "ranges, wildcards, URLs, paths, and floating versions are not allowed"
        in result.stderr
    )


def test_wrapper_form_requires_actionable_missing_dependency_error(
    tmp_path: Path,
) -> None:
    skill = copy_wrapper_skill(tmp_path)
    wrapper = skill / "scripts" / "cowsay_wrapper.py"
    wrapper.write_text(
        wrapper.read_text(encoding="utf-8").replace(
            'f"Install it with: python -m pip install {REQUIREMENT}"',
            '"Install the dependency before retrying"',
        ),
        encoding="utf-8",
    )

    result = run_harness(tmp_path)

    assert result.returncode == 1
    assert (
        "missing-dependency output must include `python -m pip install`"
        in result.stderr
    )
    assert "every exact requires pin" in result.stderr


def test_wrapper_form_requires_runnable_install_instruction(tmp_path: Path) -> None:
    skill = copy_wrapper_skill(tmp_path)
    skill_file = skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(
            "python -m pip install cowsay==6.1",
            "install the declared dependency",
        ),
        encoding="utf-8",
    )

    result = run_harness(tmp_path)

    assert result.returncode == 1
    assert "SKILL.md must include runnable install instruction" in result.stderr
    assert "python -m pip install cowsay==6.1" in result.stderr


def test_wrapper_form_requires_python_entrypoint(tmp_path: Path) -> None:
    skill = copy_wrapper_skill(tmp_path)
    skill_file = skill / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(
            "scripts/cowsay_wrapper.py",
            "scripts/cowsay_wrapper.sh",
        ),
        encoding="utf-8",
    )
    (skill / "scripts" / "cowsay_wrapper.py").rename(
        skill / "scripts" / "cowsay_wrapper.sh"
    )

    result = run_harness(tmp_path)

    assert result.returncode == 1
    assert (
        "wrapper.entrypoint must be a contained Python path under scripts/"
        in result.stderr
    )
