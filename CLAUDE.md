## Claude Code notes

- Use plan mode for non-trivial changes that touch multiple files, configs, or docs.
- If Claude seems to ignore instructions, use `/memory` to confirm which `CLAUDE.md` files were loaded.
- Keep root-level Claude guidance small. If an instruction is directory-local or procedural, move it to a nested `CLAUDE.md`, `.claude/rules/`, or a hook.
