# AlphaDiana development skills

Repository development skills live in `.agents/skills/`:

- `prepare-contribution`: draft, check, and publish authorized issues and PRs.
- `review-pr`: evidence-first review without automatic edits or publication.
- `write-clear-code`: scoped implementation and refactoring.
- `review-fix-loop`: repeated review and repair when explicitly requested.

These skills were adapted from AlphaApollo commit
`6d1be0e1fe3b5235b10554b0ab2ad5fef06e8aae`. Reference examples retain their
source attribution; they do not prescribe AlphaApollo's layout for Diana.
AlphaApollo's original skills remain available there.

Use the target repository's instructions, templates, code and actual validation
commands. When AlphaHebe reuses these skills through its `alphadiana/` submodule,
the target is still AlphaHebe unless the user asks to change Diana.

These are coding-agent development instructions, not runtime agent skills.
Do not move or modify `alphadiana/harness/skills/` as part of their maintenance.
Do not start GPU experiments, change evaluator criteria, or merge a PR merely
because a development skill is available; preserve the user's task scope.
