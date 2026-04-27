# Full-Run Configs

Active production configs in this directory are intentionally limited to the
five non-code benchmarks and four harness families currently being realigned:

- `direct_llm`
- `opencode`
- `openclaw`
- `zeroclaw`

Older full-run configs, campaign manifests, smoke configs, templates, and
non-target harness configs were moved to
[`archive/pre_prompt_alignment_20260425/`](archive/pre_prompt_alignment_20260425/).
They are retained for audit/reference, but they are not the active entry points
for the current prompt-aligned local-Qwen runs.

Canonical prompts live in [`../PROMPTS.md`](../PROMPTS.md). Every active config
below uses the matching Direct or Harness prompt from that file.

## Active Inventory

| Benchmark | DirectLLM | OpenCode | OpenClaw | ZeroClaw |
|---|---|---|---|---|
| AIME 2024 | [`aime_directllm_qwen35_27b_logprobs.yaml`](aime_directllm_qwen35_27b_logprobs.yaml) | [`aime_opencode_qwen35_27b_logprobs.yaml`](aime_opencode_qwen35_27b_logprobs.yaml) | [`aime_openclaw_qwen35_27b_logprobs.yaml`](aime_openclaw_qwen35_27b_logprobs.yaml) | [`aime_zeroclaw_qwen35_27b_logprobs.yaml`](aime_zeroclaw_qwen35_27b_logprobs.yaml) |
| IMO-AnswerBench | [`imo_directllm_qwen35_27b_logprobs.yaml`](imo_directllm_qwen35_27b_logprobs.yaml) | [`imo_opencode_qwen35_27b_logprobs.yaml`](imo_opencode_qwen35_27b_logprobs.yaml) | [`imo_openclaw_qwen35_27b_logprobs.yaml`](imo_openclaw_qwen35_27b_logprobs.yaml) | [`imo_zeroclaw_qwen35_27b_logprobs.yaml`](imo_zeroclaw_qwen35_27b_logprobs.yaml) |
| GPQA-Diamond | [`gpqa_directllm_qwen35_27b_logprobs.yaml`](gpqa_directllm_qwen35_27b_logprobs.yaml) | [`gpqa_opencode_qwen35_27b_logprobs.yaml`](gpqa_opencode_qwen35_27b_logprobs.yaml) | [`gpqa_openclaw_qwen35_27b_logprobs.yaml`](gpqa_openclaw_qwen35_27b_logprobs.yaml) | [`gpqa_zeroclaw_qwen35_27b_logprobs.yaml`](gpqa_zeroclaw_qwen35_27b_logprobs.yaml) |
| HLE multiple-choice | [`hle_directllm_qwen35_27b_logprobs.yaml`](hle_directllm_qwen35_27b_logprobs.yaml) | [`hle_opencode_qwen35_27b_logprobs.yaml`](hle_opencode_qwen35_27b_logprobs.yaml) | [`hle_openclaw_qwen35_27b_logprobs.yaml`](hle_openclaw_qwen35_27b_logprobs.yaml) | [`hle_zeroclaw_qwen35_27b_logprobs.yaml`](hle_zeroclaw_qwen35_27b_logprobs.yaml) |
| MMMU-Pro vision | [`mmmu_pro_directllm_qwen35_27b_logprobs.yaml`](mmmu_pro_directllm_qwen35_27b_logprobs.yaml) | [`mmmu_pro_opencode_qwen35_27b_logprobs.yaml`](mmmu_pro_opencode_qwen35_27b_logprobs.yaml) | [`mmmu_pro_openclaw_qwen35_27b_logprobs.yaml`](mmmu_pro_openclaw_qwen35_27b_logprobs.yaml) | [`mmmu_pro_zeroclaw_qwen35_27b_logprobs.yaml`](mmmu_pro_zeroclaw_qwen35_27b_logprobs.yaml) |

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
- `openclaw`: `agent.config.enable_thinking: true`; the provider proxy injects
  this as `chat_template_kwargs.enable_thinking=true`
- `zeroclaw`: `agent.config.enable_thinking: true`; the provider proxy injects
  this as `chat_template_kwargs.enable_thinking=true`

OpenCode configs also set:

- `controller_mode: docker`
- `controller_network: host`
- `controller_image: alphadiana/tb2-opencode-controller:latest`
- `timeout: 9300`
- `streaming: true`
- `tool_call: true`

OpenClaw configs also set:

- `rock_agent_config_path: openclaw_deploy/rock_agent_config.prebuilt.yaml`
- `openclaw_config_path: openclaw_deploy/openclaw.json`
- `request_timeout: 9300`
- `stream_idle_timeout: 1800` for the standard five-benchmark configs;
  AIME 2026 keeps `stream_idle_timeout: 9300`
- `stream_total_timeout: 9000`
- Predeployed ROCK sandboxes default to fresh-per-task mode:
  `reuse_predeployed_sandboxes: false`. Each task gets a ready sandbox/gateway,
  results and artifacts are written, then the sandbox is closed instead of
  reset and reused. Set `standby_sandboxes: N` to keep extra ready sandboxes
  warm while workers consume the active pool. The older reset-and-reuse mode is
  still available with `reuse_predeployed_sandboxes: true`; that path clears
  OpenClaw session state under the known OpenClaw home directories on every
  reset to prevent stale chat history reuse.
- `predeployed_lease_probe: true` is enabled by default for real ROCK sessions.
  Before assigning a predeployed sandbox, the runner makes a short
  `trust_env=False` reachability probe to the sandbox's host-published gateway
  and discards/replaces the sandbox if the gateway is already unreachable. This
  prevents a dead standby sandbox from consuming a full task retry cycle.

ZeroClaw configs also set:

- `provider_api_base: http://127.0.0.1:8011/v1`
- `runtime_trace_mode: all`
- `request_timeout: 9300`
- `max_tool_iterations: 100`

The AIME 2026 OpenClaw full-run config is intentionally conservative for
shared local-vLLM recovery: `max_concurrent: 1`, `max_tokens: 131072`,
`agent.config.num_sandboxes: 1`, and `agent.config.standby_sandboxes: 1`.
It also sets `task_retries: 2` with
`task_retry_on_recoverable_only: true`, so a task-level `runtime_error` caused
by a dead gateway or sandbox can be retried on a replacement fresh sandbox in
the same run without retrying ordinary output-cap or timeout failures. Raise
concurrency only with explicit overrides after checking local vLLM queue
headroom. The AIME 2026 ZeroClaw full-run config remains lowered to
`max_concurrent: 2` and additionally sets `task_retries: 2` so checkpoint
resumes can replace a dead pooled ROCK session and retry the affected sample on
a fresh sandbox.

Result persistence redacts common secret env assignments and sensitive key
fields before writing JSONL records, task JSONs, sandbox metadata, retry
artifacts, normalized traces, and common text artifacts. Raw experiment shell
logs still live under `logs/` and should be treated as local operational logs.
Result paths are resolved relative to the launch directory at run setup time,
so background ROCK deployment cwd changes cannot redirect JSONL, task JSON,
artifact, dashboard, or logprob sidecar writes.

For local `long64k` ZeroClaw smoke/recovery runs, use the serial smoke configs
under `configs/smokes/harness_prompt_alignment_20260425/` or override
`-o max_concurrent=1`. The April 26 evidence found that concurrent ZeroClaw
long-run recovery can stall before the first provider request for the full
9300-second timeout; serial checkpoint resume completed the affected tasks and
captured logprobs normally.

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
