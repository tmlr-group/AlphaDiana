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

The April 22, 2026 local-vLLM Qwen follow-up on this repo required that
override before `imo_answerbench` could load at all.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | supported | `configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml` |
| `opencode` | supported | `configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml` |
| `openclaw` | supported | `configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml` |
| `zeroclaw` | supported | `configs/full_runs/p25_full_zeroclaw_minimax_imo_answerbench.yaml` |

The corresponding smoke configs remain under `configs/examples/` and pin `dataset_index: 367`, `max_tasks: 1`.

Additional April 18/19, 2026 pilot configs are also checked in:

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

Current OpenRouter free-text follow-up on April 22, 2026 uses
`nvidia/nemotron-3-nano-30b-a3b:free`.
Early full-run evidence:

- `full_20260422_openrouter_nemotron_3_nano_30b_a3b_imo_answerbench_directllm_r1`
  writes normal task JSONs, but many early answers collapse to short scalar
  outputs even on symbolic-ground-truth items
- `..._opencode_r1` is the healthiest current path and already wrote normal
  `score=1` and `score=0` task records
- `..._openclaw_r1` still preserves `predicted=null` with
  `metadata.partial_reasoning_only=true`
- `..._zeroclaw_r1` still fails the first task with
  `score_status=runtime_error` and
  `metadata.failure_reason=empty_response`

## DirectLLM

DirectLLM calls the OpenAI-compatible endpoint directly and scores the returned answer with `imo_verify`.
Checked-in IMO configs now enforce `scorer.name: imo_verify`, and config validation rejects
`benchmark.name: imo_answerbench` with any other scorer. Historical IMO runs scored with
`math_verify` should be treated as non-canonical audit artifacts, not current support evidence.
`imo_verify` is still a repo-local heuristic scorer, not an external official
verifier. The current implementation is intentionally conservative: it blocks
the old symbolic-to-numeric false positives, but it can still over-split some
comma-heavy answer forms and therefore still carries false-negative risk.
For short OpenRouter canaries on `Qwen/Qwen3.5-27B`, set
`agent.config.extra_body.reasoning.enabled=false` when you want terse outputs.
The provider otherwise emits hidden reasoning tokens even for tiny prompts,
which can dominate latency without changing the visible boxed answer. This is a
canary-only override; benchmark defaults still keep reasoning enabled unless
you explicitly change them.

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
  -o run_id=full_20260422_imo_answerbench_direct_llm_qwen35_27b_localvllm_mc20_r1 \
  -o output_dir=./results/full_20260422_imo_answerbench_direct_llm_qwen35_27b_localvllm_mc20_r1 \
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

OpenCode runs the `opencode` CLI and uses the prompt file at `context/opencode_lean_math.md` for the IMO smoke config.

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

The checked-in OpenCode benchmark configs now use Docker controller isolation
by default. If you need the old host-process path for debugging, override
`-o agent.config.controller_mode=host`.

The smoke config uses `timeout: 1800` because shorter bounds can kill valid
slow model output before it reaches scoring. The full Docker setup and
reproduction guide live in `docs/opencode-docker-isolation.md`.

## OpenClaw

OpenClaw uses ROCK auto-deploy and the gateway config in `openclaw_deploy/`.
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

Current OpenRouter free-text full-run evidence on April 22, 2026:
`full_20260422_openrouter_nemotron_3_nano_30b_a3b_imo_answerbench_openclaw_r1`
continues to reproduce the same failure shape from smoke scale-up:
normal task JSONs are written, but early tasks preserve `predicted=null` with
`metadata.partial_reasoning_only=true`.

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

Current OpenRouter free-text full-run evidence on April 22, 2026:
`full_20260422_openrouter_nemotron_3_nano_30b_a3b_imo_answerbench_zeroclaw_r1`
still fails its first task as `score_status=runtime_error` with
`metadata.failure_reason=empty_response`, even though current main preserves
the failure record cleanly.

### Reproduce The 2026-04-17 Formal Sandbox Smoke

This is the exact smoke style used for local validation of the ZeroClaw sandbox path. It intentionally forces a fast wrong answer so the run terminates quickly with dashboard `X`, which is enough for the execution-path smoke criterion.

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax

python -m alphadiana.cli run configs/examples/zeroclaw_imo_answerbench.yaml \
  -o run_id=pr26_formal_smoke_zeroclaw_imo_minimax_rock_cli_box0_20260417_v2 \
  -o benchmark.config.dataset_index=0 \
  -o agent.config.system_prompt='Smoke test mode: ignore the math problem. Do not use tools. Output exactly $$\\boxed{0}$$ and nothing else.'
```

Expected result:

- dashboard: `X`
- task file exists under `results/zeroclaw_imo_answerbench_smoke/<run_id>/tasks/`
- task JSON has no `error`
- the recorded task is `imo_answerbench_0`

Observed local verification on 2026-04-17:

- run_id: `pr26_formal_smoke_zeroclaw_imo_minimax_rock_cli_box0_20260417_v2`
- result: dashboard `X`, `predicted=0`, `ground_truth=3`, no `error`
- execution mode: ROCK sandbox + in-sandbox ZeroClaw CLI

## Smoke Selection

The checked-in minimax smoke configs pin `dataset_index: 367` and `max_tasks: 1` so the run stays deterministic and bounded.

Use the `configs/full_runs/` files for full evaluations.

## Qwen/OpenRouter 3-Task Pilot

Environment:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_ENDPOINT=https://hf-mirror.com
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_MODEL_NAME=qwen/qwen3.5-27b
export OPENAI_API_KEY=sk-...
```

Commands:

```bash
python -m alphadiana.cli run configs/examples/directllm_qwen35_27b_imo_answerbench_pilot.yaml
python -m alphadiana.cli run configs/examples/openclaw_qwen35_27b_imo_answerbench_pilot.yaml
python -m alphadiana.cli run configs/examples/opencode_qwen35_27b_imo_answerbench_pilot.yaml
```

Observed on April 18/19/20, 2026:

- `direct_llm`: `3/3` task records written, all `score=1`
- `openclaw`: not rollout-ready yet
  - `imo_answerbench_0`: `score=1`
  - `imo_answerbench_1`: task completed on the April 19 checkpoint rerun, but
    the current scorer marked a symbolic mismatch as `score=1`
  - `imo_answerbench_2`: `score=0` with `partial_reasoning_only=true`; the
    partial reasoning trace was preserved and is treated as a normal sample
  - the path remains blocked on scorer correctness, not on benchmark completion
- `opencode`:
  - April 19 uploaded quality pilot:
    `pilot_20260419_qwen35_27b_imo_answerbench_opencode_t3`
    wrote `3/3` task records, all `score=1`
  - April 20 default-Docker confirmation rerun:
    `pilot_20260420_qwen35_27b_imo_answerbench_opencode_t3_docker_default`
    wrote `3/3` normal task records with scores `1/0/0`
  - April 20 rerun中三条 task JSON 都记录了
    `metadata.controller_mode=docker` 和
    `metadata.transport=opencode_cli_container`

Local follow-up on April 19, 2026:

- `rerun_20260419_qwen35_27b_imo_answerbench_openclaw_idx2_r2`
  completed with `score=1`; the task JSON kept a non-empty top-level
  `reasoning_trajectory` plus `metadata.raw_reasoning`
- `rerun_20260419_qwen35_27b_imo_answerbench_zeroclaw_idx0_r3`
  failed cleanly with provider transport errors and `predicted=None`; the
  previous startup-log pollution no longer leaked into a fake parsed answer

Local follow-up on April 20, 2026:

- root cause for the ZeroClaw/OpenRouter failure was a config gap, not the
  benchmark itself: AlphaDiana was not writing ZeroClaw
  `provider_timeout_secs`, so the CLI fell back to its internal `120s`
  provider timeout and aborted long streamed math responses
- the fix keeps `stream=true` and writes
  `provider_timeout_secs = request_timeout` by default unless explicitly
  overridden
- validation smoke:
  `debug_20260420_qwen35_27b_imo_answerbench_zeroclaw_idx0_provider_timeout_r1`
  completed `1/1` on the previously failing first task
- repaired 3-task pilot:
  `pilot_20260420_qwen35_27b_imo_answerbench_zeroclaw_t3_repair_r3`
  completed `3/3`, all task JSONs now have non-null predictions and no
  task-level `error`, and the archive was uploaded to
  `pilot_run/pilot_20260420_qwen35_27b_imo_answerbench_zeroclaw_t3_repair_r3/`

Reviewer-facing evidence for this pilot lives in
`context/qwen-openrouter-pilots/`.
