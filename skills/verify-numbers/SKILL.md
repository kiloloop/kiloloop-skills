---
name: verify-numbers
description: Verify counts, deltas, estimates, remembered values, and time-window metrics before quoting them. Use when a number will anchor a recommendation, comparison, status report, or protocol artifact and its scope or method must be auditable.
---

# Verify Numbers

A number that affects a decision needs evidence proportionate to the claim. Do
not let a plausible value, a remembered identifier, or a search result acquire
the authority of a measurement merely because it is numeric.

## Classify the claim

Before calculating, decide which label applies:

- **Measured:** observed from named inputs with an executed method.
- **Calculated:** derived from measured inputs with the arithmetic shown.
- **Rough estimate:** based on stated assumptions rather than complete inputs.
- **Recalled:** remembered or copied from prior context; re-verify before use.

Do not relabel an estimate as measured after rounding it or presenting it in a
table.

## Define the quantity

State the unit and scope before running a counter:

- What entity is counted: matching lines, regex matches, files, records,
  requests, bytes, or another unit?
- Which inputs are included and excluded?
- Which revision or snapshot is being measured?
- For a time series, what are the start and end instants, timezone, bucket
  semantics, and known coverage gaps?

If those choices are unresolved, the count is not ready to quote.

## Measure and verify

1. Execute the measurement against the named inputs. Preserve the command or
   calculation and the raw endpoints needed to reproduce it.
2. For a regex count, choose the intended unit explicitly. `grep -c` and
   `rg -c` count matching lines, not individual matches. Do not substitute one
   for the other silently.
3. When counting line-shaped records, require a full-line match. From this
   skill directory, run:

   ```bash
   python scripts/anchored_count.py \
     --pattern '## Verified' \
     path/to/file.md
   ```

   The helper applies the regex to each complete line and prints every matched
   path and line number. Use `--format json` when another tool will consume the
   result.
4. Inspect the matched evidence. If the result is surprising or materially
   affects a decision, spot-check the source and use a second method with the
   same unit and scope.
5. Measure both endpoints of a delta independently with the same method. Report
   `after - before`; never infer an endpoint from the edit or carry it from
   memory.
6. Re-run recalled values and identifiers before putting them in a command,
   protocol message, or final report. After context compaction, reload the
   governing artifact rather than trusting the summary to preserve exact
   numbers.

## Report the evidence

Reproduce the result in the reply. Do not make the reader recover the number
from terminal output or open an evidence file just to learn the conclusion.
Put the headline number first, then keep its basis beside it:

```markdown
**Measured:** 12 matching lines.

- **Scope:** `docs/a.md` and `docs/b.md` at revision `<sha>`
- **Method:** `python scripts/anchored_count.py --pattern '...' <inputs>`
- **Evidence:** matched paths and line numbers from the command output
- **Caveat:** the named files were supplied explicitly; completeness was not checked

**Calculated:** 7 after - 5 before = +2, with both endpoints measured above.

**Rough estimate:** about 18 files = 3 directories x roughly 6 files each;
the directories have not been enumerated yet.
```

For an estimate that anchors a decision, include the assumptions and a range or
other uncertainty statement when the inputs support one. For a time-window
claim, state the window and timezone beside the value and hedge if the data does
not cover the full event period.

Do not round measured counts, endpoints, or identifiers. Round only when the
result is explicitly approximate, and make the rounding visible. Surface only
caveats that apply to the run. Do not call a result high, low, good, or bad
unless the user requested that comparison and the benchmark is also defined
and verified.

## Limits

The helper proves only full-line regex counting over explicitly named UTF-8
files, with an optional leading byte-order mark. It treats LF as the physical
line separator and normalizes CRLF; other Unicode separators remain content.
It does not decide whether the files are the complete population, whether a
record spans multiple lines, whether duplicated records should be deduplicated,
or whether the resulting number supports the conclusion. Resolve those
questions in the claim definition and report them as limitations.
