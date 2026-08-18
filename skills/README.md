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
