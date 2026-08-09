# Skill axis — paper Table 3

These configs reproduce the Skill experiment reported in Table 3 of the
[AlphaDiana paper](https://openreview.net/forum?id=4vARlk9o95). It uses the same
8 model-harness-benchmark reference cells as the Tool study and compares the
matched Full Harness baseline with two loaded procedural libraries:

- **Math Skill**: bundled as `advanced-maths`.
- **General Skill**: bundled as `anthropic-bundle`.

OpenClaw is not part of Table 3.

## Paper matrix

| Harness | Model | GPQA-Diamond | AIME 2026 |
|---|---|---:|---:|
| ZeroClaw | Qwen3.5-27B | Math + General | Math + General |
| OpenCode | Qwen3.5-27B | Math + General | Math + General |
| ZeroClaw | Kimi-K2.6 | Math + General | Math + General |
| OpenCode | Kimi-K2.6 | Math + General | Math + General |

The directory therefore contains 16 skill-loaded YAMLs. The 8 matched Full
Harness baselines are the `_tool_full.yaml` files in `../Tool/`; they are shared
instead of duplicated.

Each Skill YAML uses a bare `agent.config.skill_folder` name. The harness
resolves it against `alphadiana/harness/skills/`, so the release contains no
machine-specific `/data*` path. OpenCode copies the bundle into
`./skills/<name>/`; ZeroClaw uploads it to
`~/.zeroclaw/workspace/skills/<name>/`. The prompts instruct the model to read
`SKILL.md`, matching the paper intervention.

Filenames end in `_skill_math.yaml` or `_skill_general.yaml`.
