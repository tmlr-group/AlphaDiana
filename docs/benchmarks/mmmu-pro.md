# MMMU-Pro

MMMU-Pro evaluates multimodal multiple-choice reasoning on `MMMU/MMMU_Pro`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
```

When running from a local checkout, prefer the module entrypoint:

```bash
python -m alphadiana.cli env
```

## Supported Modes

| Mode | Status | Smoke / Debug Config |
|---|---|---|
| `direct_llm` | smoke/debug supported | `configs/macro_runs/mmmu_pro_directllm_qwen35_27b.yaml` |
| `openclaw` | smoke/debug supported | `configs/macro_runs/mmmu_pro_openclaw_qwen35_27b.yaml` |
| `opencode` | smoke/debug supported | `configs/macro_runs/mmmu_pro_opencode_qwen35_27b.yaml` |
| `zeroclaw` | smoke/debug supported | `configs/macro_runs/mmmu_pro_zeroclaw_qwen35_27b.yaml` |

## Historical Podman Multimodal Readiness

> [!CAUTION]
> This records an earlier Podman validation. The current release configs select
> ROCK for OpenClaw/ZeroClaw and Docker for OpenCode, while the historical audit
> requires `metadata.container_engine=podman`. Do not run this helper as current
> release evidence until its configs and audit are aligned.

Phase 6 added an opt-in Podman readiness matrix for OpenClaw, ZeroClaw, and
OpenCode on three deterministic MMMU-Pro `vision` rows:

- `mmmu_pro_test_History_1`
- `mmmu_pro_test_Art_113`
- `mmmu_pro_validation_Design_19`

The three harness cells use the corresponding
`configs/macro_runs/mmmu_pro_*_qwen35_27b.yaml` files. The readiness runner
selects those files and adds a one-task override.

Current status as of May 16, 2026: `Qwen/Qwen3.5-4B` served by the local vLLM
endpoint at `http://127.0.0.1:8011/v1` is verified for both remote
`image_url` and `data:image/png;base64` chat requests on the host and from
Podman `--network host`. The repaired run prefix
`podman_mmmu_pro_qwen35_thinking_20260516_144304` passed the automated
`validate -> preflight -> pilot -> audit` readiness flow. It wrote all 9
expected task rows with `metadata.container_engine=podman`; the audit passed
with `audit_passed=true` and `audit_failure_count=0`.

Historical sequence (not a current release command):

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
export PODMAN_MMMU_PRO_MAX_TOKENS=8192
export PODMAN_MMMU_PRO_ENABLE_THINKING=1
export PODMAN_MMMU_PRO_VLM_IMAGE_URL=https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen3.5/demo/RealWorld/RealWorld-04.png
export HF_HOME=<hf-cache-dir>
export HF_DATASETS_CACHE=<hf-datasets-cache-dir>
export PODMAN_MMMU_RUN_PREFIX=podman_mmmu_pro_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_mmmu_pro_readiness.sh all
```

The patched preflight runs inside Podman, keeps thinking mode on, uses at
least 8192 output tokens, and checks both remote `image_url` and data URL image
inputs. The audit gate is infrastructure readiness, not accuracy: all 9 task
rows must be written with `metadata.container_engine=podman`, image proof, logs
and artifacts, and no text-only fallback or provider VLM rejection. No full MMMU-Pro sweep, Podman default promotion, or legacy runtime deletion is
claimed by this matrix.

## Data Configs

The benchmark supports:

```text
standard (4 options)
standard (10 options)
vision
```

The ZeroClaw smoke config uses `vision` so the benchmark path exercises image
attachment handling inside the sandbox workspace.

Current dataset note: the Hugging Face `vision` subset now exposes a singular
`image` field. AlphaDiana normalizes that payload into `image_1` task
attachments before handing the task to the agent.

Use `data_config: "vision"` in any full config so it matches the image-backed
smoke path rather than a text-only variant.

When using a local Hugging Face snapshot, verify that the selected subset
directory contains actual data files before launching an agent run. A snapshot
that only has `README.md`, `.gitattributes`, and empty
`vision` / `standard (4 options)` / `standard (10 options)` directories fails
at dataset load time with `DataFilesNotFoundError` and is not runnable.

Current full-run caveat on April 22, 2026:
the full `vision` loader is currently front-loaded. The implementation in
`alphadiana/benchmarks/mmmu_pro/benchmark.py` eagerly converts every dataset image into
PNG bytes before it returns the task list, so a full run can spend several
minutes with no task JSONs while still being active. Early OpenRouter evidence:
`full_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_{directllm,openclaw,opencode,zeroclaw}_r1`
all stayed alive with roughly `90-99%` CPU and about `1.6 GiB` RSS before the
first task. Do not classify those full runs as stalled until this preload
phase is ruled out.

## Full Run

The macro configs select the full `vision` split. Add
`-o benchmark.config.max_tasks=1` for a first smoke and retain
`data_config: "vision"` when scaling.

## DirectLLM

Config: `configs/macro_runs/mmmu_pro_directllm_qwen35_27b.yaml`

```bash
python -m alphadiana.cli validate configs/macro_runs/mmmu_pro_directllm_qwen35_27b.yaml
python -m alphadiana.cli run configs/macro_runs/mmmu_pro_directllm_qwen35_27b.yaml \
  -o benchmark.config.data_config=vision \
  -o benchmark.config.max_tasks=1 -o num_samples=1
```

## OpenClaw

Config: `configs/macro_runs/mmmu_pro_openclaw_qwen35_27b.yaml`

```bash
python -m alphadiana.cli validate configs/macro_runs/mmmu_pro_openclaw_qwen35_27b.yaml
python -m alphadiana.cli run configs/macro_runs/mmmu_pro_openclaw_qwen35_27b.yaml \
  -o benchmark.config.data_config=vision \
  -o benchmark.config.max_tasks=1 -o num_samples=1
```

## OpenCode

Config: `configs/macro_runs/mmmu_pro_opencode_qwen35_27b.yaml`

```bash
python -m alphadiana.cli validate configs/macro_runs/mmmu_pro_opencode_qwen35_27b.yaml
python -m alphadiana.cli run configs/macro_runs/mmmu_pro_opencode_qwen35_27b.yaml \
  -o benchmark.config.data_config=vision \
  -o benchmark.config.max_tasks=1 -o num_samples=1
```

The checked-in OpenCode benchmark config now uses Docker controller isolation by
default. Build `alphadiana/tb2-opencode-controller:latest` first if it is not
already present. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

As of April 22, 2026, current main also stops treating OpenCode provider error
bodies as normal MMMU-Pro answers on `qwen3vl`. Historical April 22 artifacts
from the pre-fix full run, such as
`full_20260422_mmmu_pro_vision_opencode_qwen3vl_r1`, can contain synthetic
`predicted="400"` values sourced from provider/tool-choice error bodies and
should be treated as audit-only evidence. Current main records those failures
as explicit provider errors instead.

Current OpenRouter free-VLM evidence on April 22, 2026:
`smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_opencode_t1_r1`
wrote a normal scored `mmmu_pro_test_History_1` task with
`metadata.transport=opencode_cli_container` and `metadata.num_attachments=1`.

### Qwen/OpenRouter Vision Pilot (2026-04-19/20)

Accepted local pilot:

- run_id: `pilot_20260419_qwen35_27b_mmmu_pro_opencode_t3_vision_docker`
- result: `3/3` normal trajectories in Docker isolation, one task scored `0`
  but no abnormal behavior
- run_id:
  `pilot_20260420_qwen35_27b_mmmu_pro_opencode_t3_vision_docker_default`
- result: `3/3` normal Docker-default trajectories with scores `0/1/1`, and
  every task recorded `num_attachments=1`

Historical non-canonical run:

- `pilot_20260419_qwen35_27b_mmmu_pro_opencode_t3_vision`
  wrote `3/3` normal task records, but it used `controller_mode=host` before
  the checked-in config default switched to Docker isolation, so it is kept as
  historical evidence only

Command:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b

python -m alphadiana.cli run configs/macro_runs/mmmu_pro_opencode_qwen35_27b.yaml \
  -o run_id=pilot_20260420_qwen35_27b_mmmu_pro_opencode_t3_vision_docker_default \
  -o benchmark.config.data_config=vision \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b \
  -o agent.config.model=custom/qwen/qwen3.5-27b
```

## ZeroClaw

Config: `configs/macro_runs/mmmu_pro_zeroclaw_qwen35_27b.yaml`

ZeroClaw benchmark smoke is documented only for sandboxed execution:

- ROCK sandbox
- in-sandbox ZeroClaw CLI
- `data_config: "vision"`
- `max_tasks: 1`

Start ROCK first:

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

Then validate and run:

```bash
python -m alphadiana.cli validate configs/macro_runs/mmmu_pro_zeroclaw_qwen35_27b.yaml
python -m alphadiana.cli run configs/macro_runs/mmmu_pro_zeroclaw_qwen35_27b.yaml \
  -o run_id=mmmu_pro_zeroclaw_smoke
```

On an OpenAI-compatible `qwen3vl` endpoint, multimodal ZeroClaw currently works most
reliably through the same single sandbox CLI path now used for HLE on current
main. No extra override is required for the intended path: when a live ROCK
sandbox is present and the task carries image attachments, AlphaDiana uploads
them into the sandbox workspace, appends `[IMAGE:<absolute sandbox path>]`
markers to the prompt, and runs the stock `zeroclaw agent` CLI there. The
transport marker is `metadata.transport=zeroclaw_cli_sandbox`.

This turn's fresh real-run image proofs are:
`smoke_20260422_zeroclaw_cli_sandbox_hle53_nemotron_vl_t1`, which wrote
`tasks/hle_53.json`, and
`smoke_20260422_zeroclaw_cli_sandbox_mmmu_nemotron_vl_t1`, which wrote
`tasks/mmmu_pro_test_History_1.json`. Both task records preserve
`metadata.transport=zeroclaw_cli_sandbox`, attachment artifacts, the
`[IMAGE:<absolute sandbox path>]` prompt marker, and explicit in-sandbox
OpenRouter `429` failure evidence. Treat them as execution-path proofs rather
than quality claims.

Historical `disable_tools=true` runs such as
`smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_zeroclaw_disable_tools_t1_r1`
remain useful audit evidence for the old workaround only.

When the path still fails, current main preserves explicit failure metadata
such as `metadata.failure_reason=empty_response` or `provider_error` so the
task JSON remains diagnosable.

### Qwen/OpenRouter Vision Pilot (2026-04-19)

Accepted local pilot:

- run_id: `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r3`
- result: `3/3` normal trajectories on the then-current direct in-sandbox CLI
  path, preserved `attachments/image_1.png`, no provider warning, and no
  `command_history` pollution in successful task metadata

Rejected earlier attempts from the same day:

- `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision`
  one task failed after a ROCK proxy `http proxy failed` fallback hit a binary
  upload decode bug
- `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r1`
  the binary upload decode bug was fixed, but the first fallback implementation
  still hit `/bin/sh` argument-length limits on a large image attachment
- `pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r2`
  wrote `3/3` normal task records, but it still emitted a ZeroClaw config
  warning and preserved noisy sandbox `command_history`, so it is retained as
  historical evidence only

Current recommended OpenRouter/Qwen command:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b

python -m alphadiana.cli run configs/macro_runs/mmmu_pro_zeroclaw_qwen35_27b.yaml \
  -o run_id=pilot_20260419_qwen35_27b_mmmu_pro_zeroclaw_t3_vision_r3 \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=1
```

### OpenRouter Free-VLM Smoke (2026-04-22)

Current accepted OpenRouter smoke uses:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=nvidia/nemotron-nano-12b-v2-vl:free
```

Accepted run IDs:

- `smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_direct_llm_t1_r1`
- `smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_opencode_t1_r1`
- `smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_openclaw_t1_r1`
- `smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_zeroclaw_disable_tools_t1_r1`

Observed current behavior:

- all four runs wrote normal scored `mmmu_pro_test_History_1` task JSONs
- `openclaw` recovered from one initial empty-body `http proxy failed` and
  then completed normally on attempt 2
- `zeroclaw` now targets the native in-sandbox multimodal path by default on
  image-backed MMMU-Pro rows. Historical `disable_tools=true` runs remain
  workaround evidence only; rerun MMMU-Pro on current main to refresh
  benchmark-specific support evidence

Early full-run follow-up on the same provider:

- `full_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_{directllm,openclaw,opencode,zeroclaw}_r1`
  were all launched on the checked-in full configs
- by the `2026-04-22 17:15 +0800` snapshot, all four were still in the loader
  preload phase described above, so no task JSON had appeared yet
- later the same day, `..._openclaw_r1` and `..._zeroclaw_r1` did start
  writing task JSONs after the preload cleared
- `openclaw` still shows the usual first-attempt empty-body retry and can
  preserve free-form answers or `metadata.partial_reasoning_only=true` on some
  MMMU-Pro rows
- historical `zeroclaw` results from that run family still reflect the older
  `rock-proxy-fallback-no-tools` workaround; current main now uses the same
  single sandbox CLI transport re-proved on HLE in
  `smoke_20260422_zeroclaw_cli_sandbox_hle53_nemotron_vl_t1`

## Result Locations

- `direct_llm`: `./results/`
- `openclaw`: `./results/openclaw_mmmu_pro/`
- `opencode`: `./results/opencode_mmmu_pro/`
- `zeroclaw`: `./results/zeroclaw_mmmu_pro_smoke/`
