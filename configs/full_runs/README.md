# Full-Run Configs

Production experiment configs. Each file is a single benchmark × harness combination
with a pinned model and all hyperparameters explicit.

See [`../PROMPTS.md`](../PROMPTS.md) for the canonical system prompt for every
benchmark × harness type. **Every config must set `system_prompt` explicitly** — never
rely on harness-hardcoded defaults.

Smoke/pilot configs live in `configs/examples/` (they pin `max_tasks: 1–5`). Do not
use those for full benchmark runs.

---

## Naming convention

```
{benchmark}_{harness}_{model_short}_logprobs.yaml
```

- `benchmark`: `gpqa`, `aime`, `imo`, `hle`, `mmmu_pro`, `tb2`, `swe`
- `harness`: `directllm`, `openclaw`, `opencode`, `zeroclaw`
- `model_short`: e.g. `qwen35_27b`, `minimax`
- `_logprobs` suffix: run captures token-level top-20 int16 logprob sidecars

---

## File inventory

### Phase 12 — GPQA-Diamond × Qwen3.5-27B (local vLLM, 2026-04-25)

| File | Harness | Notes |
|---|---|---|
| `full_gpqa_openclaw_qwen35_27b_logprobs.yaml` | OpenClaw | partial run; set `redo_all: true` to rerun with correct prompt |
| `full_gpqa_opencode_qwen35_27b_logprobs.yaml` | OpenCode | 198/198 done with wrong prompt; set `redo_all: true` to rerun |
| `full_gpqa_zeroclaw_qwen35_27b_logprobs.yaml` | ZeroClaw | partial run; `redo_all: true` already set |

> All three were missing `system_prompt` in the original run. Fixed 2026-04-25.

### Phase 25 — MiniMax (cloud)

| File | Benchmark | Harness |
|---|---|---|
| `p25_full_directllm_minimax_imo_answerbench.yaml` | IMO | DirectLLM |
| `p25_full_openclaw_minimax_imo_answerbench.yaml` | IMO | OpenClaw |
| `p25_full_opencode_minimax_imo_answerbench.yaml` | IMO | OpenCode |
| `p25_full_zeroclaw_minimax_imo_answerbench.yaml` | IMO | ZeroClaw |
| `p25_full_directllm_minimax_hle.yaml` | HLE | DirectLLM |
| `p25_full_openclaw_minimax_hle.yaml` | HLE | OpenClaw |
| `p25_full_opencode_minimax_hle.yaml` | HLE | OpenCode |
| `p25_full_zeroclaw_minimax_hle.yaml` | HLE | ZeroClaw |
| `p25_full_terminal_bench2_{harness}_minimax.yaml` | TB2 | all 4 harnesses |

### Phase 29 — SWE-Bench Pro

`p29_full_{openclaw,opencode,zeroclaw}_swebench_pro.yaml`

### Rollout — multi-model campaigns

| File | Benchmark | Harness |
|---|---|---|
| `rollout_full_{directllm,openclaw,opencode,zeroclaw}_gpqa_diamond.yaml` | GPQA | all 4 |
| `rollout_full_{directllm,openclaw,opencode,zeroclaw}_mmmu_pro_vision.yaml` | MMMU-Pro | all 4 |
| `rollout_local_vllm_campaign_20260419.yaml` | multi | multi |

### Phase 9–11 — Historical (Qwen3.5-27B logprob dev)

`phase{9,10,11}_{harness}_{benchmark}_qwen35_27b_logprobs[_smoke].yaml`

---

## Key config fields

```yaml
run_id: "unique_identifier"           # used as results/ subdirectory name

agent:
  name: openclaw | opencode | zeroclaw | direct_llm
  version: "..."
  config:
    system_prompt: |                  # REQUIRED — see ../PROMPTS.md
      ...
    capture_logprobs: true            # enables top-20 int16 logprob sidecar files
    top_logprobs: 20
    docker_host_ip: "127.0.0.1"   # required for logprob proxy (openclaw/zeroclaw)

max_concurrent: 1                     # tasks in parallel (1 = sequential)
redo_all: true                        # set when intentionally re-running completed tasks
```

## Local-vLLM Campaign

The staged April 19, 2026 full-rollout assets for the active
`5 benchmarks x 4 harnesses x 3 models` local-vLLM matrix live here:

- manifest:
  `configs/full_runs/rollout_local_vllm_campaign_20260419.yaml`
- env template:
  `configs/full_runs/rollout_local_vllm_campaign_20260419.env.example`
- new full configs for `GPQA-Diamond`:
  `rollout_full_{directllm,openclaw,opencode,zeroclaw}_gpqa_diamond.yaml`
- new full configs for `MMMU-Pro`:
  `rollout_full_{directllm,openclaw,opencode,zeroclaw}_mmmu_pro_vision.yaml`
- logprobs-enabled local-vLLM DirectLLM full configs for targeted analysis:
  `phase9_directllm_qwen35_27b_{gpqa_diamond,imo_answerbench,hle}_logprobs.yaml`

Use the rollout helper instead of hand-expanding commands:

```bash
python scripts/benchmark_rollout.py summary
python scripts/benchmark_rollout.py preflight --check-docker --check-rock --probe-vllm
python scripts/benchmark_rollout.py commands --wave wave_a_mainline
python scripts/benchmark_rollout.py materialize --wave wave_a_mainline
```

For the internal cross-machine bring-up plan and official-checkout readiness
notes, see:

- `context/benchmark-rollout-full-plan-20260419.md`

Required environment contract for the campaign:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export QWEN_VLLM_API_BASE=http://127.0.0.1:8001/v1
export QWEN_VLLM_API_KEY=EMPTY
export GEMMA4_VLLM_API_BASE=http://127.0.0.1:8002/v1
export GEMMA4_VLLM_API_KEY=EMPTY
export NEMOTRON_VLLM_API_BASE=http://127.0.0.1:8003/v1
export NEMOTRON_VLLM_API_KEY=EMPTY

export DIRECTLLM_ROOT=/path/to/directllm
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench/tasks
export HF_TOKEN=hf_...
```

Campaign rules:

- The helper expands `60` concrete runs and keeps the official `terminal-bench-2` `direct_llm` path in the manifest.
- `SWE-bench Pro` is intentionally excluded from the current rollout manifest while scorer/evaluator health is fixed separately.
- Generated run commands do not add `--redo-all`; AlphaDiana resumes from checkpoint by default.
- Raw shell logs stay under this repo's `logs/` even for official-checkout runs.
- `MMMU-Pro` full configs intentionally use `data_config: "vision"`.
- Checked-in full-run configs now set `strict_report: true`; the AlphaDiana-backed full configs also set `strict_isolation: true`.
- The checked-in rollout configs now align to the frozen
  `context/benchmark-rollout-full-plan-20260419.md` semantics where the
  runtime actually exposes them:
  `temperature=0.0`, `top_p=0.95`, `max_tokens=32768`, `streaming=true`,
  `timeout=1800`.
- Keep the plan's published gap list in mind when reading that line:
  `thinking` is not uniformly forwarded, and `zeroclaw` does not uniformly expose
  `top_p` / `max_tokens` / `streaming`.

Wave summary from the checked-in manifest:

- `wave_a_mainline`: `46`
- `wave_b_official`: `3`
- `wave_c_high_risk`: `8`
- `wave_d_blocked`: `3`

## SWE-bench Pro

These PR29 configs remain checked in for targeted follow-up work, but they are
not part of the current `rollout_local_vllm_campaign_20260419.yaml` manifest.
Keep them out of any "full campaign" summary until SWE-bench Pro scorer /
evaluator health is revisited.

These are the PR29 full-run configs for the Diana-backed SWE-bench Pro paths:

| Mode | Full-run config |
|---|---|
| `openclaw` | `p29_full_openclaw_swebench_pro.yaml` |
| `opencode` | `p29_full_opencode_swebench_pro.yaml` |
| `zeroclaw` | `p29_full_zeroclaw_swebench_pro.yaml` |

`directLLM` is intentionally not included here. For the direct-LLM SWE-bench Pro baseline, use the official repository `scaleapi/SWE-bench_Pro-os`, not Diana.

Common setup:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...

export SWE_BENCH_PRO_EVAL_SCRIPT=/abs/path/to/SWE-bench_Pro-os/swe_bench_pro_eval.py
export SWE_BENCH_PRO_SCRIPTS_DIR=/abs/path/to/SWE-bench_Pro-os/run_scripts

export OPENCLAW_FULL_MODEL_NAME=minimax-m2.5
export OPENCLAW_FULL_MODEL_CANDIDATES=minimax-m2.5,minimax
export OPENCLAW_AGENT_ID=main
export OPENCLAW_TOOLS_PROFILE=coding
export OPENCLAW_PROMPT_PROFILE=edit_first
export OPENCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000
export OPENCLAW_REQUIRE_PATCH=1
export OPENCLAW_MAX_TOOL_CALLS_WITHOUT_EDIT=12
export OPENCLAW_MAX_NO_EDIT_SECONDS=180
export OPENCLAW_CONTEXT_WINDOW=128000
export OPENCLAW_COMPLETION_MAX_TOKENS=32768

export OPENCODE_FULL_MODEL_NAME=minimax
export OPENCODE_FULL_MODEL_CANDIDATES=minimax
export OPENCODE_STRATEGY_SEQUENCE=guided_edit_first
export OPENCODE_REQUIRE_PATCH=0
export OPENCODE_PROMPT_PROFILE=edit_first
export OPENCODE_AUTO_TARGET_HINTS=0
export OPENCODE_TARGET_FILE_HINTS=src/database/redis/main.js,src/database/mongo/main.js,src/database/postgres/main.js,src/user/email.js
export OPENCODE_PRIMARY_TARGET_FILE=src/database/redis/main.js
export OPENCODE_PROBLEM_STATEMENT_MAX_CHARS=12000
export OPENCODE_PREFLIGHT_TIMEOUT_SEC=45
export OPENCODE_STARTUP_TIMEOUT_SEC=180
export OPENCODE_IDLE_TIMEOUT_SEC=900
export OPENCODE_IDLE_POLL_SEC=15
export OPENCODE_MAX_ACTIVE_NO_EDIT_SEC=300
export OPENCODE_MAX_TOOL_CALLS_WITHOUT_EDIT=24
export OPENCODE_ACTIVITY_HEARTBEAT_SEC=30

export ZEROCLAW_FULL_MODEL_NAME=minimax-m2.5
export ZEROCLAW_FULL_MODEL_CANDIDATES=minimax-m2.5,minimax
export ZEROCLAW_TIMEOUT_SEC=1800
export ZEROCLAW_REQUIRE_PATCH=0
export ZEROCLAW_PROMPT_PROFILE=edit_first
export ZEROCLAW_PROBLEM_STATEMENT_MAX_CHARS=12000
export ZEROCLAW_WORKSPACE_ONLY=0
export ZEROCLAW_MAX_TOOL_ITERATIONS=100
export ZEROCLAW_MAX_ACTIONS_PER_HOUR=200
export ZEROCLAW_RUNTIME_TRACE_MODE=none
```

Validate before running:

```bash
python -m alphadiana.cli validate configs/full_runs/p29_full_openclaw_swebench_pro.yaml
python -m alphadiana.cli validate configs/full_runs/p29_full_opencode_swebench_pro.yaml
python -m alphadiana.cli validate configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml
```

Run commands:

```bash
python -m alphadiana.cli run configs/full_runs/p29_full_openclaw_swebench_pro.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p29_full_opencode_swebench_pro.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p29_full_zeroclaw_swebench_pro.yaml --redo-all
```

Scope notes:

- All three configs use `benchmark.name: swebench_pro_os`, `split: test`, `subset: all`.
- `openclaw` full runs keep `OPENCLAW_REQUIRE_PATCH=1` because that path already returned non-empty smoke patches in local validation.
- `opencode` full runs default to `OPENCODE_REQUIRE_PATCH=0`, matching the smoke playbook contract where execution success is determined by task JSON and dashboard state rather than forcing a patch on every task.
- `zeroclaw` full runs default to `ZEROCLAW_REQUIRE_PATCH=0` for the same reason: task JSON and dashboard state remain the primary full-run completion signal.
- If Docker Hub cannot serve `tmlrgroup/alphadiana:opencode`, set `SWEBENCH_OPENCODE_RUNTIME_IMAGE` before the OpenCode run.
- If the default ZeroClaw runtime image is unavailable locally, set `SWEBENCH_ZEROCLAW_RUNTIME_IMAGE` before the ZeroClaw run.

## Legacy PR25 Matrix

These configs are the ready-to-run full benchmark entry points for the nine PR25 combinations that passed strict smoke validation.
All checked-in PR25 full configs now set `strict_report: true` and `strict_isolation: true`.

## Supported Matrix

| Mode | IMO-AnswerBench | HLE | terminal-bench-2 |
|---|---|---|---|
| `direct_llm` | `p25_full_directllm_minimax_imo_answerbench.yaml` | `p25_full_directllm_minimax_hle.yaml` | `p25_full_terminal_bench2_directllm_minimax.yaml` |
| `opencode` | `p25_full_opencode_minimax_imo_answerbench.yaml` | `p25_full_opencode_minimax_hle.yaml` | `p25_full_terminal_bench2_opencode_minimax.yaml` |
| `openclaw` | `p25_full_openclaw_minimax_imo_answerbench.yaml` | `p25_full_openclaw_minimax_hle.yaml` | `p25_full_terminal_bench2_openclaw_minimax.yaml` |

## Common Setup

Run from the repo root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

HLE uses the gated `cais/hle` dataset. On a fresh machine:

```bash
export HF_TOKEN=hf_...
```

terminal-bench-2 needs a local task checkout:

```bash
git clone --depth=1 https://github.com/laude-institute/terminal-bench.git /tmp/terminal-bench
export TERMINAL_BENCH2_DIR=/tmp/terminal-bench/tasks
```

Pre-pull terminal-bench-2 task images before full runs:

```bash
python - <<'PY' | sort -u | xargs -r -n1 docker pull
import os, tomllib
from pathlib import Path

root = Path(os.environ["TERMINAL_BENCH2_DIR"])
for task_toml in root.glob("*/task.toml"):
    with task_toml.open("rb") as f:
        data = tomllib.load(f)
    image = data.get("environment", {}).get("docker_image")
    if image:
        print(image)
PY
```

Prepare the native-agent runtime source images before terminal-bench-2 full runs:

```bash
docker pull tmlrgroup/alphadiana:v1
docker image inspect alphadiana/tb2-opencode-controller:latest >/dev/null
docker pull zeroclaw-reasoning:0.6.9
```

## Run Commands

Validate first:

```bash
for cfg in configs/full_runs/p25_full_*.yaml; do
  python -m alphadiana.cli validate "$cfg" || exit 1
done
```

Run sequentially unless the machine has enough local Docker and ROCK capacity:

```bash
python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_imo_answerbench.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_imo_answerbench.yaml --redo-all

python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_opencode_minimax_hle.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_openclaw_minimax_hle.yaml --redo-all

python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_directllm_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_opencode_minimax.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/p25_full_terminal_bench2_openclaw_minimax.yaml --redo-all
```

When you want the checked-in local-vLLM `Qwen/Qwen3.5-27B` DirectLLM path with
the intended benchmark-specific concurrency already pinned, use the dedicated
configs below. On current main, `direct_llm` logprob capture is enabled by
default; these configs remain useful because they freeze the local-vLLM Qwen
model/api-base entrypoint and the target `max_concurrent` values for the
logprob-heavy runs:

```bash
python -m alphadiana.cli run configs/full_runs/phase9_directllm_qwen35_27b_imo_answerbench_logprobs.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/phase9_directllm_qwen35_27b_hle_logprobs.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs.yaml --redo-all
```

For 1-task GPQA-Diamond smoke coverage of top-20 logprob storage across the
agent harnesses, use the Phase 11 configs:

```bash
python -m alphadiana.cli run configs/full_runs/phase11_opencode_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml --redo-all
python -m alphadiana.cli run configs/full_runs/phase11_zeroclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml --redo-all
```

On ROCK-backed OpenClaw/ZeroClaw local-vLLM smokes, `capture_logprobs: true`
starts a host-side OpenAI-compatible capture proxy. The proxy forwards to the
configured provider, injects `logprobs=true` and `top_logprobs`, and advertises a
sandbox-reachable URL to the runtime. The sandbox uses a random per-proxy bearer
token, while the proxy forwards upstream with the configured provider key.
Validated local run evidence is in
[`context/phase12-harness-logprob-smokes/run_evidence.md`](../../context/phase12-harness-logprob-smokes/run_evidence.md).

## Scope Notes

- IMO-AnswerBench configs run the full configured `train` split.
- HLE configs run the full supported `multipleChoice` subset. Other answer types are not included in the current exact-match scoring path.
- terminal-bench-2 configs scan all local task directories under `TERMINAL_BENCH2_DIR`.
- DirectLLM configs default to `max_concurrent: 20`.
- OpenCode configs default to `max_concurrent: 20` for IMO/HLE, and `2` for terminal-bench-2 because it also starts Docker containers.
- OpenClaw configs default to `max_concurrent: 1`.
