# Proof Before Done

Turn a completion report into executed, paste-ready evidence before calling the
work done.

## What it looks like

The shipped fixture deliberately includes one failing test claim and one claim
with no predicate. Running it produces one receipt per claim and exits `1`:

```console
$ python scripts/proof_before_done.py fixtures/data/claims.json
PASS · claim="command exits 0" · command=EXEC ["python3","-c","print('ready')"] · exit_code=0 · observed="stdout=ready\n; stderr=<empty>" · timestamp=2026-08-27T04:26:57Z
FAIL · claim="tests pass" · command=EXEC ["python3","-c","import sys; print('intentional failure', file=sys.stderr); raise SystemExit(1)"] · exit_code=1 · observed="stdout=<empty>; stderr=intentional failure\n" · timestamp=2026-08-27T04:26:57Z
PASS · claim="fixture file exists" · command=STATIC file_exists(path="present.txt") · exit_code=N/A · observed="regular_file=true" · timestamp=2026-08-27T04:26:57Z
PASS · claim="fixture contains the verified marker" · command=STATIC string_present(path="present.txt",string="verification-ready") · exit_code=N/A · observed="string_present=true" · timestamp=2026-08-27T04:26:57Z
UNVERIFIED · claim="reviewed head is current" · command=NOT_EXECUTED · exit_code=N/A · observed="no predicate supplied" · timestamp=2026-08-27T04:26:57Z
```

The failed test keeps its actual exit code and stderr. The claim without a
predicate is `UNVERIFIED`, never silently promoted to a pass.

## Ask an agent

Paste this after installing the skill and replace the bracketed values:

```text
Use the proof-before-done skill before reporting this task complete. Build a
claims JSON for [THE COMPLETION CLAIMS], attach the cheapest decisive predicate
to each claim, inspect every command, execute the checker against live state,
and paste every receipt line into your reply. Do not report done while any
receipt is FAIL or UNVERIFIED, and rerun after any relevant file or Git head
changes.
```

## What it is

`proof-before-done` is a standard-library Python receipt checker plus agent
instructions for using it at the final-report boundary. A claims file names
what the agent intends to say and the predicate that would make each statement
true. The checker runs those predicates now, records what happened, and returns
nonzero if any claim failed or never ran.

It supports five initial claim shapes: tests pass, another command exits zero,
a regular file exists, an exact string occurs in a UTF-8 file, and a Git ref
contains a commit.

## Install

Copy the complete `proof-before-done` directory into the skills directory used
by your coding-agent runtime. Keep `SKILL.md`, `scripts/`, and `fixtures/`
together. The checker uses only the Python standard library and installs
nothing at runtime.

## Use it

Create a JSON file with a non-empty `claims` array:

```json
{
  "claims": [
    {
      "claim": "tests pass",
      "type": "tests_pass",
      "command": ["python", "-m", "pytest", "-q"]
    },
    {
      "claim": "artifact exists",
      "type": "file_exists",
      "path": "dist/artifact.json"
    },
    {
      "claim": "reviewed commit is in main",
      "type": "git_ref_contains",
      "commit": "<full-commit>",
      "ref": "main",
      "repo": "."
    }
  ]
}
```

Paths and command working directories resolve from the claims file's directory.
Then run, from the installed skill directory:

```bash
python scripts/proof_before_done.py path/to/claims.json
```

Exit `0` means every receipt passed. Exit `1` means at least one claim is
`FAIL` or `UNVERIFIED`. Exit `2` means the claims document or CLI input is
invalid. Paste the receipt lines into the done-report only after an exit `0`.

## Falsifiable claim

> A done-report produced through this checker cannot contain an unexecuted
> verification claim: every listed claim receives a receipt; a missing,
> unsupported, malformed, unstartable, or unreadable check produces
> `UNVERIFIED`; and every `FAIL` or `UNVERIFIED` produces a nonzero checker exit.

The seeded fixture contains a test command that exits `1` and a claim with no
predicate. The fixture asserts the executed command and exit code remain in the
failed receipt, true static checks remain `PASS`, the missing predicate remains
`UNVERIFIED`, and the overall run exits `1`.

## Choices worth knowing about

**Commands are arrays and never use a shell.** The checker passes the declared
argument sequence to Python's
[`subprocess.run`](https://docs.python.org/3/library/subprocess.html) with
`shell=False`, so the receipt can show the executed argument boundary instead
of reconstructing a shell string.

**Static evidence is labeled.** File existence and string presence are useful
checks, but they do not prove a test or build ran. Their receipts say `STATIC`;
subprocess and Git checks say `EXEC`.

**Git containment is ancestry, not equality.** The Git predicate uses
[`git merge-base --is-ancestor`](https://git-scm.com/docs/git-merge-base),
which returns `0` when the declared commit is an ancestor of the named ref and
`1` when it is not. Use a separate exact-head command when equality is the
claim.

**Git receipts include the resolved repository path.** That keeps the recorded
argv identical to what ran, but it can disclose a local directory. Run from a
disclosure-safe checkout before a public report, or replace only that path with
`<repo>` and state that the receipt was redacted rather than pasted verbatim.

**Receipts expire when state moves.** Timestamps show when a predicate ran, not
how long its result remains valid. Rerun after relevant edits, commits, rebases,
or ref movement.

## Tradeoffs

- JSON keeps the vendored checker dependency-free, at the cost of comments and
  YAML's more relaxed authoring syntax.
- Commands inherit the caller's environment and run from the claims file's
  directory. This makes repo-native checks usable but does not sandbox them.
- Command stdout and stderr are escaped onto one receipt line. Large outputs
  stay complete and can make a pasted receipt noisy.
- The default per-command timeout is 60 seconds. Override it with
  `--timeout-seconds` when a legitimate project check needs longer.

## What it cannot see

- The checker cannot decide whether the claims file includes every statement
  the final report will make.
- A passing command proves its exit status and captured output at one instant;
  it does not prove the command was the project's right or complete check.
- `file_exists` and `string_present` inspect live files but do not execute the
  code those files describe.
- `git_ref_contains` proves reachability, not that the ref equals the reviewed
  commit or that the worktree is clean.
- The shipped seeded fixture requires `python3` on `PATH`. Repository CI and
  declared supported environments provide it; another environment must adapt
  the fixture command before using the example as reproducibility evidence.
- A receipt does not survive later state changes. The agent owns the final
  drift check and rerun.

## Verify the skill

From the repository root:

```bash
python -m pytest -q skills/proof-before-done/fixtures/test_receipts.py
python scripts/run_skill_fixtures.py
```

The fixture's negative path exits `1`, preserves the failing command's actual
exit code, and exposes the claim that has no predicate as `UNVERIFIED`.
