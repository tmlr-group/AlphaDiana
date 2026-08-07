# Skill axis

Configurations for the Skill axis (model reads pre-defined skill bundles
during reasoning).

## Mechanism

Each yaml sets `agent.config.skill_folder: "<bundle_name>"`. The harness
(`opencode` or `zeroclaw`) resolves bare names against
`alphadiana/harness/skills/<bundle_name>/`, then makes the bundle reachable to
the model:

- **OpenCode**: `shutil.copytree(skill_folder, workdir/skills/<name>/)`
  before launching the docker controller. Model reads with the `read` tool
  using path `./skills/<name>/SKILL.md`.
- **ZeroClaw**: `_upload_skill_folder()` walks the bundle and uploads each
  file via the ROCK `sandbox.upload()` API into
  `<workspace_dir>/skills/<name>/`. Model reads with the `shell` tool
  using `cat ~/.zeroclaw/workspace/skills/<name>/SKILL.md`.

The system prompt must explicitly instruct the model to consult the skill —
neither harness auto-injects skill content into context.

## Available skill bundles

- `advanced-maths` — disciplined symbolic + numeric reasoning, 7-step protocol
- `anthropic-bundle` — Anthropic's official 18 sub-skills (pdf/xlsx/pptx/etc.)

See `alphadiana/harness/skills/README.md` for bundle details.

## Naming

Mirrors `../Tool/`: `{benchmark}_{harness}_{model_short}_skill_{bundle_short}.yaml`,
where `bundle_short` is `math` (advanced-maths) or `anth` (anthropic-bundle).

## Cells

| File | Smoke? |
|---|---|
| `aime2026_zeroclaw_qwen35_27b_skill_math.yaml` | smoke (max_tasks=1) |
| `gpqa_opencode_qwen35_27b_skill_anth.yaml` | smoke (max_tasks=1) |

Production cells (full benchmark) live elsewhere and reference the same
`skill_folder` resolution mechanism.

## Smoke commands

Run from the repository root after following `../README.md`:

```bash
python -m alphadiana.cli validate \
  configs/micro_runs/Skill/aime2026_zeroclaw_qwen35_27b_skill_math.yaml
python -m alphadiana.cli run \
  configs/micro_runs/Skill/aime2026_zeroclaw_qwen35_27b_skill_math.yaml \
  -o run_id=smoke_skill_math_zeroclaw --redo-all

python -m alphadiana.cli validate \
  configs/micro_runs/Skill/gpqa_opencode_qwen35_27b_skill_anth.yaml
python -m alphadiana.cli run \
  configs/micro_runs/Skill/gpqa_opencode_qwen35_27b_skill_anth.yaml \
  -o run_id=smoke_skill_anth_opencode \
  -o benchmark.config.max_tasks=1 --redo-all
```
