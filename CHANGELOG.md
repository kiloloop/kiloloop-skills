# Changelog

Notable changes to this repository, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

A release is a snapshot of the whole collection. Skills are distributed
together but installed independently, so the exact tool versions each release
pins are also recorded in [Pinned tool versions](#pinned-tool-versions).

## [0.3.0] - 2026-08-27

### Added

- [`proof-before-done`](skills/proof-before-done/README.md) — a vendored receipt
  checker that executes declared completion predicates, emits paste-ready
  `PASS`/`FAIL`/`UNVERIFIED` evidence whose `EXEC`/`STATIC` labels identify the
  attempted verification method, and exits nonzero when any claim fails or
  lacks verification.
- [`verify-numbers`](skills/verify-numbers/README.md) — a vendored skill that
  distinguishes measured, calculated, estimated, and recalled quantities;
  ships an LF- and CRLF-aware full-line counter with inspectable match evidence
  and deterministic phantom-count and bare-CR fixtures; and documents
  copy-paste request and reply shapes plus explicit visibility limits.

## [0.2.0] - 2026-08-18

### Added

- Human-facing [`memory-lint` README](skills/memory-lint/README.md) with output
  captured from the shipped synthetic defect corpus, the exact wrapper and
  exit-status contract, configuration choices, and explicit limits. The root
  and skill indexes now link directly to that guide.
- `usage-cost` — a vendored skill that reads a coding agent's local session
  records and reports both the tokens used and what that usage would have cost
  at published API list rates. It deduplicates the repeated records the
  transcript format writes for a single request, prices uncached input, output,
  cache reads, and each cache-write lifetime separately from a versioned rate
  table, and reports server-side tool requests as a count rather than folding
  them into a token cost. A model id with no published rate has its tokens
  counted and its cost withheld, and the run exits `3` with the total marked
  incomplete rather than pricing it at a guessed rate; a runtime with no
  readable local records exits `4` with a stated reason instead of an estimate.
  Claude Code is the supported runtime in this release.

  Repeated transcript records are reconciled to the largest snapshot per
  request rather than deduplicated by first-seen, because the copies a
  streaming response leaves behind are not identical and the earliest one can
  hold a small fraction of the final output tokens. Days bucket in a real IANA
  local zone so each timestamp gets the offset that applied at that moment.
  Records carrying negative counters or no request identity are excluded and
  counted rather than silently reducing or collapsing the total, a directory
  holding no readable transcripts reports unavailable instead of a confident
  zero, and impossible calendar dates are rejected as usage errors.

  The skill's instructions state the pricing scope explicitly — every request
  is priced at the synchronous list rate, with batch processing's 50% discount
  deliberately not modeled because coding-agent requests are interactive — and
  include a short reading guide relating a report's cache-read, cache-write,
  and output shares to the published rate ratios.

## [0.1.0] - 2026-08-15

First public release.

### Added

- `memory-lint` — a version-pinned wrapper skill that runs the published
  `memory-lint` CLI as a read-only diagnostic over a Markdown memory or
  documentation corpus. The adapter never installs dependencies while running.
  When the pinned distribution is absent, version-mismatched, or present but
  unimportable it exits `2` and prints the exact pin alongside a runnable
  install command. Otherwise it preserves the CLI's own output and exit status,
  so `1` continues to mean "lint findings" rather than a failure to run, and
  abnormal termination of the delegated CLI is normalized to `2` rather than
  leaking a raw signal status.
- Repository contract in [CONTRIBUTING.md](CONTRIBUTING.md) — the two validated
  skill forms, vendored implementation and version-pinned wrapper, with
  exact-pin syntax, failure-message obligations, and clean-environment
  requirements.
- Fixture harness (`scripts/run_skill_fixtures.py`) — validates every skill
  unit. For wrapper-form skills it proves the absent-dependency error, resolves
  each exact pin from the public package index in a fresh virtual environment,
  asserts the installed version matches the pin, and runs the declared smoke
  command plus the skill's own fixtures.
- Continuous integration running the same gates on every pull request.

## Pinned tool versions

Wrapper-form skills delegate to an exact published version. This table records
which release carried which pin, so a pin change stays traceable without
reading every entry above.

| Release | Skill | Pinned distribution |
| --- | --- | --- |
| 0.3.0 | `memory-lint` | `memory-lint==0.1.0` |
| 0.2.0 | `memory-lint` | `memory-lint==0.1.0` |
| 0.1.0 | `memory-lint` | `memory-lint==0.1.0` |

[0.3.0]: https://github.com/kiloloop/kiloloop-skills/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/kiloloop/kiloloop-skills/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/kiloloop/kiloloop-skills/releases/tag/v0.1.0
