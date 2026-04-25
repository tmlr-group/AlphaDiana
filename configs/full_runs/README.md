# Full-Run Configs

Active production configs in this directory are intentionally limited to the
five non-code benchmarks and two harness families currently being realigned:

- `direct_llm`
- `opencode`

Older full-run configs, campaign manifests, smoke configs, templates, and
non-target harness configs were moved to
[`archive/pre_prompt_alignment_20260425/`](archive/pre_prompt_alignment_20260425/).
They are retained for audit/reference, but they are not the active entry points
for the current prompt-aligned local-Qwen runs.

Canonical prompts live in [`../PROMPTS.md`](../PROMPTS.md). Every active config
below uses the matching Direct or Harness prompt from that file.

## Active Inventory

| Benchmark | DirectLLM | OpenCode |
|---|---|---|
| AIME 2024 | [`aime_directllm_qwen35_27b_logprobs.yaml`](aime_directllm_qwen35_27b_logprobs.yaml) | [`aime_opencode_qwen35_27b_logprobs.yaml`](aime_opencode_qwen35_27b_logprobs.yaml) |
| IMO-AnswerBench | [`imo_directllm_qwen35_27b_logprobs.yaml`](imo_directllm_qwen35_27b_logprobs.yaml) | [`imo_opencode_qwen35_27b_logprobs.yaml`](imo_opencode_qwen35_27b_logprobs.yaml) |
| GPQA-Diamond | [`gpqa_directllm_qwen35_27b_logprobs.yaml`](gpqa_directllm_qwen35_27b_logprobs.yaml) | [`gpqa_opencode_qwen35_27b_logprobs.yaml`](gpqa_opencode_qwen35_27b_logprobs.yaml) |
| HLE multiple-choice | [`hle_directllm_qwen35_27b_logprobs.yaml`](hle_directllm_qwen35_27b_logprobs.yaml) | [`hle_opencode_qwen35_27b_logprobs.yaml`](hle_opencode_qwen35_27b_logprobs.yaml) |
| MMMU-Pro vision | [`mmmu_pro_directllm_qwen35_27b_logprobs.yaml`](mmmu_pro_directllm_qwen35_27b_logprobs.yaml) | [`mmmu_pro_opencode_qwen35_27b_logprobs.yaml`](mmmu_pro_opencode_qwen35_27b_logprobs.yaml) |

## Parameter Contract

All active configs align on:

| Field | Value |
|---|---|
| Model | `Qwen/Qwen3.5-27B` |
| Provider base | `http://127.0.0.1:8011/v1` |
| API key | `EMPTY` |
| Temperature | `0.0` |
| `top_p` | `0.95` |
| Output cap | `max_tokens: 131072` |
| Thinking | enabled |
| Streaming | enabled |
| Logprobs | `capture_logprobs: true`, `top_logprobs: 20` |
| Samples | `num_samples: 1`; AIME 2024 uses `num_samples: 32` |
| Task concurrency | `max_concurrent: 10` |
| Output root | `./results` |

Thinking is represented per harness:

- `direct_llm`: `extra_body.chat_template_kwargs.enable_thinking: true`
- `opencode`: `agent.config.enable_thinking: true`; the logprob proxy injects
  this as `chat_template_kwargs.enable_thinking=true` on provider requests

OpenCode configs also set:

- `controller_mode: docker`
- `controller_network: host`
- `controller_image: alphadiana/tb2-opencode-controller:latest`
- `timeout: 9300`
- `streaming: true`
- `tool_call: true`

## Benchmark Scope

| Benchmark | Dataset config |
|---|---|
| AIME 2024 | `HuggingFaceH4/aime_2024`, split `train`, `num_samples: 32` |
| IMO-AnswerBench | `Hwilner/imo-answerbench`, split `train`, scorer `imo_verify` |
| GPQA-Diamond | `fingertap/GPQA-Diamond`, split `test`, seed `42` |
| HLE | `cais/hle`, split `test`, `answer_types: [multipleChoice]` |
| MMMU-Pro | `MMMU/MMMU_Pro`, `data_config: vision`, split `test` |

## Common Commands

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_ENDPOINT=https://hf-mirror.com
```

Verify the local model endpoint before launching:

```bash
curl -sS http://127.0.0.1:8011/v1/models
```

Build the OpenCode controller image if it is not already present:

```bash
docker build --network host \
  -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
```

Validate one config:

```bash
python -m alphadiana.cli validate configs/full_runs/gpqa_opencode_qwen35_27b_logprobs.yaml
```

Run one config with resumable checkpoint semantics:

```bash
mkdir -p logs
python -m alphadiana.cli run configs/full_runs/gpqa_opencode_qwen35_27b_logprobs.yaml \
  2>&1 | tee logs/full_gpqa_opencode_qwen35_27b_logprobs.log
```

Use `--redo-all` only when intentionally discarding completed checkpoint
artifacts for that run ID.
