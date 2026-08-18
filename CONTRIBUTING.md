# Contributing

## Repository contract

Each `skills/<name>/` directory is one independent installation and
distribution unit. "Self-contained" means independently installable from its
own directory using only public, versioned dependencies. A skill must use one
of the two validated forms below.

### Vendored implementation

The original form carries its implementation bytes with the skill:

```text
skills/<name>/
  SKILL.md
  scripts/
    <embedded implementation>
  fixtures/
    test_<claim>.py
```

### Version-pinned wrapper

A thin wrapper may delegate to a published package when `SKILL.md` declares an
exact pin and the wrapper probe:

```yaml
---
name: example-wrapper
description: Run a published CLI through a skill adapter.
requires:
  - example-cli==1.2.3
wrapper:
  entrypoint: scripts/example_wrapper.py
  smoke_args: ["--help"]
---
```

The same `SKILL.md` must include a directly runnable install instruction for
every pin:

```bash
python -m pip install example-cli==1.2.3
```

Wrapper-form skills must satisfy all of these obligations:

- Every `requires:` entry uses exact `package==version` syntax. Ranges,
  wildcards, URLs, VCS references, local paths, and floating aliases are not
  accepted.
- The package and version resolve from the public package index in a clean
  virtual environment, and the resolved distribution version equals the pin.
- `wrapper.entrypoint` is a Python adapter under the skill's `scripts/`
  directory, and `wrapper.smoke_args` exercises a successful, side-effect-free
  command such as `--help` or `--version`.
- The wrapper does not install dependencies at runtime. When a dependency is
  absent it exits `2` and prints the exact pin plus an actionable
  `python -m pip install ...` command.
- Fixture tests run against the clean environment exposed through
  `SKILL_CLEAN_PYTHON` and `SKILL_ROOT`; they must invoke the real wrapper and
  prove a meaningful behavior claim.

Both forms must keep fixtures deterministic. Do not depend on an unpublished
repository or a package available only from a private index.

## Where changes originate

Skill behavior, packaging, and runtime-adapter changes originate in this
repository. When a skill wraps a separately maintained tool, fix broadly
applicable tool behavior in that tool's owning repository before refreshing the
pin or fixtures here. Keep adapter-specific behavior and skill instructions in
this repository so the development source and distributed skill do not drift.

## Public-content checklist

Everything that survives `git archive` must be ready for public distribution:

- Use portable paths and neutral example identities.
- Exclude personal data, credentials, private repository names, and internal
  governance or review history.
- Keep provenance in private pull-request metadata, not distributed files.
- Keep generated artifacts and environment-specific state out of commits.
- Do not cite issues or pull requests by number. A short `#<number>` reference
  resolves against whichever repository renders it, so it points somewhere
  unintended once the file is distributed. Link to a full URL when a reference
  is genuinely needed.

## Changelog

Record every user-visible change in [CHANGELOG.md](CHANGELOG.md) under
`Unreleased`, in the same pull request that makes the change. Entries are read
by people installing the skills, so describe the distributed artifact and the
behavior they can observe rather than the development history that produced it.

A change to a wrapper-form skill's `requires:` pin is always user-visible.
Record it as an entry and add the new pin to the pinned-tool-versions table
when the release is cut.

## Validation

Run the same gates as CI before opening a pull request:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
python scripts/run_skill_fixtures.py
```
