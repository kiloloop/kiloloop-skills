## Goal
The example-repo CLI prints its version from the package metadata instead of a hard-coded string, so a release bump needs one edit. The requester's words: "one source of truth for the version". Precedent: the config loader already reads metadata this way.

## Instruction
The how is yours: your skills, project instructions and runbooks own the mechanics. Work out the details, verify what you need, and execute toward the goal. Where this brief and those surfaces disagree, follow them and say so in the reply.
Background only the requester holds: the release script reads the printed version, so the output format must not change.

## Fence
- Out of scope: the release script itself · the changelog beyond one line.
- The pull request is yours to merge after review (the repository's standing rule).

## Done
Done = the change landed on the default branch of example-org/example-repo with one changelog line. Reply with the landed commit and the pull-request link.

## Context
- Repo: example-org/example-repo · main at the commit you start from
- Estimate: 30m · files ~3 (rough)
- Side effects: commit · pull request · merge
