# Full-Run Configs

Production experiment configs. Each file is a single benchmark × harness combination
with a pinned model and all hyperparameters explicit.

See [`../PROMPTS.md`](../PROMPTS.md) for the canonical system prompt for every
benchmark × harness type. **Every config must set `system_prompt` explicitly** — never
rely on harness-hardcoded defaults.

Smoke/pilot configs live in `configs/examples/` (they pin `max_tasks: 1–5`). Do not
use those for full benchmark runs.

Before launching local-Qwen logprob full runs, also read
[`context/current_eval_status.md`](../../context/current_eval_status.md) and
[`context/phase12-harness-logprob-smokes/full_run_context/README.md`](../../context/phase12-harness-logprob-smokes/full_run_context/README.md).
They record the latest OpenClaw/ZeroClaw/OpenCode support matrix, local-Qwen
timeout sizing, `tee` logging guidance, and output-cap preservation caveats.

---

## Naming convention

```
{benchmark}_{harness}_{model_short}[_logprobs][_smoke].yaml   # pinned-model production run
{benchmark}_{harness}_template.yaml                           # env-var placeholder template
```

- `benchmark`: `gpqa`, `hle`, `imo`, `mmmu_pro`, `tb2`, `swebench_pro`
- `harness`: `directllm`, `openclaw`, `opencode`, `zeroclaw`
- `model_short`: e.g. `qwen35_27b`, `minimax`
- `_logprobs` suffix: run captures token-level top-20 int16 logprob sidecars
- `_smoke` suffix: 1-task smoke validation run
- `_template`: uses `${OPENAI_MODEL_NAME}` / `${OPENAI_BASE_URL}` placeholders — fill before use

> **Note:** `run_id` inside each file is the historical identifier used as the `results/`
> subdirectory name. Renaming the file does not change existing results.

---

## File inventory

### GPQA-Diamond × Qwen3.5-27B (local vLLM)

| File | Harness | Type |
|---|---|---|
| `gpqa_directllm_qwen35_27b_logprobs.yaml` | DirectLLM | full run |
| `gpqa_openclaw_qwen35_27b_logprobs.yaml` | OpenClaw | full run |
| `gpqa_opencode_qwen35_27b_logprobs.yaml` | OpenCode | full run |
| `gpqa_zeroclaw_qwen35_27b_logprobs.yaml` | ZeroClaw | full run |
| `gpqa_directllm_qwen35_27b_logprobs_smoke.yaml` | DirectLLM | 1-task smoke |
| `gpqa_openclaw_qwen35_27b_logprobs_smoke.yaml` | OpenClaw | 1-task smoke |
| `gpqa_opencode_qwen35_27b_logprobs_smoke.yaml` | OpenCode | 1-task smoke |
| `gpqa_zeroclaw_qwen35_27b_logprobs_smoke.yaml` | ZeroClaw | 1-task smoke |
| `gpqa_directllm_qwen35_27b_logprobs_smoke_legacy.yaml` | DirectLLM | pre-int16 smoke (superseded) |

### GPQA-Diamond — env-var templates

| File | Harness |
|---|---|
| `gpqa_directllm_template.yaml` | DirectLLM |
| `gpqa_openclaw_template.yaml` | OpenClaw |
| `gpqa_opencode_template.yaml` | OpenCode |
| `gpqa_zeroclaw_template.yaml` | ZeroClaw |

### HLE

| File | Harness | Model |
|---|---|---|
| `hle_directllm_qwen35_27b_logprobs.yaml` | DirectLLM | Qwen3.5-27B |
| `hle_directllm_minimax.yaml` | DirectLLM | MiniMax |
| `hle_openclaw_minimax.yaml` | OpenClaw | MiniMax |
| `hle_opencode_minimax.yaml` | OpenCode | MiniMax |
| `hle_zeroclaw_minimax.yaml` | ZeroClaw | MiniMax |

### IMO-AnswerBench

| File | Harness | Model |
|---|---|---|
| `imo_directllm_qwen35_27b_logprobs.yaml` | DirectLLM | Qwen3.5-27B |
| `imo_directllm_minimax.yaml` | DirectLLM | MiniMax |
| `imo_openclaw_minimax.yaml` | OpenClaw | MiniMax |
| `imo_opencode_minimax.yaml` | OpenCode | MiniMax |
| `imo_zeroclaw_minimax.yaml` | ZeroClaw | MiniMax |

### MMMU-Pro — env-var templates

| File | Harness |
|---|---|
| `mmmu_pro_directllm_template.yaml` | DirectLLM |
| `mmmu_pro_openclaw_template.yaml` | OpenClaw |
| `mmmu_pro_opencode_template.yaml` | OpenCode |
| `mmmu_pro_zeroclaw_template.yaml` | ZeroClaw |

### Terminal Bench 2

| File | Harness | Model |
|---|---|---|
| `tb2_directllm_minimax.yaml` | DirectLLM | MiniMax |
| `tb2_openclaw_minimax.yaml` | OpenClaw | MiniMax |
| `tb2_opencode_minimax.yaml` | OpenCode | MiniMax |
| `tb2_zeroclaw_minimax.yaml` | ZeroClaw | MiniMax |

### SWE-Bench Pro

| File | Harness |
|---|---|
| `swebench_pro_openclaw.yaml` | OpenClaw |
| `swebench_pro_opencode.yaml` | OpenCode |
| `swebench_pro_zeroclaw.yaml` | ZeroClaw |

> DirectLLM is intentionally excluded — use the official `scaleapi/SWE-bench_Pro-os` repo for that baseline.

### Campaign orchestrators

| File | Purpose |
|---|---|
| `rollout_local_vllm_campaign_20260419.yaml` | 5-benchmark × 4-harness × 3-model local-vLLM campaign manifest |
| `rollout_local_vllm_campaign_20260419.env.example` | Env-var template for the campaign |
| `swe_verified_mini.yaml` | SWE-bench mini campaign |
| `terminal_bench_v2.yaml` | Terminal Bench 2 campaign |

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

---

## Common run commands

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

# Validate before running
python -m alphadiana.cli validate configs/full_runs/<config>.yaml

# Run (resumes from checkpoint by default)
python -m alphadiana.cli run configs/full_runs/<config>.yaml

# Force re-run all tasks (ignores completed)
python -m alphadiana.cli run configs/full_runs/<config>.yaml --redo-all
```

### Local-vLLM Qwen3.5-27B environment

```bash
export ROCK_BASE_URL=http://127.0.0.1:<admin_port>
export ROCK_PROXY_URL=http://127.0.0.1:<proxy_port>
export OPENCLAW_GATEWAY_TOKEN=<token>
```

### MiniMax cloud environment

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

### Campaign runner

```bash
python scripts/benchmark_rollout.py summary
python scripts/benchmark_rollout.py preflight --check-docker --check-rock --probe-vllm
python scripts/benchmark_rollout.py commands --wave wave_a_mainline
python scripts/benchmark_rollout.py materialize --wave wave_a_mainline
```
