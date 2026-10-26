# AlphaDiana repository guidance

Use the target repository's instructions, templates, code and actual validation
commands. When AlphaHebe uses its `alphadiana/` submodule,
the target is still AlphaHebe unless the user asks to change Diana.

These are coding-agent development instructions, not runtime agent skills.
Do not move or modify `alphadiana/harness/skills/` as part of their maintenance.
Do not start GPU experiments, change evaluator criteria, or merge a PR merely
because a development skill is available; preserve the user's task scope.
