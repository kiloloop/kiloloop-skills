#!/usr/bin/env python3
"""Validate self-contained skill units and run their fixture tests."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import venv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

SCRIPT_SUFFIXES = frozenset({".js", ".pl", ".ps1", ".py", ".rb", ".sh", ".ts"})
PUBLIC_INDEX_URL = "https://pypi.org/simple"
EXACT_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$"
)


@dataclass(frozen=True)
class RequirementPin:
    raw: str
    name: str
    version: str


@dataclass(frozen=True)
class WrapperContract:
    requirements: tuple[RequirementPin, ...]
    entrypoint: Path
    smoke_args: tuple[str, ...]


def load_skill_frontmatter(skill: Path) -> tuple[Mapping[str, object], str, list[str]]:
    skill_file = skill / "SKILL.md"
    if not skill_file.is_file():
        return {}, "", []

    text = skill_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, []

    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing is None:
        return {}, text, [f"{skill.name}: SKILL.md has unclosed YAML frontmatter"]

    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing]))
    except yaml.YAMLError as error:
        return (
            {},
            text,
            [f"{skill.name}: SKILL.md frontmatter is invalid YAML: {error}"],
        )
    if metadata is None:
        return {}, text, []
    if not isinstance(metadata, Mapping):
        return {}, text, [f"{skill.name}: SKILL.md frontmatter must be a mapping"]
    return metadata, text, []


def _normalized_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirement_pins(
    skill: Path, raw_requires: object
) -> tuple[list[RequirementPin], list[str]]:
    if not isinstance(raw_requires, list) or not raw_requires:
        return [], [
            f"{skill.name}: requires must be a non-empty list of exact package==version pins"
        ]

    pins: list[RequirementPin] = []
    errors: list[str] = []
    seen: set[str] = set()
    for value in raw_requires:
        if not isinstance(value, str) or not (
            match := EXACT_REQUIREMENT.fullmatch(value)
        ):
            errors.append(
                f"{skill.name}: requires entry {value!r} must be an exact pin like package==1.2.3; "
                "ranges, wildcards, URLs, paths, and floating versions are not allowed"
            )
            continue
        name = match.group("name")
        normalized = _normalized_distribution_name(name)
        if normalized in seen:
            errors.append(f"{skill.name}: duplicate requires entry for {name}")
            continue
        seen.add(normalized)
        pins.append(
            RequirementPin(raw=value, name=name, version=match.group("version"))
        )
    return pins, errors


def parse_wrapper_contract(
    skill: Path,
    metadata: Mapping[str, object],
    skill_text: str,
) -> tuple[WrapperContract | None, list[str]]:
    has_requires = "requires" in metadata
    has_wrapper = "wrapper" in metadata
    if not has_requires and not has_wrapper:
        return None, []

    errors: list[str] = []
    if not has_requires:
        errors.append(f"{skill.name}: wrapper form must declare requires")
    if not has_wrapper:
        errors.append(f"{skill.name}: requires is only valid with wrapper metadata")

    pins, pin_errors = parse_requirement_pins(skill, metadata.get("requires"))
    errors.extend(pin_errors)
    for pin in pins:
        install_command = f"python -m pip install {pin.raw}"
        if install_command not in skill_text:
            errors.append(
                f"{skill.name}: SKILL.md must include runnable install instruction `{install_command}`"
            )

    wrapper = metadata.get("wrapper")
    if not isinstance(wrapper, Mapping):
        errors.append(f"{skill.name}: wrapper must be a mapping")
        return None, errors

    raw_entrypoint = wrapper.get("entrypoint")
    entrypoint = Path(raw_entrypoint) if isinstance(raw_entrypoint, str) else None
    if (
        entrypoint is None
        or entrypoint.is_absolute()
        or ".." in entrypoint.parts
        or "\\" in str(entrypoint)
        or not entrypoint.parts
        or entrypoint.parts[0] != "scripts"
        or entrypoint.suffix != ".py"
    ):
        errors.append(
            f"{skill.name}: wrapper.entrypoint must be a contained Python path under scripts/"
        )
    elif not (skill / entrypoint).is_file():
        errors.append(f"{skill.name}: wrapper.entrypoint does not exist: {entrypoint}")

    raw_smoke_args = wrapper.get("smoke_args")
    if (
        not isinstance(raw_smoke_args, list)
        or not raw_smoke_args
        or not all(isinstance(value, str) for value in raw_smoke_args)
    ):
        errors.append(
            f"{skill.name}: wrapper.smoke_args must be a non-empty list of strings"
        )

    if errors or entrypoint is None or not isinstance(raw_smoke_args, list):
        return None, errors
    return WrapperContract(
        requirements=tuple(pins),
        entrypoint=entrypoint,
        smoke_args=tuple(raw_smoke_args),
    ), []


def is_embedded_script(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.startswith(".")
        and (
            path.suffix.lower() in SCRIPT_SUFFIXES or bool(path.stat().st_mode & 0o111)
        )
    )


def discover_skills(root: Path) -> list[Path]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        return []
    return sorted(
        path
        for path in skills_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def validate_skill(skill: Path) -> tuple[list[str], list[Path], WrapperContract | None]:
    errors: list[str] = []
    fixture_tests: list[Path] = []

    if not (skill / "SKILL.md").is_file():
        errors.append(f"{skill.name}: missing SKILL.md")
    metadata, skill_text, frontmatter_errors = load_skill_frontmatter(skill)
    errors.extend(frontmatter_errors)
    wrapper_contract, wrapper_errors = parse_wrapper_contract(
        skill, metadata, skill_text
    )
    errors.extend(wrapper_errors)
    wrapper_requested = "requires" in metadata or "wrapper" in metadata

    scripts_dir = skill / "scripts"
    scripts = (
        [path for path in scripts_dir.rglob("*") if is_embedded_script(path)]
        if scripts_dir.is_dir()
        else []
    )
    if not scripts:
        requirement = (
            "a wrapper entrypoint"
            if wrapper_requested
            else "an embedded implementation"
        )
        errors.append(f"{skill.name}: scripts/ must contain {requirement}")

    fixtures_dir = skill / "fixtures"
    if fixtures_dir.is_dir():
        fixture_tests = sorted(fixtures_dir.rglob("test_*.py"))
    if not fixture_tests:
        errors.append(f"{skill.name}: fixtures/ must contain at least one test_*.py")

    return errors, fixture_tests, wrapper_contract


def _clean_wrapper_environment(python: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "PIP_EXTRA_INDEX_URL",
        "PIP_FIND_LINKS",
        "PIP_NO_INDEX",
        "PYTHONHOME",
        "PYTHONPATH",
    ):
        environment.pop(name, None)
    environment["PATH"] = os.pathsep.join((str(python.parent), os.defpath))
    environment["VIRTUAL_ENV"] = str(python.parent.parent)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["PIP_INDEX_URL"] = PUBLIC_INDEX_URL
    environment["PIP_NO_INPUT"] = "1"
    return environment


def _print_process_output(completed: subprocess.CompletedProcess[str]) -> None:
    if completed.stdout:
        print(completed.stdout.rstrip(), file=sys.stderr)
    if completed.stderr:
        print(completed.stderr.rstrip(), file=sys.stderr)


def _run_captured(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout: int,
    failure_label: str,
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"ERROR: {failure_label} timed out after {timeout}s", file=sys.stderr)
    except OSError as error:
        print(f"ERROR: {failure_label} could not run: {error}", file=sys.stderr)
    return None


def run_wrapper_skill(
    root: Path,
    skill: Path,
    contract: WrapperContract,
    fixture_tests: Sequence[Path],
) -> int:
    with tempfile.TemporaryDirectory(prefix=f"{skill.name}-clean-") as temporary:
        environment_root = Path(temporary) / "venv"
        try:
            venv.EnvBuilder(with_pip=True).create(environment_root)
        except Exception as error:  # noqa: BLE001 - report platform venv failures cleanly
            print(
                f"ERROR: {skill.name}: could not create clean environment: {error}",
                file=sys.stderr,
            )
            return 1

        python = environment_root / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        environment = _clean_wrapper_environment(python)
        entrypoint = skill / contract.entrypoint
        smoke_command = [str(python), str(entrypoint), *contract.smoke_args]

        missing = _run_captured(
            smoke_command,
            cwd=skill,
            environment=environment,
            timeout=30,
            failure_label=f"{skill.name}: missing-dependency probe",
        )
        if missing is None:
            return 1

        missing_output = f"{missing.stdout}\n{missing.stderr}"
        if missing.returncode != 2:
            print(
                f"ERROR: {skill.name}: wrapper must exit 2 when its pinned dependency is absent; "
                f"got {missing.returncode}",
                file=sys.stderr,
            )
            _print_process_output(missing)
            return 1
        if "python -m pip install" not in missing_output or any(
            pin.raw not in missing_output for pin in contract.requirements
        ):
            print(
                f"ERROR: {skill.name}: missing-dependency output must include `python -m pip install` "
                "and every exact requires pin",
                file=sys.stderr,
            )
            _print_process_output(missing)
            return 1

        install = _run_captured(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--index-url",
                PUBLIC_INDEX_URL,
                "--no-cache-dir",
                "--no-input",
                *(pin.raw for pin in contract.requirements),
            ],
            cwd=skill,
            environment=environment,
            timeout=180,
            failure_label=f"{skill.name}: public package install",
        )
        if install is None:
            return 1
        if install.returncode != 0:
            pins = ", ".join(pin.raw for pin in contract.requirements)
            print(
                f"ERROR: {skill.name}: pinned public requirement did not resolve/install: {pins}",
                file=sys.stderr,
            )
            _print_process_output(install)
            return 1

        for pin in contract.requirements:
            resolved = _run_captured(
                [
                    str(python),
                    "-c",
                    "from importlib.metadata import version; import sys; print(version(sys.argv[1]))",
                    pin.name,
                ],
                cwd=skill,
                environment=environment,
                timeout=30,
                failure_label=f"{skill.name}: installed-version probe for {pin.name}",
            )
            if resolved is None:
                return 1
            if resolved.returncode != 0 or resolved.stdout.strip() != pin.version:
                actual = resolved.stdout.strip() or "unavailable"
                print(
                    f"ERROR: {skill.name}: resolved {pin.name} version {actual}; expected {pin.version}",
                    file=sys.stderr,
                )
                _print_process_output(resolved)
                return 1

        smoke = _run_captured(
            smoke_command,
            cwd=skill,
            environment=environment,
            timeout=30,
            failure_label=f"{skill.name}: installed wrapper smoke command",
        )
        if smoke is None:
            return 1
        if smoke.returncode != 0:
            print(
                f"ERROR: {skill.name}: wrapper smoke command failed after dependency install",
                file=sys.stderr,
            )
            _print_process_output(smoke)
            return 1

        pins = ", ".join(pin.raw for pin in contract.requirements)
        print(
            f"{skill.name}: missing-dependency probe passed; resolved {pins}; wrapper smoke passed."
        )
        fixture_environment = environment.copy()
        fixture_environment["SKILL_CLEAN_PYTHON"] = str(python)
        fixture_environment["SKILL_ROOT"] = str(skill)
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", *map(str, fixture_tests)],
            cwd=root,
            env=fixture_environment,
            check=False,
        )
        return completed.returncode


def run(root: Path) -> int:
    skills = discover_skills(root)
    if not skills:
        print("No skill ports found; the empty fixture gate passes.")
        return 0

    errors: list[str] = []
    validated: list[tuple[Path, list[Path], WrapperContract | None]] = []
    for skill in skills:
        skill_errors, skill_tests, wrapper_contract = validate_skill(skill)
        errors.extend(skill_errors)
        validated.append((skill, skill_tests, wrapper_contract))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    fixture_count = sum(len(skill_tests) for _, skill_tests, _ in validated)
    print(f"Running {fixture_count} fixture file(s) from {len(skills)} skill(s).")

    embedded_tests: list[Path] = []
    for skill, skill_tests, wrapper_contract in validated:
        if wrapper_contract is None:
            embedded_tests.extend(skill_tests)
            continue
        if run_wrapper_skill(root, skill, wrapper_contract, skill_tests) != 0:
            return 1

    if not embedded_tests:
        return 0
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *map(str, embedded_tests)],
        cwd=root,
        check=False,
    )
    return completed.returncode


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root (default: current working directory)",
    )
    args = parser.parse_args(argv)
    return run(args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
