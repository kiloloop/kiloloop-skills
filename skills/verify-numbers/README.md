# Verify Numbers

Verify a quantitative claim before it becomes the premise of a recommendation,
comparison, status report, or protocol action.

## What it looks like

This output is produced from the shipped seeded corpus:

```console
$ python scripts/anchored_count.py \
    --pattern '## Verified' \
    fixtures/data/claims.md
fixtures/data/claims.md:1:## Verified
fixtures/data/claims.md:4:## Verified
Measured matching lines: 2
```

This is a count of matching physical lines in one explicitly named file. It is
not a count of regex occurrences, Markdown headings in general, or every
relevant file in a larger corpus.

For machine-readable evidence:

```bash
python scripts/anchored_count.py \
  --pattern '## Verified' \
  --format json \
  fixtures/data/claims.md
```

The JSON includes the pattern, case-sensitivity choice, count unit, physical
line-separator rule, input paths, total, and every matched path, line number,
and line text.

## Ask an agent

Paste this after installing the skill and replace the bracketed values:

```text
Use the verify-numbers skill to check [QUANTITATIVE CLAIM] against [INPUTS] at
[REVISION OR TIME WINDOW]. Define the unit and scope before measuring, execute
the method, inspect the evidence, and return the headline number, scope,
snapshot, method, evidence, and applicable caveats. Label estimates explicitly
and do not substitute one for a missing measurement.
```

## What it is

`verify-numbers` gives an agent a compact procedure for separating measured,
calculated, estimated, and recalled values. It requires the unit, input scope,
revision or time window, method, and relevant uncertainty to travel with the
number instead of being reconstructed after the fact.

The bundled helper handles one narrow but common failure class: phantom counts
from a regex that matches a target inside prose or as a prefix of a different
record. It counts only lines whose complete text matches the supplied pattern
and emits every matching path and line number for inspection.

## Falsifiable claim

> Given an explicit set of UTF-8 files, optionally beginning with a byte-order
> mark, `anchored_count.py` counts a line only when the regex matches the entire
> LF- or CRLF-delimited line. Mid-line and prefix-only occurrences cannot
> inflate the reported count, and every counted line appears in the evidence
> output.

The seeded fixture contains two exact `## Verified` lines, one prose mention,
and one longer `## Verified details` heading. A substring search reports four
matching lines; the shipped full-line method reports two. Replacing the
helper's full-line match with a substring search makes the central fixture
fail.

## A practical verification pass

1. **Define the quantity.** Name the unit and included inputs. A count of
   matching lines is not a count of regex occurrences, files, or logical
   records.
2. **Pin the snapshot.** Record a revision, immutable artifact, or explicit
   time window and timezone.
3. **Execute the method.** Preserve the command and raw endpoints. For a delta,
   measure before and after separately.
4. **Inspect the evidence.** Read the matches or spot-check the source. For a
   surprising or decision-critical result, use a second method with the same
   unit and scope.
5. **Label the result.** Say measured, calculated, or rough estimate. Re-verify
   recalled numbers and identifiers before use.
6. **State uncertainty and gaps.** Estimates need their calculation and
   assumptions. Time-series claims need their window, timezone, and coverage
   limits.

## Choices worth knowing about

**Whole-line matching is deliberate.** Python's
[`fullmatch`](https://docs.python.org/3/library/re.html#re.fullmatch) requires
the complete string to match. The helper applies it one line at a time rather
than relying on hand-written `^` and `$`, whose behavior changes around final
newlines and multiline mode.

**Count units differ across tools.** GNU
[`grep -c`](https://www.gnu.org/software/grep/manual/grep.html#General-Output-Control)
and ripgrep
[`rg -c`](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md#common-options)
report matching lines. A line may contain multiple matches, so a command that
counts matches can return a different value without either tool being wrong.
Name the unit beside the result.

**A method is part of the result.** NIST's guidance for reporting measurement
uncertainty emphasizes describing how a result and its uncertainty were
evaluated. This skill applies the same practical boundary to ordinary software
claims: report the method and assumptions needed to interpret the number. See
[`NIST TN 1297, section 7`](https://www.nist.gov/pml/nist-technical-note-1297/nist-tn-1297-7-reporting-uncertainty).

## Tradeoffs

- The helper accepts explicit files, not directories or globs. This makes the
  measured population visible, but the caller must enumerate it first.
- Inputs are decoded as UTF-8 with an optional leading byte-order mark, and
  duplicate references to the same resolved file are rejected so aliases
  cannot double-count it.
- LF delimits physical lines and CRLF is normalized. Form feeds, vertical tabs,
  and Unicode line/paragraph separators stay within the physical line, matching
  the line semantics of the recommended `grep` and `rg` cross-checks.

## What it cannot see

- Evidence shows what matched, not whether the selected files are the complete
  population or whether generated and ignored files should be included.
- Matching is line-oriented. Multiline records, semantic Markdown structure,
  generated data, and deduplication rules need a domain-specific method.
- A correct count does not establish that the number supports a conclusion or
  that the chosen comparison benchmark is appropriate.
- The workflow reduces unsupported precision; it cannot eliminate stale,
  biased, or incomplete source data.

## Verify the skill

From the repository root:

```bash
python -m pytest -q skills/verify-numbers/fixtures/test_anchored_count.py
python scripts/run_skill_fixtures.py
```

The fixture checks the shipped CLI output and proves that the seeded corpus
contains the intended negative case: an unanchored substring method returns
four while the full-line method returns two.
