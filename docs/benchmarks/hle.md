# HLE

HLE evaluates multiple-choice Humanity's Last Exam tasks from `cais/hle`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
```

`cais/hle` is gated on HuggingFace. On a fresh machine, export `HF_TOKEN` before running:

```bash
export HF_TOKEN=hf_...
```

If the dataset is already cached locally, the loader can run without forcing `HF_TOKEN`.
You can also point `benchmark.config.dataset` at a complete local Hugging Face
snapshot directory; the HLE loader has been smoke-tested on that path with the
OpenCode Docker controller.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | macro config available | `configs/macro_runs/hle_directllm_qwen35_27b.yaml` |
| `opencode` | macro config available | `configs/macro_runs/hle_opencode_qwen35_27b.yaml` |
| `openclaw` | macro config available | `configs/macro_runs/hle_openclaw_qwen35_27b.yaml` |
| `zeroclaw` | macro config available | `configs/macro_runs/hle_zeroclaw_qwen35_27b.yaml` |

The checked-in configs exercise the HLE `multipleChoice` subset. Other HLE
answer types are not included in the current exact-match scoring path.

As of April 22, 2026, the loader also treats `image: ""` rows in the current
`cais/hle` snapshot as ordinary no-attachment questions. Earlier full-run
attempts on the same snapshot could fail early with
`HLE: unsupported image type ... str` before this compatibility fix landed.

## Full Runs

The macro configs select the full `multipleChoice` subset. Add
`-o benchmark.config.max_tasks=1` for a first smoke, then remove the override
only after reviewing the model, output, timeout, and concurrency contract.

## DirectLLM

```bash
python -m alphadiana.cli run configs/macro_runs/hle_directllm_qwen35_27b.yaml \
  -o run_id=hle_directllm_smoke -o benchmark.config.max_tasks=1
```

On current main, `direct_llm` captures logprobs by default. For a local-vLLM
Qwen run, use `configs/macro_runs/hle_directllm_qwen35_27b.yaml` and add a
bounded selection before the first smoke.

## OpenCode

Build the controller image once before using the checked-in OpenCode configs:

```bash
docker build --network host \
  -f alphadiana/benchmarks/terminal_bench2/deploy/dockerfiles/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
```

```bash
python -m alphadiana.cli run configs/macro_runs/hle_opencode_qwen35_27b.yaml \
  -o run_id=hle_opencode_smoke -o benchmark.config.max_tasks=1
```

The checked-in OpenCode benchmark configs now use Docker controller isolation
by default. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

The macro config keeps `timeout: 1800` to allow visible model output before
scoring. The controller image build and caveats are documented in
the [OpenCode harness guide](../harnesses/opencode.md).

As of April 22, 2026, current main also stops treating OpenCode provider error
bodies as normal HLE answers on `qwen3vl`. Historical April 22 artifacts such
as `fixproof_before_20260422_hle_opencode_qwen3vl_t1/tasks/hle_1.json`, which
recorded `predicted="400"` from a tool-choice failure body, are pre-fix audit
evidence only. The replacement run
`fixproof_after_20260422_hle_opencode_qwen3vl_t1` records the same class of
failure as `score_status=provider_error` with `predicted=null`.

Local Qwen/OpenCode logprob evidence on April 25, 2026:
`smoke_20260425_hle_opencode_qwen35_local_snapshot_t1` used a local
`cais/hle` snapshot path, Docker-controller OpenCode,
`Qwen/Qwen3.5-27B` at `http://127.0.0.1:8011/v1`, `streaming=true`,
`max_tokens=25000`, `capture_logprobs=true`, and `top_logprobs=20`. It wrote a
normal `hle_1` task record with `score_status=valid_scored`, `score=1.0`,
`predicted=D`, and matching float/int16 logprob sidecars with `1538` lines
each. A follow-up image-backed smoke,
`smoke_20260425_hle53_opencode_qwen35_attachment_artifact_fix_t1`, completed
`hle_53` with `metadata.num_attachments=1`,
`metadata.logprobs_capture_status=captured`, matching float/int16 sidecars with
`512` lines each, and preserved the attachment bytes as
`artifacts/hle_53/workspace/attachments/image_1.png.base64`.

## OpenClaw

```bash
python -m alphadiana.cli run configs/macro_runs/hle_openclaw_qwen35_27b.yaml \
  -o run_id=hle_openclaw_smoke
```

OpenClaw HLE responses can take several minutes after the gateway returns HTTP 200. Wait for artifact collection and result writing before classifying the run as stuck.

Current OpenRouter free-VLM evidence on April 22, 2026:
`smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_hle_openclaw_img53_t1_r1`
used the real image-backed row `hle_53`, logged one initial empty-body
`http proxy failed`, retried automatically, and then wrote a normal scored task
record.

Early same-day full-run caveat on the same provider:
`full_20260422_openrouter_nemotron_nano_12b_v2_vl_hle_openclaw_r1` is
advancing, but several early text-only rows preserved
`metadata.partial_reasoning_only=true` or other free-form predictions instead
of a clean option letter. Treat that as current model/harness quality drift,
not as a silent-runner failure.

## ZeroClaw

ZeroClaw now consumes HLE attachments by writing them into the workspace under
`attachments/` and mentioning them in the task prompt.

Unlike the AIME quickstart in the main `README.md`, the formal benchmark smoke here is counted only when the task executes inside a ROCK sandbox. Do not clear `agent.config.rock_image` for the benchmark smoke.

Start ROCK first:

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

If another branch is already using ROCK, edit `scripts/.rock_ports.env` before startup so this worktree gets isolated admin/proxy/redis/ray ports.

```bash
python -m alphadiana.cli run configs/macro_runs/hle_zeroclaw_qwen35_27b.yaml \
  -o run_id=hle_zeroclaw_smoke
```

On the OpenAI-compatible `qwen3vl` endpoint, multimodal ZeroClaw currently works most
reliably through the same single sandbox CLI path now used for text benchmarks
on current main. No extra override is required: when a live ROCK sandbox is
present and the HLE row has an image attachment, AlphaDiana uploads the image
into the sandbox workspace, appends an
`[IMAGE:<absolute sandbox path>]` marker to the prompt, and runs the stock
`zeroclaw agent` CLI there. The transport marker is now
`metadata.transport=zeroclaw_cli_sandbox`.

Current transport proof:
`smoke_20260422_zeroclaw_cli_sandbox_hle53_nemotron_vl_t1` used `hle_53` on
`nvidia/nemotron-nano-12b-v2-vl:free` and wrote `tasks/hle_53.json` with
`metadata.transport=zeroclaw_cli_sandbox`, the preserved
`[IMAGE:/.alphadiana_zeroclaw/.../workspace/attachments/image_1.png]` prompt
marker, normal sandbox metadata, attachment artifacts, and a preserved
in-sandbox OpenRouter `429` failure record. Treat that run as execution-path
evidence, not a quality claim.

Historical same-day `disable_tools=true` runs such as
`smoke_20260422_hle_zeroclaw_qwen3vl_disable_tools_t1` and
`smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_hle_zeroclaw_disable_tools_img53_t1_r1`
remain useful audit evidence for the old workaround, but they are no longer
the recommended path for current main.

When ZeroClaw still fails on current main, the task JSON now preserves an
explicit `metadata.failure_reason` such as `empty_response` or
`provider_error` instead of dropping the failure into an opaque runtime error.
The real-API before/after evidence is
`fixproof_before_20260422_hle_zeroclaw_qwen3vl_t1` versus
`fixproof_after_20260422_hle_zeroclaw_qwen3vl_t1`.

## Smoke Selection

The checked-in configs select the full `multipleChoice` subset. Add
`-o benchmark.config.max_tasks=1` for a smoke. The scorer is `exact_match`, so
the final answer should be one of the multiple-choice options.

## Historical Qwen/OpenRouter 3-Task Pilot

Environment:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_ENDPOINT=https://hf-mirror.com
export HF_TOKEN=hf_...
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
export OPENAI_API_KEY=sk-...
```

Command:

```bash
python -m alphadiana.cli run configs/macro_runs/hle_directllm_qwen35_27b.yaml
python -m alphadiana.cli run configs/macro_runs/hle_openclaw_qwen35_27b.yaml
python -m alphadiana.cli run configs/macro_runs/hle_opencode_qwen35_27b.yaml
```

Config note:

- The legacy `HLE x opencode` pilot used the first three scoreable
  `multipleChoice` rows: `hle_1`, `hle_11`, and `hle_13`.
- In the current `cais/hle` snapshot those three rows expose `image: ""`, so
  they are not valid image-backed probes even though the benchmark is
  multimodal in general.
- Historical `direct_llm` and `openclaw` pilot configs pinned
  `dataset_indices: [53, 98, 111]`, which do carry real image payloads and
  preserve `task.attachments.image_1`.

### OpenRouter Free-VLM Smoke (2026-04-22)

Current accepted image-backed OpenRouter smoke uses:

```bash
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=nvidia/nemotron-nano-12b-v2-vl:free
```

The accepted run IDs on the real image-backed `hle_53` row are:

- `smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_hle_direct_llm_img53_t1_r1`
- `smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_hle_opencode_img53_t1_r1`
- `smoke_20260422_openrouter_nemotron_nano_12b_v2_vl_hle_openclaw_img53_t1_r1`
- `smoke_20260422_zeroclaw_cli_sandbox_hle53_nemotron_vl_t1` for the current
  native ZeroClaw execution-path proof

Observed current behavior:

- `direct_llm` and `opencode` both preserved the image-backed request path.
- `openclaw` wrote a normal scored task after recovering from one initial
  empty-body `http proxy failed`.
- `zeroclaw` now uses the same native sandbox CLI path for both text and
  image-backed HLE rows. The current execution proof is
  `smoke_20260422_zeroclaw_cli_sandbox_hle53_nemotron_vl_t1`, which wrote
  `metadata.transport=zeroclaw_cli_sandbox` and preserved the
  `[IMAGE:<absolute sandbox path>]` marker in the prompt. Earlier
  `disable_tools=true` runs remain historical workaround evidence only.

Observed on April 19/20, 2026:

- `direct_llm`:
  - April 20 image-backed pilot:
    `pilot_20260420_qwen35_27b_hle_directllm_t3_multimodal_r1`
    wrote `3/3` normal task records on `hle_53`, `hle_98`, and `hle_111`
    with scores `0/0/1`
  - all three task artifacts preserved `error=None`
  - every task `request_messages.json` contained one text block plus one
    `image_url` block

- `openclaw`:
  - April 20 image-backed pilot:
    `pilot_20260420_qwen35_27b_hle_openclaw_t3_multimodal_r1`
    wrote `3/3` normal task records on `hle_53`, `hle_98`, and `hle_111`
    with scores `0/0/0`
  - all three task artifacts preserved `error=None`
  - every task `request_messages.json` contained one text block plus one
    `image_url` block
  - the run log recorded normal SSE completion on all three OpenClaw requests
    before artifact collection

- `opencode`:
  - April 19 uploaded host-mode pilot:
    `pilot_20260419_qwen35_27b_hle_opencode_t3`
    wrote `3/3` normal task records on `hle_1`, `hle_11`, and `hle_13`
    with scores `0/0/1`
  - April 20 default-Docker confirmation rerun:
    `pilot_20260420_qwen35_27b_hle_opencode_t3_docker_default`
    wrote `3/3` normal task records on the same task trio with scores `1/0/0`
