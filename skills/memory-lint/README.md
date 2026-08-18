# Memory Lint

Run a deterministic, read-only structural check over a Markdown memory or
documentation corpus from a coding-agent workflow.

## What it is

The `memory-lint` skill gives an agent a version-pinned adapter, a repeatable
workflow, and a reporting contract for the published `memory-lint` CLI. It is
useful when a repository treats Markdown as operational state: agent memory,
runbooks, indexes, generated sections, or documentation that multiple agents
edit and consume.

This is deliberately a linter, not a proof that the prose is true or useful.
It catches configured structural drift—broken links, incomplete indexes,
frontmatter violations, malformed managed markers, staleness, duplicate
anchors, line limits, and suspicious revision changes—without editing the
corpus.

## What it looks like

The output below is real. It was captured through this skill's exact wrapper
after `materialize_fixture_snapshot` in `fixtures/test_cli.py` expanded the
shipped synthetic defect corpus from `fixtures/corpus.yaml`, using the fixed
date `2026-08-10`:

```console
$ python scripts/memory_lint_wrapper.py \
    --config fixture-snapshot/config.yaml \
    --corpus-root fixture-snapshot/fixtures/defects \
    --now 2026-08-10
SEVERITY  CODE                        LOCATION                         PROFILE          MESSAGE
--------  --------------------------  -------------------------------  ---------------  -------------------------------------------------------------------------
ERROR     broken-link                 MEMORY.md:14                     indexes          link target 'notes/does-not-exist.md' does not resolve to a file
ERROR     index-missing-target        MEMORY.md:14                     index:MEMORY.md  index target 'notes/does-not-exist.md' does not resolve within the corpus
ERROR     frontmatter-type-enum       notes/bad-type.md:4              notes            type must be one of: memory, note
ERROR     broken-link                 notes/broken-links.md:11         notes            link target 'missing-relative.md' does not resolve to a file
ERROR     broken-link                 notes/broken-links.md:11         notes            link target 'missing-wiki-target' does not resolve to a file
ERROR     duplicate-anchor            notes/duplicate-anchor.md:15     notes            explicit anchor 'reused-anchor' duplicates line 11
ERROR     duplicate-heading           notes/duplicate-heading.md:15    notes            heading anchor 'repeated-section' duplicates line 11
WARNING   line-too-long               notes/long-line.md:11            notes            line has 162 characters; maximum is 120
ERROR     marker-nested               notes/marker-nested.md:13        notes            managed marker block begins inside another managed block
ERROR     marker-orphaned             notes/marker-orphaned.md:11      notes            managed end marker has no matching begin marker
ERROR     marker-unclosed             notes/marker-unclosed.md:11      notes            managed begin marker has no matching end marker
ERROR     frontmatter-required-key    notes/missing-key.md:2           notes            required key is missing: type
WARNING   stale-updated               notes/stale.md:7                 notes            Updated header is 952 days old; maximum is 30
ERROR     index-missing-entry         notes/unindexed.md               index:MEMORY.md  file is missing from MEMORY.md
ERROR     frontmatter-unquoted-value  notes/unquoted-description.md:3  notes            description must use a quoted scalar value

15 finding(s).
```

The command exits `1`: the lint ran successfully and found issues. Exit `1`
is evidence to inspect, not an adapter failure.

## Run it

Install the exact dependency before invoking the skill. The adapter never
installs or upgrades packages at runtime.

```bash
python -m pip install "memory-lint==0.1.0"
```

From `skills/memory-lint/`, point the wrapper at a configuration file:

```bash
python scripts/memory_lint_wrapper.py \
  --config path/to/memory-lint.yaml \
  --format json \
  --now "$(date -u +%F)"
```

Use table output for a person and JSON for an agent or CI step. Report the
executed command, configuration and corpus paths, exit status, finding count,
and findings grouped by severity and code. The full agent procedure is in
[`SKILL.md`](SKILL.md).

## Choices worth knowing about

- **Exact package pin:** the wrapper accepts `memory-lint==0.1.0` only. A
  missing, mismatched, or unimportable installation exits `2` with the exact
  install command.
- **Read-only boundary:** lint output is diagnostic. The skill tells the agent
  to propose changes and wait for approval instead of auto-fixing files.
- **Deterministic time:** pass `--now YYYY-MM-DD` when staleness results must be
  reproducible. Without it, the CLI uses the current UTC date.
- **Profile-scoped policy:** configuration profiles decide which files and
  checks apply. `--corpus-root` can reuse that policy against another snapshot.
- **Exit status is data:** `0` is clean, `1` is findings, and `2` is a usage,
  dependency, configuration, filesystem, Git, or delegated-process error.
- **Revision modes:** `--against <git-ref>` compares linted changes with a
  local ref; paired `--compare-before` and `--compare-after` flags inspect one
  explicit revision pair. Both paths remain read-only.

## What it cannot see

- It does not judge factual accuracy, writing quality, completeness of ideas,
  or whether a remembered decision is still strategically correct.
- It sees only files selected by configured profiles and indexes. An omitted
  directory is outside the check.
- It does not validate arbitrary external URLs or non-Markdown assets.
- Staleness is based on configured `*Updated:* YYYY-MM-DD` headers, not Git
  history or the meaning of the prose.
- It does not repair findings. A person or agent must decide which edits are
  appropriate and validate them separately.

## Verify the skill

From the repository root:

```bash
python scripts/run_skill_fixtures.py
```

The fixtures are synthetic. They cover a clean corpus, all core defect codes,
revision comparisons, dependency failures, and abnormal delegated-process
termination without using real memory or documentation content.
