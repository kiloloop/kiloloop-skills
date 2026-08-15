from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import yaml

CORE_DEFECT_CODES = {
    "broken-link",
    "duplicate-anchor",
    "duplicate-heading",
    "frontmatter-required-key",
    "frontmatter-type-enum",
    "frontmatter-unquoted-value",
    "index-missing-entry",
    "index-missing-target",
    "line-too-long",
    "marker-nested",
    "marker-orphaned",
    "marker-unclosed",
    "stale-updated",
}


def load_wrapper_module() -> ModuleType:
    wrapper_path = (
        Path(__file__).parents[1] / "scripts" / "memory_lint_wrapper.py"
    )
    spec = spec_from_file_location("memory_lint_wrapper_under_test", wrapper_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def materialize_fixture_snapshot(root: Path) -> None:
    manifest = yaml.safe_load(
        (Path(__file__).with_name("corpus.yaml")).read_text(encoding="utf-8")
    )
    files = manifest.get("files") if isinstance(manifest, Mapping) else None
    assert isinstance(files, Mapping)
    for relative_path, content in files.items():
        assert isinstance(relative_path, str)
        assert isinstance(content, str)
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


def run_wrapper(
    *arguments: str,
    environment_overrides: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    clean_python = Path(os.environ["SKILL_CLEAN_PYTHON"])
    skill_root = Path(os.environ["SKILL_ROOT"])
    wrapper = skill_root / "scripts" / "memory_lint_wrapper.py"
    environment = os.environ.copy()
    if environment_overrides is not None:
        environment.update(environment_overrides)
    return subprocess.run(
        [str(clean_python), str(wrapper), *arguments],
        cwd=skill_root,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def test_clean_and_defect_corpora_through_wrapper(tmp_path: Path) -> None:
    snapshot = tmp_path / "fixture-snapshot"
    materialize_fixture_snapshot(snapshot)
    config = snapshot / "config.yaml"

    clean = run_wrapper(
        "--config",
        str(config),
        "--corpus-root",
        str(snapshot / "fixtures" / "clean"),
        "--format",
        "json",
        "--now",
        "2026-08-10",
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    clean_payload = json.loads(clean.stdout)
    assert clean_payload["finding_count"] == 0

    defects = run_wrapper(
        "--config",
        str(config),
        "--corpus-root",
        str(snapshot / "fixtures" / "defects"),
        "--format",
        "json",
        "--now",
        "2026-08-10",
    )
    assert defects.returncode == 1, defects.stdout + defects.stderr
    defect_payload = json.loads(defects.stdout)
    codes = {finding["code"] for finding in defect_payload["findings"]}
    assert CORE_DEFECT_CODES <= codes


def test_revision_pairs_through_wrapper(tmp_path: Path) -> None:
    snapshot = tmp_path / "fixture-snapshot"
    materialize_fixture_snapshot(snapshot)
    config = snapshot / "config.yaml"
    revisions = snapshot / "fixtures" / "revisions"

    pairs = (
        ("identical", "noop-identical"),
        ("whitespace", "noop-whitespace-only"),
    )
    for stem, expected_code in pairs:
        completed = run_wrapper(
            "--config",
            str(config),
            "--format",
            "json",
            "--now",
            "2026-08-10",
            "--compare-before",
            str(revisions / f"{stem}-before.md"),
            "--compare-after",
            str(revisions / f"{stem}-after.md"),
        )
        assert completed.returncode == 1, completed.stdout + completed.stderr
        payload = json.loads(completed.stdout)
        assert [finding["code"] for finding in payload["findings"]] == [
            expected_code
        ]


def test_partial_install_is_a_dependency_error(monkeypatch, capsys) -> None:
    wrapper = load_wrapper_module()
    monkeypatch.setattr(wrapper, "version", lambda _distribution: "0.1.0")
    monkeypatch.setattr(wrapper, "find_spec", lambda _module: None)

    assert wrapper.main([]) == 2
    captured = capsys.readouterr()
    assert "required module memory_lint is not importable" in captured.err
    assert "python -m pip install memory-lint==0.1.0" in captured.err


def test_signal_termination_is_an_execution_error(monkeypatch, capsys) -> None:
    wrapper = load_wrapper_module()
    monkeypatch.setattr(wrapper, "version", lambda _distribution: "0.1.0")
    monkeypatch.setattr(wrapper, "find_spec", lambda _module: object())
    monkeypatch.setattr(wrapper, "_module_import_error", lambda: None)
    monkeypatch.setattr(
        wrapper.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], -15),
    )

    assert wrapper.main([]) == 2
    captured = capsys.readouterr()
    assert "delegated CLI terminated by signal 15" in captured.err


def test_broken_import_is_a_dependency_error(tmp_path: Path) -> None:
    broken_root = tmp_path / "broken-import"
    broken_package = broken_root / "memory_lint"
    broken_package.mkdir(parents=True)
    (broken_package / "__init__.py").write_text(
        'raise RuntimeError("synthetic broken install")\n',
        encoding="utf-8",
    )

    completed = run_wrapper(
        "--version",
        environment_overrides={"PYTHONPATH": str(broken_root)},
    )

    assert completed.returncode == 2
    assert "required module memory_lint failed its import probe" in completed.stderr
    assert "RuntimeError: synthetic broken install" in completed.stderr
    assert "python -m pip install memory-lint==0.1.0" in completed.stderr
