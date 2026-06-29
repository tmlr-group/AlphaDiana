# HLE

HLE evaluates multiple-choice Humanity's Last Exam tasks from `cais/hle`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
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
| `direct_llm` | supported | `configs/full_runs/p25_full_directllm_minimax_hle.yaml` |
| `opencode` | supported | `configs/full_runs/p25_full_opencode_minimax_hle.yaml` |
| `openclaw` | supported | `configs/full_runs/p25_full_openclaw_minimax_hle.yaml` |
| `zeroclaw` | supported | `configs/full_runs/p25_full_zeroclaw_minimax_hle.yaml` |

The full configs run the supported HLE `multipleChoice` subset. Other HLE answer types are not included in the current exact-match scoring path.

The loader treats `image: ""` rows in the `cais/hle` snapshot as ordinary
no-attachment questions.

The corresponding smoke configs remain under `configs/examples/` and pin `dataset_index: 1`, `max_tasks: 1`.

Current dataset caveat:
the checked-in smoke row `hle_1` is text-only in the current `cais/hle`
snapshot. For a real image-backed transport probe, override
`-o benchmark.config.dataset_index=53`.

Pilot configs that drop the smoke `dataset_index: 1` pin (so they load three
distinct `multipleChoice` tasks) are also checked in:

- `configs/examples/directllm_qwen35_27b_hle_pilot.yaml`
- `configs/examples/openclaw_qwen35_27b_hle_pilot.yaml`
- `configs/examples/opencode_qwen35_27b_hle_pilot.yaml`

## Full Runs

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_zeroclaw_minimax_hle.yaml --redo-all
```

For the local-vLLM `Qwen/Qwen3.5-27B` path when you need per-token logprobs,
use:

```bash
python -m alphadiana.cli run configs/full_runs/phase9_directllm_qwen35_27b_hle_logprobs.yaml --redo-all
```

## DirectLLM

```bash
python -m alphadiana.cli run configs/examples/directllm_minimax_hle.yaml \
  -o run_id=hle_directllm_smoke
```

On current main, `direct_llm` captures logprobs by default. The dedicated
`configs/full_runs/phase9_directllm_qwen35_27b_hle_logprobs.yaml` entrypoint is
still the preferred local-vLLM Qwen path because it pins the model/api-base and
the heavier `max_concurrent: 15` benchmark run contract in one checked-in file.

## OpenCode

Build the controller image once before using the checked-in OpenCode configs:

```bash
docker build --network host \
  -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
```

```bash
python -m alphadiana.cli run configs/examples/opencode_minimax_hle.yaml \
  -o run_id=hle_opencode_smoke
```

The checked-in OpenCode benchmark configs now use Docker controller isolation
by default. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

The smoke config keeps `timeout: 1800` to allow visible model output before
scoring. The controller image build and caveats are documented in
`docs/opencode-docker-isolation.md`.

OpenCode provider error bodies are not treated as normal HLE answers on
`qwen3vl`; such failures are recorded as `score_status=provider_error` with
`predicted=null`.

## OpenClaw

```bash
python -m alphadiana.cli run configs/examples/openclaw_minimax_hle.yaml \
  -o run_id=hle_openclaw_smoke
```

OpenClaw HLE responses can take several minutes after the gateway returns HTTP 200. Wait for artifact collection and result writing before classifying the run as stuck.

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
python -m alphadiana.cli run configs/examples/zeroclaw_hle.yaml \
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

## Smoke Selection

The checked-in minimax smoke configs pin:

- `dataset_index: 1`
- `answer_types: ["multipleChoice"]`
- `max_tasks: 1`

The scorer is `exact_match`, so the final answer should be one of the multiple-choice options.

Use the `configs/full_runs/` files for full supported HLE multiple-choice evaluations.
