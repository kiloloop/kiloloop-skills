# Dispatch Brief

A brief template and a lint for handing work to another agent: a subagent, a
coding agent in another session, or a teammate's agent. The brief has five
short sections and ends with one explicit line of side effects.

## What it looks like

The skill ships three fixture briefs. `clean.md` asks for a commit, a pull
request and a merge, and declares all three. `under_declared.md` is the same
body declaring only the commit. `publish_undeclared.md` is a written
comparison that declares no side effects, but its instruction also asks to
publish. Linting all three:

```console
$ python scripts/brief_lint.py fixtures/briefs/clean.md fixtures/briefs/under_declared.md fixtures/briefs/publish_undeclared.md
== fixtures/briefs/clean.md
clean
== fixtures/briefs/under_declared.md
[declared] L10: "merge" asks for merge, which Side effects does not declare
[declared] L13: "pull-request link" asks for pull request, which Side effects does not declare
2 findings
== fixtures/briefs/publish_undeclared.md
[declared] L6: "publish" asks for publish, which Side effects does not declare
1 finding
```

The run exits `1`. Each finding quotes the wording that asks and names the
side effect the brief never declared.

## Why

Two things go wrong when a requester writes a brief. Procedure typed by the
requester is double cost, because the receiver re-derives every mechanical
detail anyway, and it is the most frequent source of wrong detail. A side
effect asked for in passing ("the pull request is yours to merge") grants
authority without anyone deciding to grant it. Both are checkable before the
hand-off.

## Ask an agent

Paste this after installing the skill and replace the bracketed values:

```text
Use the dispatch-brief skill to write the brief for [THE WORK] before
handing it to [THE RECEIVING AGENT]. Start from the template, keep it to the
goal, the fence, the done line and the background only I hold, declare every
side effect on the Side effects line, run the lint, and fix every finding
before the hand-off.
```

## Install

Copy the complete `dispatch-brief` directory into the skills directory used by
your coding-agent runtime. Keep `SKILL.md`, `agents/`, `templates/`, `scripts/`
and `fixtures/` together. The lint uses only the Python standard library and
installs nothing at runtime.

### Codex

From the root of a checkout containing this skill, install it for your user:

```bash
mkdir -p "$HOME/.agents/skills"
cp -R skills/dispatch-brief "$HOME/.agents/skills/"
```

For a repository-scoped installation, copy the same directory into that
repository's `.agents/skills/` instead. The included `agents/openai.yaml`
provides the **Dispatch Brief** display name, a short description and a
starter prompt. Automatic skill selection remains enabled.

In Codex CLI or the IDE extension, invoke it explicitly with:

```text
Use $dispatch-brief to write and lint a brief for the work I want to delegate.
```

Include the work and its scope in the same request. Codex detects new skills
automatically; restart it if the skill does not appear. See the
[official OpenAI skill documentation](https://learn.chatgpt.com/docs/build-skills)
for discovery locations and invocation details.

## Use it

1. Copy `templates/brief.md` and write the five sections.
2. List every outward action on the `- Side effects:` line in Context, or
   write `none`.
3. From the installed skill directory, run:

```bash
python scripts/brief_lint.py brief.md
```

4. Fix every finding: declare the side effect, or reword the ask if the work
   does not do it.

Exit `0` means no findings; advisories may remain. Exit `1` means at least
one finding. Exit `2` means a usage error or an unreadable file.

## Falsifiable claim

> A brief that passes this lint declares every side effect its own body asks
> for, in the wording the lint's ask table covers.

The fixture test asserts the complete output of all three briefs. The clean
brief exits `0`. The under-declared twin exits `1` and names `merge` and
`pull request` at the lines that ask for them. The publish brief exits `1`
with one finding naming `publish` at its line. Further cases cover:

- each side effect against its trigger wording
- negated asks and negated headings
- negation scope across commas, contrast words, conditions and lists
- descriptive merge wording
- backtick and tilde fences, including a fence that must not negate the
  prose after it
- heading case
- malformed declarations

Making the lint accept an undeclared pull request, read an `Out of scope:`
bullet as an ask, let a negation in one clause hide an ask in the next, or
skip a missing `Side effects:` line turns the fixtures red. A last
test keeps the ask table in `SKILL.md` identical to the script's.

## Choices worth knowing about

**One plain line, not a schema.** Side effects are declared as a short list
in Context, so the brief reads the same to a person and to any agent runtime.
The lint needs no configuration and no knowledge of the receiver.

**Negation is read, not guessed.** An ask in a clause headed by `not`,
`never`, `out of scope` and similar, or in the block under a negated heading,
is not an ask. A fence bullet that lists what the work does not do therefore
never reads as a declaration gap. A negation reaches only its own sub-clause
or list. `Do not merge, but publish the notes` still asks to publish, and a
negation inside a condition (`if no issues remain, publish`) negates nothing
after it.

**Over-declaring is an advisory.** A declared side effect the body never asks
for does not fail the lint, but it is reported: it grants authority nothing
in the brief uses.

**Procedure is flagged, not forbidden.** A numbered list of three or more
steps in a prose section is an advisory, and so is a brief past 2,500
characters. Past 3,500 characters the size is a finding.

## What it cannot see

- An ask phrased outside the trigger wording in the ask table.
- Whether the goal is the right goal, the fence is complete, or the estimate
  is honest.
- What the receiving agent or its runtime will actually permit.

## Verify the skill

From the repository root:

```bash
python -m pytest -q skills/dispatch-brief/fixtures/test_declared_side_effects.py
python scripts/run_skill_fixtures.py
```
