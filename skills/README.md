# Skill units

Each child directory is one self-contained skill and one installation unit.
Self-contained means independently installable through either a vendored
implementation or an exact public package pin.

Vendored form:

```text
skills/<name>/
  SKILL.md       # agent instructions
  scripts/       # implementation embedded with the skill
  fixtures/      # pytest claims that travel with the skill
```

Wrapper form adds this machine-validated frontmatter to `SKILL.md` while
keeping the same directory shape:

```yaml
requires:
  - example-cli==1.2.3
wrapper:
  entrypoint: scripts/example_wrapper.py
  smoke_args: ["--help"]
```

A consumer can copy or install one child directory without pulling code from
another skill or an unpublished package. The repository-level fixture runner
discovers both forms automatically. See [CONTRIBUTING.md](../CONTRIBUTING.md)
for wrapper pin, failure-message, install, and clean-environment obligations.

## Available skills

- [`memory-lint`](memory-lint/README.md) — lint Markdown memory and
  documentation corpora through the exact `memory-lint==0.1.0` release. See
  its README for real fixture output, tradeoffs, and limits.
- [`usage-cost`](usage-cost/) — report a coding agent's locally recorded token
  usage and what that usage would cost at published API list rates. See its
  [README](usage-cost/README.md) for example output.
- [`verify-numbers`](verify-numbers/) — verify counts, deltas, estimates,
  remembered values, and time-window metrics before quoting them. Its vendored
  full-line counter exposes every match so phantom substring counts are
  inspectable. See its [README](verify-numbers/README.md) for the falsifiable
  claim, example output, tradeoffs, and limits.
- [`proof-before-done`](proof-before-done/) — execute completion claims against
  live state and emit paste-ready receipts with the exact command or static
  predicate, exit code, observed value, timestamp, and verification status. See
  its [README](proof-before-done/README.md) for the falsifiable claim, fixture
  output, tradeoffs, and limits.
- [`render-check`](render-check/) — review the surface a reader actually sees
  across a declared viewport × theme × state matrix. Its vendored checker
  enumerates uncaptured cells, rejects findings without real screenshot bytes,
  labels source-only inference, and surfaces measured overflow. See its
  [README](render-check/README.md) for the falsifiable claim, fixture output,
  tradeoffs, and limits.
