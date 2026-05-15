# MMMU-Pro

MMMU-Pro evaluates multimodal multiple-choice reasoning on `MMMU/MMMU_Pro`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

When running from a local checkout, prefer the module entrypoint:

```bash
python -m alphadiana.cli env
```

## Supported Modes

| Mode | Status | Smoke / Debug Config | Full-run Config |
|---|---|---|---|
| `direct_llm` | smoke/debug supported | `configs/examples/direct_llm_mmmu_pro.yaml` | `configs/full_runs/rollout_full_directllm_mmmu_pro_vision.yaml` |
| `openclaw` | smoke/debug supported | `configs/examples/openclaw_mmmu_pro.yaml` | `configs/full_runs/rollout_full_openclaw_mmmu_pro_vision.yaml` |
| `opencode` | smoke/debug supported | `configs/examples/opencode_mmmu_pro.yaml` | `configs/full_runs/rollout_full_opencode_mmmu_pro_vision.yaml` |
| `zeroclaw` | smoke/debug supported | `configs/examples/zeroclaw_mmmu_pro.yaml` | `configs/full_runs/rollout_full_zeroclaw_mmmu_pro_vision.yaml` |

## Podman Multimodal Readiness

Phase 6 added an opt-in Podman readiness matrix for OpenClaw, ZeroClaw, and
OpenCode on three deterministic MMMU-Pro `vision` rows:

- `mmmu_pro_test_History_1`
- `mmmu_pro_test_Art_113`
- `mmmu_pro_validation_Design_19`

Configs live under `configs/smokes/podman_mmmu_pro_readiness/`.

Current status as of May 15, 2026: config validation passes, but the live
provider preflight failed for `OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B` against
`http://localhost:8011/v1`. From inside Podman, `/v1/models` was reachable,
but the tiny image `chat/completions` request returned HTTP 400, so the
9-task pilot was not launched. This is not MMMU-Pro Podman multimodal
readiness evidence.

Use this sequence before any future pilot attempt:

```bash
export OPENAI_BASE_URL=<openai-compatible-vlm-base-url>
export OPENAI_API_KEY=<secret>
export OPENAI_MODEL_NAME=<real-vlm-model>
export HF_HOME=<hf-cache-dir>
export HF_DATASETS_CACHE=<hf-datasets-cache-dir>
export PODMAN_MMMU_RUN_PREFIX=podman_mmmu_pro_$(date +%Y%m%d_%H%M%S)

bash scripts/run_podman_mmmu_pro_readiness.sh validate
bash scripts/run_podman_mmmu_pro_readiness.sh preflight
```

Only launch the Phase 6 pilot after the Podman-context image preflight passes
with the same provider base URL and model. Evidence for the failed
`Qwen/Qwen3.5-4B` attempt is recorded in
[`context/podman-mmmu-pro-readiness/README.md`](../../context/podman-mmmu-pro-readiness/README.md).
No full MMMU-Pro sweep, Podman default promotion, or legacy runtime deletion is
claimed by this matrix.

For the staged `72`-run local-vLLM campaign, use
[full-rollout-local-vllm-20260419.md](full-rollout-local-vllm-20260419.md)
instead of launching the four full configs manually.

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

The checked-in full configs also use `data_config: "vision"` so the full run
matches the image-backed path rather than a text-only variant.

When using a local Hugging Face snapshot, verify that the selected subset
directory contains actual data files before launching an agent run. A snapshot
that only has `README.md`, `.gitattributes`, and empty
`vision` / `standard (4 options)` / `standard (10 options)` directories fails
at dataset load time with `DataFilesNotFoundError` and is not runnable.

Current full-run caveat on April 22, 2026:
the full `vision` loader is currently front-loaded. The implementation in
`alphadiana/benchmark/mmmu_pro.py` eagerly converts every dataset image into
PNG bytes before it returns the task list, so a full run can spend several
minutes with no task JSONs while still being active. Early OpenRouter evidence:
`full_20260422_openrouter_nemotron_nano_12b_v2_vl_mmmu_pro_{directllm,openclaw,opencode,zeroclaw}_r1`
all stayed alive with roughly `90-99%` CPU and about `1.6 GiB` RSS before the
first task. Do not classify those full runs as stalled until this preload
phase is ruled out.

## Full Run

Validate the four full configs directly:

```bash
python -m alphadiana.cli validate configs/full_runs/rollout_full_directllm_mmmu_pro_vision.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_openclaw_mmmu_pro_vision.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_opencode_mmmu_pro_vision.yaml
python -m alphadiana.cli validate configs/full_runs/rollout_full_zeroclaw_mmmu_pro_vision.yaml
```

## DirectLLM

Config:
[configs/examples/direct_llm_mmmu_pro.yaml](../../configs/examples/direct_llm_mmmu_pro.yaml)

```bash
python -m alphadiana.cli validate configs/examples/direct_llm_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/direct_llm_mmmu_pro.yaml
```

## OpenClaw

Config:
[configs/examples/openclaw_mmmu_pro.yaml](../../configs/examples/openclaw_mmmu_pro.yaml)

```bash
python -m alphadiana.cli validate configs/examples/openclaw_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/openclaw_mmmu_pro.yaml
```

## OpenCode

Config:
[configs/examples/opencode_mmmu_pro.yaml](../../configs/examples/opencode_mmmu_pro.yaml)

```bash
python -m alphadiana.cli validate configs/examples/opencode_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/opencode_mmmu_pro.yaml
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

python -m alphadiana.cli run configs/examples/opencode_mmmu_pro.yaml \
  -o run_id=pilot_20260420_qwen35_27b_mmmu_pro_opencode_t3_vision_docker_default \
  -o benchmark.config.data_config=vision \
  -o benchmark.config.max_tasks=3 \
  -o max_concurrent=3 \
  -o agent.config.model_name=qwen/qwen3.5-27b \
  -o agent.config.model=custom/qwen/qwen3.5-27b
```

## ZeroClaw

Config:
[configs/examples/zeroclaw_mmmu_pro.yaml](../../configs/examples/zeroclaw_mmmu_pro.yaml)

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
python -m alphadiana.cli validate configs/examples/zeroclaw_mmmu_pro.yaml
python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
  -o run_id=mmmu_pro_zeroclaw_smoke
```

On the OpenAI-compatible `qwen3vl` endpoint, multimodal ZeroClaw currently works most
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

### Reproduce The 2026-04-18 Sandbox Smoke

This smoke run intentionally returns a fixed option letter so the benchmark path
finishes quickly while still proving that attachment upload, prompt plumbing,
and scoring all complete.

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5

python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
  -o run_id=pr23_smoke_zeroclaw_mmmupro_minimaxm25_boxA_20260418 \
  -o output_dir=./results/pr23_zeroclaw_smokes \
  -o agent.config.system_prompt='Smoke test mode: ignore the question and attachments. Do not use tools. Output exactly $$\\boxed{A}$$ and nothing else.'
```

Observed local verification on 2026-04-18:

- run_id: `pr23_smoke_zeroclaw_mmmupro_minimaxm25_boxA_20260418`
- result: dashboard `X`, `predicted=A`, `ground_truth=B`, no `error`
- execution mode: ROCK sandbox + in-sandbox ZeroClaw CLI

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

python -m alphadiana.cli run configs/examples/zeroclaw_mmmu_pro.yaml \
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
