# Runbook — SWE-Bench Verified Mini × Gemma 4 31B

Formalized on 2026-07-24 for these four cells:

| Cell | Execution path | Checked-in config |
| --- | --- | --- |
| DirectLLM | Official standalone SWE-agent, upstream default agent | None; follow §4 |
| OpenClaw | AlphaDiana `openclaw` + `swebench_container` | `configs/full_runs/swe_verified_mini_openclaw_gemma4_31b.yaml` |
| OpenCode | AlphaDiana `opencode` + `swebench_container` | `configs/full_runs/swe_verified_mini_opencode_gemma4_31b.yaml` |
| ZeroClaw | AlphaDiana `zeroclaw` + `swebench_container` | `configs/full_runs/swe_verified_mini_zeroclaw_gemma4_31b.yaml` |

DirectLLM deliberately has no AlphaDiana experiment YAML. In this matrix,
“DirectLLM” means the official SWE-agent path using upstream
`config/default.yaml`. The other three cells run the agent inside each
SWE-bench task container through AlphaDiana.

## 1. Locked parameter contract

| Parameter | Value | Enforcement point |
| --- | --- | --- |
| Dataset | `MariusHobbhahn/swe-bench-verified-mini`, `test`, 50 tasks | SWE-agent CLI or AlphaDiana benchmark block |
| Model | `google/gemma-4-31B-it` | vLLM and agent settings |
| Temperature | `0.0` | SWE-agent CLI or AlphaDiana agent config |
| Sample K | `1` | One SWE-agent attempt or `num_samples: 1` |
| Maximum model length | `262144` (256K) | vLLM `--max-model-len` |
| Maximum output tokens | `131072` (128K) | SWE-agent CLI or AlphaDiana agent config |
| Top-p | `0.95` | SWE-agent CLI or AlphaDiana agent config |
| Presence penalty | `1.5` | Shared vLLM generation default |
| Thinking | `true` | vLLM default plus AlphaDiana request override |
| Streaming | `true` | SWE-agent transport or native agent transport |
| Maximum concurrency | `4` | SWE-agent `--num_workers` or AlphaDiana `max_concurrent` |
| HF repository | `T-MARS/alphadiana-benchmark-results` (private dataset) | Upload step |
| HF folder | `YYYYMMDD-dataset-agent-model-vNN` | Run ID and upload destination |

Presence penalty and model length are server properties. They appear in config
metadata for auditability but are enforced by the vLLM launch, not by
AlphaDiana request rewriting.

Logprob capture is off because it is not part of this matrix contract and the
official SWE-agent path does not write AlphaDiana logprob sidecars.

## 2. Host and checkout prerequisites

Run all AlphaDiana commands from the repository root. Required:

- Docker works without `sudo` (`docker ps` succeeds).
- The AlphaDiana environment is installed and activated.
- At least 200 GB of free space is available for task images and artifacts.
- The three checked-in configs pass `python -m alphadiana.cli validate`.
- A separate official SWE-agent checkout exists for DirectLLM.

Create the official-agent layout once:

```bash
export DIRECTLLM_SWE_VERIFIED_ROOT=/path/to/swe-bench-root
mkdir -p "$DIRECTLLM_SWE_VERIFIED_ROOT"
cd "$DIRECTLLM_SWE_VERIFIED_ROOT"

git clone https://github.com/SWE-agent/SWE-agent
python3.11 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -e SWE-agent
./.venv/bin/pip install swebench huggingface_hub
```

Expected layout:

```text
$DIRECTLLM_SWE_VERIFIED_ROOT/
├── .venv/
├── SWE-agent/
│   └── config/default.yaml
└── sweagent_results/
```

## 3. Start the shared Gemma 4 vLLM endpoint

Set `GEMMA4_CHAT_TEMPLATE` to the
`examples/tool_chat_template_gemma4.jinja` shipped by the installed vLLM
release.

```bash
export VLLM_PORT=8011
export VLLM_GPUS=0,1
export VLLM_TENSOR_PARALLEL=2
export GEMMA4_CHAT_TEMPLATE=/path/to/examples/tool_chat_template_gemma4.jinja

CUDA_VISIBLE_DEVICES="$VLLM_GPUS" \
vllm serve google/gemma-4-31B-it \
  --host 0.0.0.0 \
  --port "$VLLM_PORT" \
  --trust-remote-code \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --reasoning-parser gemma4 \
  --chat-template "$GEMMA4_CHAT_TEMPLATE" \
  --default-chat-template-kwargs '{"enable_thinking":true}' \
  --enable-prefix-caching \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL" \
  --gpu-memory-utilization 0.90 \
  --max-model-len 262144 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty":1.5}' \
  --served-model-name gemma-4-31b-it google/gemma-4-31B-it
```

The default chat-template setting is required for DirectLLM because upstream
SWE-agent's default config does not expose AlphaDiana's request-level
`enable_thinking` option.

## 4. DirectLLM: official default SWE-agent

This is the official standalone path and intentionally does not use an
AlphaDiana YAML.

```bash
export OPENAI_BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"
export OPENAI_API_KEY=EMPTY
export RUN_ID="$(date -u +%Y%m%d)-swe_bench_verified_mini-directllm-gemma-4-31b-it-v01"

cd "$DIRECTLLM_SWE_VERIFIED_ROOT/SWE-agent"
mkdir -p ../sweagent_results

OPENAI_BASE_URL="$OPENAI_BASE_URL" OPENAI_API_KEY="$OPENAI_API_KEY" \
../.venv/bin/sweagent run-batch \
  --config config/default.yaml \
  --output_dir "../sweagent_results/$RUN_ID" \
  --num_workers 4 \
  --random_delay_multiplier 0 \
  --instances.type swe_bench \
  --instances.path_override MariusHobbhahn/swe-bench-verified-mini \
  --instances.subset verified \
  --instances.split test \
  --instances.shuffle=False \
  --instances.evaluate=False \
  --instances.deployment.type docker \
  --instances.deployment.startup_timeout 1800 \
  --agent.model.name openai/gemma-4-31b-it \
  --agent.model.api_base "$OPENAI_BASE_URL" \
  --agent.model.api_key "$OPENAI_API_KEY" \
  --agent.model.temperature 0.0 \
  --agent.model.top_p 0.95 \
  --agent.model.max_output_tokens 131072 \
  --agent.model.per_instance_cost_limit 0 \
  --agent.model.total_cost_limit 0 \
  --agent.model.per_instance_call_limit 80 \
  --progress_bar False
```

Then run the official evaluator:

```bash
cd "$DIRECTLLM_SWE_VERIFIED_ROOT"
./.venv/bin/python -m swebench.harness.run_evaluation \
  --dataset_name MariusHobbhahn/swe-bench-verified-mini \
  --split test \
  --predictions_path "sweagent_results/$RUN_ID/preds.json" \
  --run_id "$RUN_ID" \
  --max_workers 4
```

If the installed SWE-agent revision writes only per-instance `.pred` files,
use that checkout's patch-gather helper to produce `preds.json`, then rerun
only the evaluation command.

## 5. AlphaDiana environment and config validation

Return to the AlphaDiana checkout and export values reachable from both the
host and Docker bridge containers:

```bash
export OPENAI_BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1"
export SWE_CONTAINER_OPENAI_BASE_URL="http://host.docker.internal:${VLLM_PORT}/v1"
export OPENAI_API_KEY=EMPTY
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"

curl -fsS "$OPENAI_BASE_URL/models"
docker ps

python -m alphadiana.cli validate \
  configs/full_runs/swe_verified_mini_openclaw_gemma4_31b.yaml
python -m alphadiana.cli validate \
  configs/full_runs/swe_verified_mini_opencode_gemma4_31b.yaml
python -m alphadiana.cli validate \
  configs/full_runs/swe_verified_mini_zeroclaw_gemma4_31b.yaml
```

`SWE_CONTAINER_OPENAI_BASE_URL` must be reachable from a normal Docker bridge
container. Verify the bridge gateway on the run host rather than assuming it
is always `host.docker.internal`.

## 6. One-task AlphaDiana smokes

Use unique smoke run IDs so the full-run checkpoint is never polluted:

```bash
python -m alphadiana.cli run \
  configs/full_runs/swe_verified_mini_openclaw_gemma4_31b.yaml \
  --redo-all \
  -o run_id="$(date -u +%Y%m%d)-swe_bench_verified_mini-openclaw-gemma-4-31b-it-smoke" \
  -o benchmark.config.max_tasks=1 \
  -o max_concurrent=1

python -m alphadiana.cli run \
  configs/full_runs/swe_verified_mini_opencode_gemma4_31b.yaml \
  --redo-all \
  -o run_id="$(date -u +%Y%m%d)-swe_bench_verified_mini-opencode-gemma-4-31b-it-smoke" \
  -o benchmark.config.max_tasks=1 \
  -o max_concurrent=1

python -m alphadiana.cli run \
  configs/full_runs/swe_verified_mini_zeroclaw_gemma4_31b.yaml \
  --redo-all \
  -o run_id="$(date -u +%Y%m%d)-swe_bench_verified_mini-zeroclaw-gemma-4-31b-it-smoke" \
  -o benchmark.config.max_tasks=1 \
  -o max_concurrent=1
```

Before the full sweep, confirm each smoke wrote one task JSON, preserved a
trajectory/runtime trace, and reached a classified SWE-bench outcome rather
than an unclassified infrastructure error.

## 7. Full AlphaDiana runs

Run the three cells sequentially unless the provider has been capacity-tested
beyond four simultaneous long-context workers. Reusing a run ID resumes from
AlphaDiana checkpoints; do not add `--redo-all` to a resume.

```bash
export RUN_DATE="$(date -u +%Y%m%d)"

export RUN_ID="${RUN_DATE}-swe_bench_verified_mini-openclaw-gemma-4-31b-it-v01"
python -m alphadiana.cli run \
  configs/full_runs/swe_verified_mini_openclaw_gemma4_31b.yaml \
  -o run_id="$RUN_ID" 2>&1 | tee "logs/$RUN_ID.log"

export RUN_ID="${RUN_DATE}-swe_bench_verified_mini-opencode-gemma-4-31b-it-v01"
python -m alphadiana.cli run \
  configs/full_runs/swe_verified_mini_opencode_gemma4_31b.yaml \
  -o run_id="$RUN_ID" 2>&1 | tee "logs/$RUN_ID.log"

export RUN_ID="${RUN_DATE}-swe_bench_verified_mini-zeroclaw-gemma-4-31b-it-v01"
python -m alphadiana.cli run \
  configs/full_runs/swe_verified_mini_zeroclaw_gemma4_31b.yaml \
  -o run_id="$RUN_ID" 2>&1 | tee "logs/$RUN_ID.log"
```

For unattended runs, put each command in a named `tmux` session and retain the
same `tee` log path.

## 8. Completion checks

For an AlphaDiana run:

```bash
find "results/$RUN_ID/tasks" -maxdepth 1 -name '*.json' | wc -l
test -f "results/$RUN_ID/run_manifest.json"
test -f "results/$RUN_ID.jsonl"
```

The task count must be 50. Inspect any row whose `score_status` is not
`valid_scored`; task files store sample lists, so inspect `data[0]` rather
than treating the JSON root as a result record.

For DirectLLM:

```bash
find "$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/$RUN_ID" \
  -name '*.pred' | wc -l
test -f "$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/$RUN_ID/preds.json"
```

## 9. Upload to the private HF dataset

Authenticate with a token that can write
`T-MARS/alphadiana-benchmark-results`. Keep full runs below `full_run/` and
use the local run ID as the folder name:

```bash
export HF_REPO=T-MARS/alphadiana-benchmark-results
export HF_HUB_WRITE_TOKEN=hf_...
export RESULTS_LOCAL="$PWD/results/$RUN_ID"
export HF_FOLDER="full_run/$RUN_ID"

huggingface-cli upload \
  "$HF_REPO" "$RESULTS_LOCAL" "$HF_FOLDER" \
  --repo-type dataset \
  --token "$HF_HUB_WRITE_TOKEN"

huggingface-cli upload \
  "$HF_REPO" "$RESULTS_LOCAL.jsonl" "$HF_FOLDER.jsonl" \
  --repo-type dataset \
  --token "$HF_HUB_WRITE_TOKEN"
```

For DirectLLM, set `RESULTS_LOCAL` to
`$DIRECTLLM_SWE_VERIFIED_ROOT/sweagent_results/$RUN_ID` and upload the
directory. There is no AlphaDiana sibling JSONL on that path.

Never overwrite an existing `vNN`. Bump `v01` to the next free two-digit
version for a repaired or repeated run.

## 10. Path-specific notes

- OpenCode's task-container adapter installs its configured/current CLI
  package in the task container; preserve the effective CLI version in the
  uploaded artifacts.
- ZeroClaw owns downstream streaming through its native CLI. The config uses
  the provider proxy without logprob capture so `top_p`, `max_tokens`, and
  thinking are applied to upstream requests without requesting sidecars.
- Presence penalty is deliberately server-side so all four cells inherit the
  same value.
- This runbook targets Docker. Re-formalize gateway and networking settings
  before substituting Podman.
