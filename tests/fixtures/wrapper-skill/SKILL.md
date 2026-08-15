---
name: wrapper-pin-example
description: Exercise the clean-install contract for a published CLI wrapper.
requires:
  - cowsay==6.1
wrapper:
  entrypoint: scripts/cowsay_wrapper.py
  smoke_args: ["--help"]
---

# Wrapper pin example

Install the exact CLI version declared by this skill:

```bash
python -m pip install cowsay==6.1
```

The adapter delegates to the installed `cowsay` command. It never installs a
dependency while running and reports the exact recovery command when the pin
is absent.
