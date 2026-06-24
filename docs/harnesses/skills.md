---
sidebar_position: 6
---

# Skills

Skills are file bundles you mount into a harness sandbox so the model can read
domain guidance (a reasoning protocol, a document toolkit, sub-skill indexes)
at solve time. A skill is just a directory under `alphadiana/harness/skills/<name>/`
with a top-level `SKILL.md`. AlphaDiana copies that directory into the agent's
workspace; the model reaches it with its normal file tools.

Skills are consumed by the [OpenCode](../harnesses/opencode) and
[ZeroClaw](../harnesses/zeroclaw) harnesses through the single config key
`agent.config.skill_folder`. The [direct_llm](../harnesses/direct_llm) baseline
has no sandbox and ignores skills.

## Bundle layout

Each bundle is a folder with a `SKILL.md` at its root. `SKILL.md` opens with
YAML frontmatter (`name`, `description`) followed by the instructions:

```markdown
---
name: advanced-maths
description: Use when a question involves numerical computation, algebra,
  calculus, linear algebra, probability, or symbolic manipulation.
---

# Advanced Maths — Disciplined Symbolic + Numeric Reasoning
...
```

A bundle may carry arbitrary sub-files: `references/`, sub-skill folders, each
with their own `SKILL.md`. Nothing about the file tree is special; the harness
copies it verbatim.

## Shipped bundles

Two bundles live under `alphadiana/harness/skills/`:

| Bundle | Size | What it is |
| --- | --- | --- |
| `advanced-maths` | ~384 KB | A 7-step symbolic + numeric reasoning protocol (restate, tool-pick, step-out, unit audit, magnitude sanity-check, second-route verification, commit), plus a `references/` tree. Adapted from `rand/cc-polymath/skills/math`. |
| `anthropic-bundle` | ~15 MB | Anthropic's official Skills SDK content: 18 sub-skills under `skills/` (`pdf`, `xlsx`, `pptx`, `canvas-design`, `webapp-testing`, `mcp-builder`, `skill-creator`, and more). The top-level `SKILL.md` is an index pointing to each sub-skill. Source: https://github.com/anthropics/skills |

See `anthropic-bundle/THIRD_PARTY_NOTICES.md` for license details on bundled
third-party assets (ImageIO, FFmpeg, OOXML schemas, fonts).

## Selecting a skill from config

Set `skill_folder` under `agent.config`. The bare name resolves against the
shipped skills directory:

```yaml
agent:
  name: opencode
  config:
    # Bare name -> alphadiana/harness/skills/<name>/
    skill_folder: "advanced-maths"
```

### `skill_folder` resolution

`skill_folder` is resolved by `_resolve_skill_folder(raw)`, defined identically
in `alphadiana/harness/opencode/agent.py:43` and
`alphadiana/harness/zeroclaw/agent.py:44`. The accepted forms:

| Value | Resolves to |
| --- | --- |
| empty / unset | `None` — skill mounting disabled |
| absolute path (e.g. `/abs/path/to/skill`) | used as-is |
| path containing `/`, or a path that exists | resolved against the current working directory |
| bare name (e.g. `advanced-maths`) | `alphadiana/harness/skills/<name>/` |

The resolved path is stored as `self._skill_folder` during `setup()`
(opencode `agent.py:761`, zeroclaw `agent.py:550`). If it cannot be found at
mount time the run fails fast (`skill_folder not found: ...`).

## How each harness mounts the bundle

The two harnesses make the bundle reachable to the in-sandbox model, but by
different mechanisms.

### OpenCode — `shutil.copytree`

OpenCode copies the bundle into the per-task workdir with
`shutil.copytree(self._skill_folder, <workdir>/skills/<name>)`
(`alphadiana/harness/opencode/agent.py:1091`, repeated at `1257` and `1331`
for the host / docker / podman controller modes). Because the files are real
files in the workdir, the opencode `read` tool can index and open them:

```
./skills/<name>/SKILL.md
```

### ZeroClaw — `sandbox.upload`

ZeroClaw cannot copy into the sandbox filesystem directly. `_upload_skill_folder`
(`alphadiana/harness/zeroclaw/agent.py:1349`) `rglob`-walks the bundle, creates
each parent directory inside the sandbox, then `sandbox.upload()`s every file to
`<workspace_dir>/skills/<name>/` (mirrored as `~/.zeroclaw/workspace/skills/<name>/`).
The model reaches it through the `shell` tool:

```bash
cat ~/.zeroclaw/workspace/skills/<name>/SKILL.md
```

## Skills are NOT auto-injected

Neither harness places skill content into the model's context. Mounting only
puts the files where the model *can* read them. The model will not read
`SKILL.md` unless told to, so the **system prompt must instruct it to**, for
example: "A skill is available at `./skills/<name>/SKILL.md` — read it before
solving."

This is a deliberate contrast with Claude Code, which auto-injects skill
frontmatter metadata at session start. In AlphaDiana, skill efficacy is a
prompt-level concern: the bundle is inert until the system prompt points the
model at it. Set the instruction with `agent.config.system_prompt` (see
[OpenCode](../harnesses/opencode) and [ZeroClaw](../harnesses/zeroclaw)).

## Adding your own bundle

1. Create `alphadiana/harness/skills/<your-skill>/SKILL.md` with `name` and
   `description` frontmatter, plus any sub-files.
2. Reference it: `agent.config.skill_folder: "<your-skill>"` (or point at an
   absolute / relative path anywhere on disk).
3. Add the read instruction to `agent.config.system_prompt`.

No registration step is needed — skills are loaded by path, not through the
[AgentRegistry](../harnesses/overview). Captured trajectories and answers land
in the result store (`alphadiana/analysis/io/result_store.py`) like any other
run.
