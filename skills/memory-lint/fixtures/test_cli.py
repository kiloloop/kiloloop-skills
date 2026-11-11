from __future__ import annotations

import json
import os
import py_compile
import shutil
import subprocess
import zipfile
from collections.abc import Mapping
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest
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
    cwd: Path | None = None,
    python: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    clean_python = Path(os.environ["SKILL_CLEAN_PYTHON"]) if python is None else python
    skill_root = Path(os.environ["SKILL_ROOT"])
    wrapper = skill_root / "scripts" / "memory_lint_wrapper.py"
    environment = os.environ.copy()
    if environment_overrides is not None:
        environment.update(environment_overrides)
    return subprocess.run(
        [str(clean_python), str(wrapper), *arguments],
        cwd=skill_root if cwd is None else cwd,
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


def _purelib(python: Path) -> Path:
    completed = subprocess.run(
        [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        text=True, capture_output=True, check=True,
    )
    return Path(completed.stdout.strip())


def test_a_broken_installation_is_a_dependency_error(tmp_path: Path) -> None:
    """An installed release whose import fails exits 2, and nothing is delegated.

    The clean environment's installation is shared by every fixture and stays
    untouched: the release is copied into a private environment built from the
    same interpreter, and only that copy is broken. The copy's package records
    each import, so a delegated run would leave a second line.
    """
    clean_python = Path(os.environ["SKILL_CLEAN_PYTHON"])
    environment = tmp_path / "broken-env"
    subprocess.run(
        [str(clean_python), "-m", "venv", "--without-pip", str(environment)],
        check=True, capture_output=True,
    )
    broken_python = environment / "bin" / "python"
    source, target = _purelib(clean_python), _purelib(broken_python)
    shutil.copytree(source / "memory_lint", target / "memory_lint")
    (metadata,) = source.glob("memory_lint-0.1.0.dist-info")
    shutil.copytree(metadata, target / metadata.name)
    imports = tmp_path / "imports"
    (target / "memory_lint" / "__init__.py").write_text(
        f"open({str(imports)!r}, 'a').write('import\\n')\n"
        'raise RuntimeError("synthetic broken installation")\n',
        encoding="utf-8",
    )

    completed = run_wrapper("--version", cwd=tmp_path, python=broken_python)

    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert "required module memory_lint failed its import probe" in completed.stderr
    assert "RuntimeError: synthetic broken installation" in completed.stderr
    assert "python -m pip install memory-lint==0.1.0" in completed.stderr
    # One import, by the probe; the delegated command never ran.
    assert imports.read_text(encoding="utf-8") == "import\n"


SHADOW_METADATA = "Metadata-Version: 2.1\nName: memory-lint\nVersion: 0.1.0\n"


def _write_shadow(root: Path, form: str, sentinel: Path) -> None:
    """A copy of memory_lint whose every code path records that it ran."""
    root.mkdir(parents=True, exist_ok=True)

    def records(where: str) -> str:
        return f"open({str(sentinel)!r}, 'a').write({where!r} + '\\n')\n"

    if form == "module":
        (root / "memory_lint.py").write_text(
            records("memory_lint.py") + 'print("SHADOW RAN")\n', encoding="utf-8"
        )
        return
    if form == "sourceless":
        source = root.parent / "memory_lint_source.py"
        source.write_text(records("memory_lint.pyc") + 'print("SHADOW RAN")\n', encoding="utf-8")
        py_compile.compile(str(source), cfile=str(root / "memory_lint.pyc"), doraise=True)
        source.unlink()
        return
    package = root / "memory_lint"
    package.mkdir()
    (package / "__init__.py").write_text(
        '__version__ = "0.1.0"\n' + records("__init__"), encoding="utf-8"
    )
    (package / "__main__.py").write_text(
        records("__main__") + 'print("SHADOW RAN")\n', encoding="utf-8"
    )
    if form == "package+egg-info":
        (root / "memory_lint.egg-info").mkdir()
        (root / "memory_lint.egg-info" / "PKG-INFO").write_text(
            SHADOW_METADATA, encoding="utf-8"
        )
    elif form == "package+dist-info":
        (root / "memory_lint-0.1.0.dist-info").mkdir()
        (root / "memory_lint-0.1.0.dist-info" / "METADATA").write_text(
            SHADOW_METADATA, encoding="utf-8"
        )


@pytest.mark.parametrize(
    "form", ["package", "package+egg-info", "package+dist-info", "module", "sourceless"]
)
@pytest.mark.parametrize("shadow_via", ["PYTHONPATH", "working directory"])
def test_a_shadowing_memory_lint_is_refused_before_any_of_it_runs(
    tmp_path: Path, shadow_via: str, form: str
) -> None:
    """A copy ahead of the installation is refused, and none of its code runs.

    Matching build metadata beside the copy, as in a source checkout, must not
    make it pass for the installation.
    """
    shadow_root = tmp_path / "shadow"
    sentinel = tmp_path / "shadow-code-ran"
    _write_shadow(shadow_root, form, sentinel)

    if shadow_via == "PYTHONPATH":
        completed = run_wrapper(
            "--version",
            environment_overrides={"PYTHONPATH": str(shadow_root)},
            cwd=tmp_path,
        )
    else:
        completed = run_wrapper("--version", cwd=shadow_root)

    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert not sentinel.exists(), sentinel.read_text(encoding="utf-8")
    assert "SHADOW RAN" not in completed.stdout
    assert "memory_lint resolves to" in completed.stderr
    assert "not the installed memory-lint==0.1.0" in completed.stderr
    assert "python -m pip install memory-lint==0.1.0" in completed.stderr


def test_a_zipped_shadow_on_pythonpath_is_refused_before_any_of_it_runs(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "shadow-code-ran"
    archive = tmp_path / "shadow.zip"
    record = f"open({str(sentinel)!r}, 'a').write('zip\\n')\n"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("memory_lint/__init__.py", record)
        bundle.writestr("memory_lint/__main__.py", record + 'print("SHADOW RAN")\n')
        bundle.writestr("memory_lint-0.1.0.dist-info/METADATA", SHADOW_METADATA)

    completed = run_wrapper(
        "--version", environment_overrides={"PYTHONPATH": str(archive)}, cwd=tmp_path
    )

    assert completed.returncode == 2, completed.stdout + completed.stderr
    assert not sentinel.exists()
    assert "memory_lint resolves to" in completed.stderr


def test_a_directory_that_does_not_shadow_the_installation_is_not_refused(
    tmp_path: Path,
) -> None:
    """A `memory_lint` directory without `__init__.py` is at most a namespace
    portion, and an installed regular package takes precedence over it, so the
    delegated command still runs the pinned release."""
    (tmp_path / "memory_lint").mkdir()
    (tmp_path / "memory_lint" / "notes.txt").write_text("data\n", encoding="utf-8")

    completed = run_wrapper("--version", cwd=tmp_path)

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "0.1.0" in completed.stdout
