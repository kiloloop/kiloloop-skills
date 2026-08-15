---
name: memory-lint
description: Lint Markdown memory and documentation corpora with the deterministic memory-lint CLI. Use when validating frontmatter, links, indexes, managed markers, staleness, structure, or revision changes without modifying the corpus.
requires:
  - memory-lint==0.1.0
wrapper:
  entrypoint: scripts/memory_lint_wrapper.py
  smoke_args: ["--version"]
---

# Memory Lint

Use the version-pinned adapter to run `memory-lint` as a read-only diagnostic.
It preserves the CLI's output and exit status and never installs dependencies
while running.

## Install

Install the exact supported release into the Python environment that will run
the adapter:

```bash
python -m pip install memory-lint==0.1.0
```

## Workflow

1. Locate the corpus configuration file. Every input is a named flag; do not
   pass a positional corpus path.
2. Prefer JSON for machine parsing and table output for a person reading the
   command directly.
3. Use `--now YYYY-MM-DD` when results must be reproducible across runs.
4. Run the adapter from this skill directory:

   ```bash
   python scripts/memory_lint_wrapper.py \
     --config path/to/config.yaml \
     --format json \
     --now YYYY-MM-DD
   ```

5. Interpret the exit status before reporting results:

   - `0`: clean corpus
   - `1`: one or more findings
   - `2`: usage, configuration, filesystem, Git, dependency, or abnormal
     delegated-process error

An exit status of `1` is a successful lint run with findings, not an execution
failure. Parse JSON only when the command produced valid JSON.

## Corpus and Revision Options

- Use `--corpus-root <path>` to override `corpus_root` from the configuration
  without editing the file.
- Use `--against <git-ref>` only when the corpus is inside a Git repository and
  the ref is available locally. The CLI performs read-only Git operations.
- Supply `--compare-before` and `--compare-after` together to detect identical
  or whitespace-only revisions.
- Run `python scripts/memory_lint_wrapper.py --help` for the complete flag
  surface.

## Report

Include the executed command, configuration and corpus paths, exit status,
finding count, and findings grouped by severity and code. Report exit-2 errors
verbatim enough to make the next action clear. Do not edit or auto-fix corpus
files on the strength of lint output alone.
