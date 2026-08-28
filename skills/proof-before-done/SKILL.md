---
name: proof-before-done
description: Execute completion claims and produce paste-ready evidence before any done, verified, or tested report. Use when an agent is about to claim that tests pass, a file or string exists, a command succeeds, or a Git ref contains a reviewed commit.
---

# Proof Before Done

Turn each completion claim into a live check immediately before reporting it.
Static inspection is not evidence that a command ran, and an earlier successful
run is stale after a relevant file or head changes.

## Build the claims file

Create a JSON object with a non-empty `claims` array. Give every claim a
predicate and its exact inputs:

| `type` | Required fields | What is checked |
| --- | --- | --- |
| `tests_pass` | `command` string array | Runs the test command; exit `0` passes. |
| `command_exit_zero` | `command` string array | Runs the command; exit `0` passes. |
| `file_exists` | `path` | Stats the live path; a regular file passes, an absent path fails, and a path the checker cannot inspect is `UNVERIFIED`. |
| `string_present` | `path`, `string` | Reads the UTF-8 file and looks for the exact string. |
| `git_ref_contains` | `commit`, `ref`; optional `repo` | Runs `git merge-base --is-ancestor`; exit `0` passes. |

Paths and command working directories are relative to the claims file. Commands
are argument arrays, not shell strings, and run without a shell. Inspect every
command before executing a claims file you did not write.

```json
{
  "claims": [
    {
      "claim": "tests pass",
      "type": "tests_pass",
      "command": ["python", "-m", "pytest", "-q"]
    },
    {
      "claim": "reviewed head is reachable from main",
      "type": "git_ref_contains",
      "repo": ".",
      "commit": "<full-reviewed-commit>",
      "ref": "main"
    }
  ]
}
```

Do not invent a predicate just to make a claim pass. Omit the predicate when no
decisive check exists; the checker will make that gap `UNVERIFIED`.

## Execute and decide

From this skill directory, run:

```bash
python scripts/proof_before_done.py path/to/claims.json
```

Each receipt records the claim, the exact executed command or static predicate,
its exit code when applicable, the observed value, an execution-time UTC
timestamp, and `PASS`, `FAIL`, or `UNVERIFIED`.

`FAIL` means a predicate executed and observed a false result, a nonzero exit,
or a timeout. `UNVERIFIED` means the predicate was missing or unsupported, its
required inputs were malformed, the command could not start, or the static
input could not be inspected or read. In both cases the checker exits nonzero.

- Exit `0`: every claim passed.
- Exit `1`: at least one claim failed or was not verified. Do not report done.
- Exit `2`: the claims document or CLI input is invalid. Fix it and rerun.

Rerun after any change that could invalidate a receipt, including edits,
commits, rebases, check reruns, or moved refs. A receipt proves only the live
state observed by that run.

## Report to the person

Paste the complete receipt lines into the final reply; terminal output alone is
not the deliverable. Lead with the truthful outcome, then name skipped checks or
limits. Preserve `FAIL` and `UNVERIFIED` exactly—never translate either into
"done," "verified," or "tests pass."

`EXEC` marks an attempted subprocess predicate and preserves its argv.
`STATIC` marks an attempted filesystem inspection. Either method can be
`UNVERIFIED` when the process could not start or the state could not be read;
the status and observed field carry that outcome. Keep the method distinction
visible in the reply.

Inspect receipts for local paths before a public reply. `git_ref_contains`
preserves the resolved repository path so its argv stays exact. Prefer running
from a disclosure-safe checkout; if a path must be redacted, label the receipt
as redacted rather than claiming it was pasted verbatim.

## Limits

The checker executes only the predicates declared in one JSON file. It does not
discover the right project checks, prove that the claims list is complete,
isolate commands, preserve receipts after state changes, or establish that a
passing commit is the exact reviewed head unless the claims express that
relationship.
