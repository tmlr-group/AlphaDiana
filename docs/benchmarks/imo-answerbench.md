# IMO-AnswerBench

IMO-AnswerBench evaluates mathematical-answer extraction and scoring on `Hwilner/imo-answerbench`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

The benchmark loads from HuggingFace. If the default mirror is slow, set `HF_ENDPOINT` before running.

If the host cannot reach `huggingface.co` directly, set:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | supported | `configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml` |
| `opencode` | supported | `configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml` |
| `openclaw` | supported | `configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml` |
| `zeroclaw` | supported | `configs/full_runs/p25_full_zeroclaw_minimax_imo_answerbench.yaml` |

The corresponding smoke configs remain under `configs/examples/` and pin `dataset_index: 367`, `max_tasks: 1`.

Additional pilot configs are also checked in:

- `configs/examples/directllm_qwen35_27b_imo_answerbench_pilot.yaml`
- `configs/examples/openclaw_qwen35_27b_imo_answerbench_pilot.yaml`
- `configs/examples/opencode_qwen35_27b_imo_answerbench_pilot.yaml`

These pilot configs use `max_tasks: 3` and the OpenRouter slug
`qwen/qwen3.5-27b` for the logical model target `Qwen/Qwen3.5-27B`.
The dedicated `opencode` pilot config intentionally omits the smoke
`dataset_index: 367` pin so it can load three distinct tasks.

## Full Runs

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_zeroclaw_minimax_imo_answerbench.yaml --redo-all
```

For the local-vLLM `Qwen/Qwen3.5-27B` path when you need per-token logprobs,
use:

```bash
python -m alphadiana.cli run configs/full_runs/phase9_directllm_qwen35_27b_imo_answerbench_logprobs.yaml --redo-all
```

## DirectLLM

```bash
python -m alphadiana.cli run configs/examples/directllm_minimax_imo_answerbench.yaml \
  -o run_id=imo_directllm_smoke
```

Local-vLLM Qwen example with the rollout-plan `temperature=0.0` semantics:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_ENDPOINT=https://hf-mirror.com
export QWEN_VLLM_API_BASE=http://127.0.0.1:8011/v1
export QWEN_VLLM_API_KEY=EMPTY

python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml \
  -o run_id=full_run \
  -o output_dir=./results/full_run \
  -o max_concurrent=20 \
  -o agent.config.model='Qwen/Qwen3.5-27B' \
  -o agent.config.api_base="$QWEN_VLLM_API_BASE" \
  -o agent.config.api_key="$QWEN_VLLM_API_KEY" \
  -o agent.config.temperature=0.0 \
  -o agent.config.top_p=0.95 \
  -o agent.config.max_tokens=32768 \
  -o agent.config.stream=true
```

This command intentionally differs from the checked-in rollout manifest only on
`max_concurrent`: the local follow-up kept the user-requested `20`, while the
manifest template for this path still defaults to `10`.

On current main, `direct_llm` captures logprobs by default. The dedicated
`phase9_directllm_qwen35_27b_imo_answerbench_logprobs.yaml` config is still the
preferred local-vLLM Qwen path because it checks in the intended model/api-base
contract and `max_concurrent: 10` for the heavier logprob run.

## OpenCode

OpenCode runs the `opencode` CLI. The checked-in minimax smoke config includes
OpenCode-specific external agent settings (`agent: lean-math` and
`agent_md_path: context/opencode_lean_math.md`) in addition to
`agent.config.system_prompt`. When comparing harness prompts or running local
Qwen logprob smoke without that external agent layer, override both fields to
empty strings.

Build the controller image once before using the checked-in OpenCode configs:

```bash
docker build --network host \
  -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
```

```bash
python -m alphadiana.cli run configs/examples/opencode_minimax_imo_answerbench.yaml \
  -o run_id=imo_opencode_smoke
```

Local-vLLM Qwen logprob smoke with the external agent disabled:

```bash
python -m alphadiana.cli run configs/examples/opencode_minimax_imo_answerbench.yaml \
  --redo-all \
  -o run_id=phase11_opencode_imo_answerbench_qwen35_27b_logprobs_smoke \
  -o output_dir=./results \
  -o agent.config.model=custom/Qwen/Qwen3.5-27B \
  -o agent.config.model_name=Qwen/Qwen3.5-27B \
  -o agent.config.api_base=http://127.0.0.1:8011/v1 \
  -o agent.config.api_key=EMPTY \
  -o agent.config.controller_mode=docker \
  -o agent.config.controller_network=host \
  -o agent.config.capture_logprobs=true \
  -o agent.config.top_logprobs=20 \
  -o agent.config.agent= \
  -o agent.config.agent_md_path=
```

The checked-in OpenCode benchmark configs now use Docker controller isolation
by default. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

The smoke config uses `timeout: 1800` because shorter bounds can kill valid
slow model output before it reaches scoring. The full Docker setup and
reproduction guide live in `docs/opencode-docker-isolation.md`.

## OpenClaw

OpenClaw uses ROCK auto-deploy and the gateway config in `alphadiana/harness/openclaw/deploy/`.
Benchmark fairness now requires a fresh ROCK sandbox session per task for
`openclaw`; do not treat older sequential runs that reused one shared session
across tasks as comparable evidence. Current main also skips the OpenClaw
chat-completions warmup by default on benchmark runs because that warmup could
pollute the first task with a leftover `READY`/bootstrap response.

```bash
python -m alphadiana.cli run configs/examples/openclaw_minimax_imo_answerbench.yaml \
  -o run_id=imo_openclaw_smoke
```

ROCK services must be healthy before this run. `scripts/activate.sh` loads the local ROCK port configuration.

## ZeroClaw

ZeroClaw uses the same ROCK auto-deploy path as the PR23 AIME integration.

Unlike the AIME quickstart in the main `README.md`, the formal benchmark smoke here is counted only when the task executes inside a ROCK sandbox. Do not clear `agent.config.rock_image` for the benchmark smoke.

Start ROCK first:

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

If another branch is already using ROCK, edit `scripts/.rock_ports.env` before startup so this worktree gets isolated admin/proxy/redis/ray ports.

```bash
python -m alphadiana.cli run configs/examples/zeroclaw_imo_answerbench.yaml \
  -o run_id=imo_zeroclaw_smoke
```

## Smoke Selection

The checked-in minimax smoke configs pin `dataset_index: 367` and `max_tasks: 1` so the run stays deterministic and bounded.

Use the `configs/full_runs/` files for full evaluations.
