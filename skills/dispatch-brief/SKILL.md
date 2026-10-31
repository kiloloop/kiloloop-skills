---
name: dispatch-brief
description: Compose or review a brief before handing work to another agent. Use when delegating a task to a subagent or to a coding agent in another session, when delegated work keeps coming back over-scoped or wrong, or when reviewing someone else's brief for missing sections, procedure the receiver should own, or side effects the brief asks for but never declares.
---

# Dispatch Brief

A brief hands work to another agent: a subagent, a coding agent in another
session, or a teammate's agent. Two things go wrong in practice:

1. **Briefs that describe procedure instead of outcome.** The receiver
   re-derives every mechanical detail anyway, so steps typed by the requester
   are double cost and the most frequent source of wrong detail.
2. **Side effects the brief asks for but never states.** A brief that says
   "open a pull request" or "merge it yourself" in passing grants that
   authority without anyone deciding to. The receiver either does it
   unexamined or stops to ask.

The template keeps the requester to the goal, the fence, the done line and
the background only the requester holds, and ends with one explicit line of
side effects. The lint reads the brief against that line and names what is
missing. It reports; it never rewrites the brief.

## The brief

Copy [`templates/brief.md`](templates/brief.md). It has five sections:

| Section | Carries |
| --- | --- |
| `## Goal` | The outcome as the requester would see it, in one paragraph, with the requester's words quoted. |
| `## Instruction` | The fixed hand-over sentence, then background only the requester holds. |
| `## Fence` | What is out of scope, and what stays the human's hand where that departs from the standing rule. |
| `## Done` | The done line and the reply the requester expects. |
| `## Context` | The repository and starting commit, the model when it departs from the receiver's default, the estimate, and the `- Side effects:` line. |

Target 1,500–2,500 characters. Past 3,500 the brief is carrying procedure.

## What goes in, what stays out

**Always in:** the goal in the requester's words, the fence, what only the
requester holds (decisions, riders, cross-repository ripples, other work in
flight), the model when it departs from the default, the done line, the side
effects.

**Never in:** step lists, script names and flags, verification tails,
acceptance checklists derived from the tree, repository-state inventories,
facts the receiver reads from its own repository (versions, file lists,
changelog contents), review and landing mechanics, reply-quality rules.

## Sizing the estimate

State the estimate as the ceiling you would accept, not the hoped-for
duration: the expected time multiplied by the review rounds the work
plausibly runs. A review loop doubles it, and work without one takes ×1.5
headroom. Count files as distinct deliverable files: created or modified as
part of the outcome, including the test and documentation ripple and every
file in a fixture tree the work creates. Then add headroom. Never shrink a
number to make the work look smaller than it is.

## Declaring side effects

Read the done line verb by verb and list every outward action on the
`- Side effects:` line in Context, separated by `·` or commas. Write `none`
when there are none. The lint reads the Goal, Instruction, Fence and Done
sections for these asks. It skips fenced code (backtick or tilde fences) and
any ask a negation governs, and reports each ask whose side effect the line
does not declare:

| Side effect | Trigger wording |
| --- | --- |
| `commit` | commit, land or push a change |
| `pull request` | open, raise, create, update or submit a pull request; a pull-request link |
| `issue` | file, open, raise or create an issue |
| `comment` | comment on something, or post a comment |
| `merge` | merge, or land it yourself; yours to land |
| `deploy` | deploy, or ship to production |
| `publish` | publish |

The table reads wording, not intent. A trigger word used descriptively, such
as the commit a bug came from, still reads as an ask: reword it. Descriptive
merge wording (merge conflicts, a merge commit, the merge method) is not an
ask. A declared side effect the body never asks for is an advisory, because
it grants authority nothing in the brief uses.

Negation heads are `no`, `not`, `never`, `do not`, `does not`, `don't`,
`doesn't`, `out of scope`, `exclude`, `excluded`, `excludes`, `avoid`,
`refrain from`, `prohibited`, `forbidden`, `skip` and `without`. A negation
governs the rest of its sub-clause, up to a comma or a contrast word:
`but`, `however`, `instead`, `though`, `although`, `whereas`, `except`, or
`yet` or `then` after a comma or `and` (as adverbs they leave the negation
alone, as in `do not yet publish`). It also governs a whole list, still up to
a contrast word, in two cases: when a colon follows it, or when a comma
series closes with `, or`, `, and` or `, nor`.

A descriptive negation inside a condition (`if`, `when`, `whenever`,
`unless`, `once`, `until`, `whether`) governs nothing past the condition. A
command negation still governs the consequent it heads, even with no comma:
`do not`, `don't`, `never`, `avoid`, `refrain from` or `skip`, as in
`if tests fail do not publish`.

A negated Markdown heading, or a line ending in a colon, governs the block
below it up to the first blank line or code fence. `skip` and `without`
work only within their own clause. Examples:

- `- Out of scope: filing issues, commenting on the tracker` asks for
  neither.
- `Never deploy, publish, or merge.` asks for none of the three.
- `Do not merge, but publish the notes.` asks to publish, and so does
  `If no issues remain, publish the notes.`

## Running the lint

From this skill directory:

```bash
python scripts/brief_lint.py path/to/brief.md
```

Run it before every hand-off and after every edit. It is a detector, not a
formatter. Several briefs can be linted in one run.

Output is one finding per line, `[check] L<line>: <message>`, sorted by line.
Whole-file findings carry no line. A final line reads `clean` or
`<n> findings`, with any advisories counted. The checks:

- `sections`: a required heading is missing. Headings match without regard
  to case.
- `declared`: the body asks for a side effect the `Side effects:` line does
  not declare. Also a finding: the line is missing, empty, repeated, names an
  unknown side effect, or pairs `none` with others. A declared side effect
  the body never asks for is an advisory.
- `procedure`: a numbered list of three or more steps in a prose section.
  This is an advisory: state the outcome and let the receiver own the steps.
- `size`: more than 3,500 characters, or an advisory past 2,500.

Exit codes:

- `0`: clean. Advisories are allowed.
- `1`: one or more findings.
- `2`: a usage error or an unreadable file.

## Limits

The lint reads wording. It cannot judge whether the goal is the right goal,
whether the fence is complete, or whether the estimate is honest. An ask
phrased outside the trigger wording is not seen. It reads the brief file and
nothing else, and it does not know what the receiving agent or its runtime
will actually permit.
