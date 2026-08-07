# Configuration map

`configs/` contains several kinds of YAML. They do not all have the same
top-level schema, so choose an entry point by purpose instead of assuming every
file can be passed directly to `alphadiana run`.

## Start here

| Goal | Entry point |
| --- | --- |
| Run the first DirectLLM smoke | `examples/direct_llm.yaml` with a one-task override |
| Browse ad-hoc harness/benchmark examples | `examples/` |
| Run a checked-in Podman readiness matrix | `smokes/` and its per-folder `README.md` |
| Reproduce Tool/Skill/Memory micro cells | `micro_runs/README.md` |
| Run the shipped memory experiment | `memory_experiments/exp1_zw_aime_memory_seq.yaml` |
| Render the SWE-bench Verified Mini campaign | `full_runs/swe_verified_mini.yaml` |
| Inspect the annotated core experiment shape | `schema.yaml` |
| Compare canonical benchmark prompts | `PROMPTS.md` |

Validate an ordinary experiment config before running it:

```bash
python -m alphadiana.cli validate configs/examples/direct_llm.yaml \
  -o benchmark.config.max_tasks=1
python -m alphadiana.cli run configs/examples/direct_llm.yaml \
  -o run_id=config_map_aime_directllm_t1_k1 \
  -o benchmark.config.max_tasks=1 -o num_samples=1
```

Provider-backed examples normally require endpoint, key, and model variables.
Read the target YAML because older examples use either
`OPENAI_MODEL`/`OPENAI_API_BASE` or
`OPENAI_MODEL_NAME`/`OPENAI_BASE_URL`. Never commit a real key.

## Directory roles

### `examples/`

Runnable or validation-oriented examples for DirectLLM, OpenClaw, OpenCode,
ZeroClaw, SWE-bench, Terminal-Bench 2, DecodingTrust, and Podman
paths. Some are single-task smokes; others are larger templates. The filename
and comments are not proof of support: inspect the matching page under
`docs/benchmarks/` or `docs/harnesses/` for prerequisites and current caveats.

Useful first entries:

- `direct_llm.yaml`: sandbox-free AIME baseline used by Quick Start with a
  one-task CLI override.
- `openclaw_aime2024.yaml`: OpenClaw ROCK auto-deploy template.
- `opencode_aime_podman_smoke.yaml`: OpenCode Podman smoke.
- `zeroclaw_aime2026.yaml`: ZeroClaw with a live ROCK sandbox.
- `openclaw_decodingtrust_finance_cli.yaml`: DecodingTrust-specific OpenClaw CLI path.

`zeroclaw_aime2026_local_smoke.yaml` is a legacy validation fixture, not a
runnable host-mode example: current generic ZeroClaw requires a live
sandbox/container session.

Files ending in `.local.yaml` are local override templates. Review every path,
image, endpoint, and environment variable before use.

### `smokes/`

Each active smoke matrix has its own README and runner/auditor scripts:

| Folder | Scope | User-facing guide |
| --- | --- | --- |
| `podman_scale_readiness/` | Standard-reasoning OpenClaw/OpenCode/ZeroClaw cells | `docs/benchmarks/index.md` and harness pages |
| `podman_mmmu_pro_readiness/` | MMMU-Pro multimodal Podman pilot | `docs/benchmarks/mmmu-pro.md` |
| `podman_terminal_bench2/` | Terminal-Bench 2 task containers | `docs/benchmarks/terminal-bench-2.md` |
| `podman_swe_verified_readiness/` | SWE-bench Verified task containers | `docs/benchmarks/swebench-verified.md` |
| `podman_nightly_validation/` | Validation-only cross-path matrix | folder README |

These are opt-in evidence/configuration paths. Their presence does not promote
Podman to a global default.

### `micro_runs/`

Paper-oriented Tool, Skill, and Memory cells. Start at
`micro_runs/README.md`. These configs can be expensive and may require local
images, skills, or provider-specific tool-call support; do not treat them as
Getting Started smokes.

### `memory_experiments/`

The current checkout ships only
`exp1_zw_aime_memory_seq.yaml`. Other experiment names discussed in drafts are
not published here and must not be used as runnable paths.

### `full_runs/`

The current checkout contains only `swe_verified_mini.yaml`. It is a rollout
campaign manifest consumed by `python -m alphadiana.benchmark_rollout_cli`, not
an ordinary `ExperimentConfig` for `alphadiana run`. See
`docs/benchmarks/swebench-verified-mini.md` for render, preflight, and launch
commands.

## Root files

| File | Purpose |
| --- | --- |
| `schema.yaml` | Annotated core experiment shape; pass-through keys are documented per harness/benchmark |
| `PROMPTS.md` | Prompt catalogue |
| `test_openclaw_quick.yaml` | OpenClaw sanity config; requires provider and ROCK prerequisites |

## Naming conventions

Names are descriptive rather than executable contracts:

- `_smoke`: intended small smoke path;
- `_pilot`: limited pilot rather than full evaluation;
- `.local`: host-specific override template;
- `_podman`: Podman-backed path;
- `_logprobs`: logprob capture enabled.

Always confirm the actual `benchmark.config`, `num_samples`, runtime backend,
model/reasoning controls, scorer, and `run_id` before launch.
