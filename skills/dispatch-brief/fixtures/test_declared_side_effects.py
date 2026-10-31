from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "brief_lint.py"
BRIEFS = Path(__file__).resolve().parent / "briefs"

# Hand-derived from fixtures/briefs/: clean.md asks for a merge (L10), a
# commit and a pull request (L13) and declares all three; the under-declared
# twin declares only the commit; publish_undeclared.md declares none and its
# Instruction asks to publish (L6).
UNDER_DECLARED_LINES = [
    '[declared] L10: "merge" asks for merge, which Side effects does not declare',
    '[declared] L13: "pull-request link" asks for pull request, which Side effects does not declare',
    "2 findings",
]
PUBLISH_LINES = [
    '[declared] L6: "publish" asks for publish, which Side effects does not declare',
    "1 finding",
]


def brief(goal: str = "", fence: str = "- Out of scope: the release script.", effects: str = "none") -> str:
    return (
        "## Goal\nA written comparison of two retry strategies for the requester.\n"
        f"{goal}\n\n"
        "## Instruction\nThe how is yours. Say so in the reply where this brief disagrees.\n\n"
        f"## Fence\n{fence}\n\n"
        "## Done\nDone = the comparison sent as the reply.\n\n"
        f"## Context\n- Repo: example-org/example-repo\n- Side effects: {effects}\n"
    )


def run_lint(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=SKILL_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def lint_text(tmp_path: Path, text: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "brief.md"
    path.write_text(text, encoding="utf-8")
    return run_lint(path)


def test_clean_brief_declares_every_side_effect_it_asks_for() -> None:
    completed = run_lint(BRIEFS / "clean.md")

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.splitlines() == ["clean"]
    assert completed.stderr == ""


def test_under_declared_brief_names_each_missing_effect_at_the_asking_line() -> None:
    completed = run_lint(BRIEFS / "under_declared.md")

    assert completed.returncode == 1
    assert completed.stdout.splitlines() == UNDER_DECLARED_LINES


def test_undeclared_publish_is_reported_at_its_line() -> None:
    completed = run_lint(BRIEFS / "publish_undeclared.md")

    assert completed.returncode == 1
    assert completed.stdout.splitlines() == PUBLISH_LINES


@pytest.mark.parametrize(
    ("sentence", "effect"),
    [
        ("Push the notes to a branch.", "commit"),
        ("Open a pull request with the comparison.", "pull request"),
        ("File an issue for each gap you find.", "issue"),
        ("Comment on the tracking issue when done.", "comment"),
        ("Land it yourself after review.", "merge"),
        ("Deploy the winner to staging.", "deploy"),
        ("Publish the notes when done.", "publish"),
    ],
)
def test_each_ask_requires_its_declaration(tmp_path: Path, sentence: str, effect: str) -> None:
    undeclared = lint_text(tmp_path, brief(goal=sentence))
    declared = lint_text(tmp_path, brief(goal=sentence, effects=effect))

    assert undeclared.returncode == 1
    assert f"asks for {effect}, which Side effects does not declare" in undeclared.stdout
    assert f"asks for {effect}," not in declared.stdout, declared.stdout


def test_negated_asks_are_not_declarations(tmp_path: Path) -> None:
    text = brief(
        goal="Do not open a pull request; the comparison is the deliverable.",
        fence="- Out of scope: filing issues, commenting on the tracker.\n- Never push or deploy.",
    )

    completed = lint_text(tmp_path, text)

    assert completed.returncode == 0, completed.stdout
    assert completed.stdout.splitlines() == ["clean"]


@pytest.mark.parametrize(
    "goal",
    [
        "If no issues remain, publish the notes.",
        "Do not merge, but publish the notes.",
        "Do not merge but publish the notes.",
        "Out of scope: the tracker, but publish the notes.",
        "If no issues remain publish the notes.",
        "If no issues remain:\n- publish the notes",
        "Skip the tracker, then publish the notes.",
        "Do not merge, publish the notes.",
        "Do not merge, yet publish the notes.",
        "Do not merge and then publish the notes.",
    ],
)
def test_a_negation_does_not_reach_past_its_sub_clause(tmp_path: Path, goal: str) -> None:
    completed = lint_text(tmp_path, brief(goal=goal))

    assert completed.returncode == 1, completed.stdout
    declared = [line for line in completed.stdout.splitlines() if line.startswith("[declared]")]
    assert [line.split(": ", 1)[1] for line in declared] == [
        '"publish" asks for publish, which Side effects does not declare'
    ]


@pytest.mark.parametrize(
    "goal",
    [
        "Do not publish the notes.",
        "If no issues remain, do not publish the notes.",
        "Unless told otherwise, never publish or deploy.",
        "Out of scope: the tracker, publishing the notes, deploying.",
        "Never deploy, publish, or merge.",
        "Do not commit, push, nor publish the notes.",
        "Do not yet publish the notes.",
        "Not yet:\n- publish the notes",
        "If tests fail do not publish the notes.",
        "If tests fail, do not publish the notes.",
        "When the build is red never deploy or publish.",
    ],
)
def test_a_negation_governs_its_own_sub_clause_and_list(tmp_path: Path, goal: str) -> None:
    completed = lint_text(tmp_path, brief(goal=goal))

    assert completed.returncode == 0, completed.stdout
    assert completed.stdout.splitlines() == ["clean"]


def test_section_names_are_matched_without_regard_to_case(tmp_path: Path) -> None:
    text = brief(goal="Publish the notes.").replace("## Goal", "## goal").replace("## Context", "## CONTEXT")

    completed = lint_text(tmp_path, text)

    assert completed.stdout.splitlines() == [
        '[declared] L3: "Publish" asks for publish, which Side effects does not declare',
        "1 finding",
    ]


def test_a_fenced_example_neither_negates_later_prose_nor_asks(tmp_path: Path) -> None:
    leak = lint_text(tmp_path, brief(goal="```text\nNever:\n```\nPublish the notes."))
    tilde = lint_text(tmp_path, brief(goal="~~~text\npublish the notes\n~~~"))
    longer = lint_text(tmp_path, brief(goal="````markdown\n```\npublish the notes\n```\n````"))

    assert leak.stdout.splitlines() == [
        '[declared] L6: "Publish" asks for publish, which Side effects does not declare',
        "1 finding",
    ]
    assert tilde.stdout.splitlines() == ["clean"]
    assert longer.stdout.splitlines() == ["clean"]


def test_a_fenced_declaration_example_is_not_the_declaration(tmp_path: Path) -> None:
    text = brief(effects="none").replace(
        "## Context\n", "## Context\n~~~text\n- Side effects: commit · publish\n~~~\n"
    )

    completed = lint_text(tmp_path, text)

    assert completed.stdout.splitlines() == ["clean"]


def test_negated_heading_governs_its_block(tmp_path: Path) -> None:
    fence = "Not part of this work:\n- publishing the results\n- the deploy to staging\n\n- Merge after review."

    completed = lint_text(tmp_path, brief(fence=fence))

    assert completed.stdout.splitlines() == [
        '[declared] L13: "Merge" asks for merge, which Side effects does not declare',
        "1 finding",
    ]


def test_descriptive_merge_wording_is_not_an_ask(tmp_path: Path) -> None:
    completed = lint_text(tmp_path, brief(goal="Note which strategy causes fewer merge conflicts."))

    assert completed.returncode == 0, completed.stdout


def test_code_and_context_are_not_read_for_asks(tmp_path: Path) -> None:
    text = brief(goal="```text\ngit commit and deploy\n```").replace(
        "- Repo: example-org/example-repo", "- Repo: example-org/example-repo · main at the commit you start from"
    )

    completed = lint_text(tmp_path, text)

    assert completed.returncode == 0, completed.stdout


def test_over_declaration_is_an_advisory(tmp_path: Path) -> None:
    completed = lint_text(tmp_path, brief(effects="deploy"))

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        '[declared] L16: "deploy" is declared but the body never asks for it (advisory)',
        "clean, 1 advisory",
    ]


@pytest.mark.parametrize(
    ("effects", "message"),
    [
        ("commit · release", '[declared] L16: unknown side effect "release"; use commit, pull request,'
         " issue, comment, merge, deploy, publish or none"),
        ("none · commit", '[declared] L16: "none" beside other side effects'),
        ("", '[declared] L16: empty "Side effects:" line; write "none" when there are none'),
    ],
)
def test_malformed_declaration_is_a_finding(tmp_path: Path, effects: str, message: str) -> None:
    completed = lint_text(tmp_path, brief(effects=effects))

    assert completed.returncode == 1
    assert message in completed.stdout.splitlines()


def test_missing_sections_and_declaration_are_reported(tmp_path: Path) -> None:
    completed = lint_text(tmp_path, "## Goal\nCompare two retry strategies.\n")

    assert completed.returncode == 1
    assert completed.stdout.splitlines() == [
        "[sections]: missing: ## Instruction",
        "[sections]: missing: ## Fence",
        "[sections]: missing: ## Done",
        "[sections]: missing: ## Context",
        '[declared]: no "- Side effects:" line in ## Context; write "none" when there are none',
        "5 findings",
    ]


def test_step_list_is_a_procedure_advisory(tmp_path: Path) -> None:
    steps = "1. Read the notes.\n2. Compare the strategies.\n\n3. Write the summary."

    completed = lint_text(tmp_path, brief(goal=steps))

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "[procedure] L3: a 3-step list in ## Goal; state the outcome and let the receiver own the steps (advisory)",
        "clean, 1 advisory",
    ]


def test_procedure_heavy_brief_breaks_the_size_cap(tmp_path: Path) -> None:
    padding = "\n".join(f"- Background point {number}: the strategies differ again." for number in range(1, 80))

    completed = lint_text(tmp_path, brief(goal=padding))

    assert completed.returncode == 1
    assert any(
        line.startswith("[size]: ") and line.endswith("chars (cap 3,500)")
        for line in completed.stdout.splitlines()
    )


def test_unreadable_brief_is_an_input_error(tmp_path: Path) -> None:
    completed = run_lint(tmp_path / "absent.md")

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.startswith("error: cannot read ")


def test_skill_instructions_mirror_the_ask_table() -> None:
    spec = importlib.util.spec_from_file_location("brief_lint", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    instructions = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

    for ask in module.ASKS:
        assert f"| `{ask.effect}` | {ask.triggers} |" in instructions
