# Skills

Skill bundles consumed by `agent.config.skill_folder` in benchmark configs.
Each bundle is a directory containing a top-level `SKILL.md` describing what
the skill does, plus optional sub-files (references, sub-skill folders, etc.).

## Bundles in this directory

### `advanced-maths/` (~384 KB)
Forces disciplined symbolic + numeric reasoning. 7-step protocol covering
restate, tool-pick, step-out, unit audit, magnitude sanity-check, second-route
verification, commit. Adapted from `rand/cc-polymath/skills/math`.

### `anthropic-bundle/` (~15 MB)
Anthropic's official Skills SDK content (Apache 2.0 for most, source-available
for `docx/pdf/pptx/xlsx`). 18 sub-skills including `pdf`, `xlsx`, `pptx`,
`canvas-design`, `webapp-testing`, `mcp-builder`, `skill-creator`, etc.
The bundle's top-level `SKILL.md` is an index pointing to each sub-skill.
Source: https://github.com/anthropics/skills

See `anthropic-bundle/THIRD_PARTY_NOTICES.md` for license details on
third-party assets (ImageIO, FFmpeg, OOXML schemas, fonts).

## Usage from a config yaml

```yaml
agent:
  config:
    # Bare name — resolves to alphadiana/skills/<name>/
    skill_folder: "advanced-maths"
```

Other accepted forms:
- Relative path: `skill_folder: "alphadiana/skills/advanced-maths"` (resolved against cwd)
- Absolute path: `skill_folder: "/abs/path/to/your_skill_dir"`

The harness then makes the bundle reachable to the model:
- **OpenCode** (`opencode.py`): `shutil.copytree` the bundle into the
  per-task docker workdir at `<workdir>/skills/<name>/`. The model uses
  the opencode `read` tool with path `./skills/<name>/SKILL.md`.
- **ZeroClaw** (`zeroclaw.py`): walks the bundle locally and
  `sandbox.upload()`s each file into the ROCK sandbox at
  `<workspace_dir>/skills/<name>/` (mirrored as
  `~/.zeroclaw/workspace/skills/<name>/`). The model uses zeroclaw's
  `shell` tool with `cat ~/.zeroclaw/workspace/skills/<name>/SKILL.md`.

The system prompt must instruct the model to consult the skill — neither
harness auto-injects skill content into context (cf. Claude Code, which
auto-injects skill frontmatter metadata at session start).
