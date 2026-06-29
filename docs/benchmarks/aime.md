# AIME

AIME benchmark runs use `benchmark.name: aime` with numeric scoring on
`MathArena/aime_2026`.

## Prerequisites

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD

export QWEN_VLLM_API_BASE=http://127.0.0.1:8011/v1
export QWEN_VLLM_API_KEY=EMPTY
export HF_HOME=<hf-cache-dir>
export HF_DATASETS_CACHE=<hf-cache-dir>/datasets
export HUGGINGFACE_HUB_CACHE=<hf-cache-dir>/hub
```

The checked-in AIME 2026 path assumes a local vLLM endpoint serving
`Qwen/Qwen3.5-27B` with `max_model_len=262144` and `allow_logprobs=true`.

## Supported Modes

| Mode | Status | Config |
|---|---|---|
| `direct_llm` | local-vLLM `Qwen/Qwen3.5-27B` | `configs/full_runs/phase9_directllm_qwen35_27b_aime2026_logprobs.yaml` |
| `opencode` | local-vLLM `Qwen/Qwen3.5-27B`, Docker controller + logprob sidecars | `configs/full_runs/phase12_opencode_qwen35_27b_aime2026_k32.yaml` |

## Full Run

### OpenCode

```bash
python -u -m alphadiana.cli run \
  configs/full_runs/phase12_opencode_qwen35_27b_aime2026_k32.yaml \
  2>&1 | tee logs/aime2026_opencode_full.log
```

Runtime contract:

- `temperature: 0.6`
- `top_p: 0.95`
- `max_tokens: 131072`
- `streaming: true`
- `timeout: 9000`
- `max_concurrent: 10`
- `num_samples: 32`
- `capture_logprobs: true`
- `top_logprobs: 20`
- Docker controller: `alphadiana/tb2-opencode-controller:latest`
- server-side preconditions: `max_model_len=262144`, thinking enabled, `presence_penalty=1.5`

Do not set `thinking` or `presence_penalty` in the OpenCode request config; those
are served-model / server-side controls on this setup.

Before a full run, run two small checks with distinct run IDs:

```bash
python -u -m alphadiana.cli run \
  configs/full_runs/phase12_opencode_qwen35_27b_aime2026_k32.yaml \
  --redo-all \
  -o run_id=aime2026_opencode_smoke_t1_k1 \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1 \
  -o max_concurrent=1 \
  -o agent.config.max_tokens=25000
```

Then check a known long-generation item:

```bash
python -u -m alphadiana.cli run \
  configs/full_runs/phase12_opencode_qwen35_27b_aime2026_k32.yaml \
  --redo-all \
  -o run_id=aime17_opencode_max25k \
  -o benchmark.config.split='train[16:17]' \
  -o num_samples=1 \
  -o max_concurrent=1 \
  -o agent.config.max_tokens=25000
```

Expected evidence for the long-generation check:

- `tasks/aime_17.json` exists and is a one-record sample list.
- `score_status=valid_scored`; the score can be `0` because the purpose is
  transport and storage validation.
- OpenCode records `step_finish.reason="length"` and output tokens equal the
  `max_tokens` override.
- Float and Int16 logprob sidecars have the same number of lines as the output
  token count.
- `artifacts/aime_17/workspace/opencode_partial_output.txt` exists and preserves
  the streamed model text for audit.

On timeout or truncation, OpenCode result preservation writes
`opencode_partial_output.txt` from the captured logprob tokens, so those paths
stay auditable even when no text events are flushed before the error.

### DirectLLM

The checked-in config captures per-token top-20 logprobs in Int16 form, enables
Qwen reasoning, and requests `32` samples per task.

```bash
python -u -m alphadiana.cli run \
  configs/full_runs/phase9_directllm_qwen35_27b_aime2026_logprobs.yaml \
  --redo-all \
  2>&1 | tee logs/aime2026_directllm_full.log
```

Runtime contract:

- `temperature: 0.6`
- `top_p: 0.95`
- `presence_penalty: 1.5`
- `max_tokens: 131072`
- `stream: true`
- `extra_body.include_reasoning: true`
- `max_concurrent: 10`
- `num_samples: 32`
- `capture_logprobs: true`
- `top_logprobs: 20`
- `logprobs_format: int16`

## Smoke Before Full Run

Use the same config with CLI overrides so the smoke and full run share one entry
point:

```bash
python -u -m alphadiana.cli run \
  configs/full_runs/phase9_directllm_qwen35_27b_aime2026_logprobs.yaml \
  -o run_id=aime2026_directllm_smoke_t1 \
  -o num_samples=2 \
  -o max_concurrent=2 \
  -o benchmark.config.max_tasks=1 \
  --redo-all
```

Expected artifacts:

- task JSON list under `tasks/aime_*.json` with one record per `sample_index`
- `artifacts/<task_id>/...` for sample `0` and `artifacts/<task_id>/sample_<N>/...` for later samples
- `logprobs/<task_id>.jsonl` for sample `0`
- `logprobs/<task_id>/sample_<N>.jsonl` for later samples

Do not upload the smoke run.
