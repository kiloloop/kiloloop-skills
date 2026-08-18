# Kiloloop Skills

Agent-discipline skills for coding-agent runtimes (Claude Code, Codex):
self-contained, versioned, fixture-tested.

Each skill is an independent unit under `skills/<name>/`. It carries its own
instructions, deterministic scripts, and falsifiable fixtures. Self-contained
means independently installable: a skill either vendors its implementation or
wraps an exact version of a published package declared through `requires:`.
Neither form may depend on an unpublished repository or a floating dependency.

## Development model

Skill behavior, packaging, and runtime-adapter changes originate here. When a
skill wraps a separately maintained tool, broadly applicable tool fixes belong
in that tool's owning repository before its pin or fixtures are refreshed. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the repository contract.

## Validation

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python scripts/run_skill_fixtures.py
```

The fixture runner intentionally passes when no skills exist. Once a
`skills/<name>/` directory is added, the same gate requires its `SKILL.md`, a
script, and at least one executable fixture test. Wrapper-form skills are also
installed in a clean environment: the gate proves the absent-dependency error,
resolves every exact pin from the public package index, asserts installed
versions, and runs the declared wrapper smoke command plus its fixtures.

## Available skills

- [`memory-lint`](skills/memory-lint/README.md) — lint Markdown memory and
  documentation corpora through the exact `memory-lint==0.1.0` release, with
  real fixture output and the skill's operating boundaries.
- [`usage-cost`](skills/usage-cost/) — report a coding agent's locally recorded
  token usage and what that usage would cost at published API list rates.
