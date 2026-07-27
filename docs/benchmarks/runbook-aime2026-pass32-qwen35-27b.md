# Runbook — AIME 2026 pass@32 × Qwen3.5-27B

Formalized on 2026-07-27 for these three cells, launched **in parallel**:

| Cell | Execution path | Checked-in config |
| --- | --- | --- |
| OpenClaw | AlphaDiana `openclaw` ROCK gateway | `configs/full_runs/aime2026_pass32_openclaw_qwen35_27b.yaml` |
| OpenCode | AlphaDiana `opencode` docker controller | `configs/full_runs/aime2026_pass32_opencode_qwen35_27b.yaml` |
| ZeroClaw | AlphaDiana `zeroclaw` ROCK sandbox | `configs/full_runs/aime2026_pass32_zeroclaw_qwen35_27b.yaml` |

There is deliberately no DirectLLM cell in this matrix. Each config derives
from the existing 2026-05-29 AIME contract cells (OpenCode from the
prompt-aligned GPQA OpenCode cell, since main had no prior AIME OpenCode
config); the YAML headers list the exact fields changed.

## 1. Locked parameter contract

| Parameter | Value | Enforcement point |
| --- | --- | --- |
| Dataset | `MathArena/aime_2026`, `train`, 30 tasks | AlphaDiana benchmark block |
| Model | `Qwen/Qwen3.5-27B` | vLLM and agent settings |
| Temperature | `0.6` | AlphaDiana agent config |
| Sample K | `32` (pass@32) | `num_samples: 32` |
| Maximum model length | `262144` (256K) | vLLM `--max-model-len` |
| Maximum output tokens | `131072` (128K) | AlphaDiana agent config |
| Top-p | `0.95` | AlphaDiana agent config |
| Presence penalty | `1.5` | Shared vLLM generation default |
| Thinking | `true` | AlphaDiana request override (`enable_thinking`) |
| Streaming | `true` | OpenClaw `stream` / OpenCode `streaming` / native ZeroClaw CLI transport |
| Logprobs | `capture_logprobs: true`, `top_logprobs: 20` | AlphaDiana agent config |
| Maximum concurrency | `3` per cell × 3 cells in parallel ≈ 9 | `max_concurrent` + parallel launch |
| Scorer | `numeric`, tolerance `1e-6` | AlphaDiana scorer block |
| HF repository | `T-MARS/alphadiana-benchmark-results` (private dataset) | Upload step |
| HF folder | `YYYYMMDD-aime_2026-<agent>-qwen3.5-27b-vNN` | Run ID and upload destination |

Presence penalty and model length are server properties. They appear in config
metadata for auditability but are enforced by the vLLM launch, not by
AlphaDiana request rewriting.

Sizing: 30 tasks × 32 samples = 960 work items per cell, 2880 total. The
AlphaDiana run summary prints Pass@32 directly (a task passes when any of its
32 samples is correct).

## 2. Host and checkout prerequisites

Run all commands from the repository root. Required:

- Docker works without `sudo` (`docker ps` succeeds).
- The AlphaDiana environment is installed and activated
  (`source scripts/activate.sh; export PYTHONPATH=$PWD`).
- ROCK services for this checkout are up (OpenClaw gateway autodeploy and
  ZeroClaw sandboxes ride on them):

```bash
source scripts/rock_env.sh
python -m alphadiana.cli env
```

- Harness images exist locally: `tmlrgroup/alphadiana:v1` (OpenClaw),
  `alphadiana/tb2-opencode-controller:latest` (OpenCode),
  `zeroclaw-reasoning:0.6.9` (ZeroClaw). Build commands are in
  [`configs/full_runs/README.md`](../../configs/full_runs/README.md).
- RAM headroom for 6 concurrent ROCK sandboxes at `rock_memory: 4g` plus 3
  OpenCode controllers: plan for ≥32 GB free.
- Disk headroom for 2880 samples with top-20 logprob sidecars under long
  thinking chains: plan for ≥100 GB free under `results/`.
- The three checked-in configs pass `python -m alphadiana.cli validate`.

## 3. Start the shared Qwen3.5 vLLM endpoint

```bash
export VLLM_PORT=8011
export VLLM_GPUS=0,1
export VLLM_TENSOR_PARALLEL=2

CUDA_VISIBLE_DEVICES="$VLLM_GPUS" \
vllm serve Qwen/Qwen3.5-27B \
  --host 0.0.0.0 \
  --port "$VLLM_PORT" \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser hermes \
  --enable-prefix-caching \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL" \
  --gpu-memory-utilization 0.90 \
  --max-model-len 262144 \
  --generation-config vllm \
  --override-generation-config '{"presence_penalty":1.5}' \
  --served-model-name Qwen/Qwen3.5-27B
```

The presence-penalty override is the enforcement point for the contract's
`presence_penalty: 1.5`: AlphaDiana harnesses do not inject this field
per-request, so the server default applies it uniformly to all three cells.
Any other job sharing this endpoint inherits the same penalty — do not
co-schedule runs that need a different value.

`enable_thinking` is injected per-request by each harness's provider proxy
(`chat_template_kwargs.enable_thinking=true`), so no server-side chat-template
default is needed.

Verify reachability from the host and from the Docker bridge (OpenClaw and
ZeroClaw call `host.docker.internal`):

```bash
curl -sS "http://127.0.0.1:${VLLM_PORT}/v1/models"
curl -sS "http://host.docker.internal:${VLLM_PORT}/v1/models"
```

If this host uses a different bridge gateway, override the provider URL at
launch instead of editing the configs (see
[`configs/full_runs/README.md`](../../configs/full_runs/README.md)).

## 4. Validate configs and export runtime values

```bash
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
# ROCK_BASE_URL / ROCK_PROXY_URL come from scripts/rock_env.sh

python -m alphadiana.cli validate configs/full_runs/aime2026_pass32_openclaw_qwen35_27b.yaml
python -m alphadiana.cli validate configs/full_runs/aime2026_pass32_opencode_qwen35_27b.yaml
python -m alphadiana.cli validate configs/full_runs/aime2026_pass32_zeroclaw_qwen35_27b.yaml
```

## 5. Smoke runs (1 task × 2 samples)

Use unique smoke run IDs so the full-run checkpoints are never polluted. Two
samples per task make the smoke also prove sampling diversity: at
temperature 0.6 the two outputs must differ. If they are byte-identical, the
sampling parameters are not reaching the server — fix that before burning
2880 full-run samples on a degenerate pass@32.

```bash
for agent in openclaw opencode zeroclaw; do
  python -m alphadiana.cli run \
    "configs/full_runs/aime2026_pass32_${agent}_qwen35_27b.yaml" \
    --redo-all \
    -o run_id="$(date -u +%Y%m%d)-aime_2026-${agent}-qwen3.5-27b-smoke" \
    -o benchmark.config.max_tasks=1 \
    -o num_samples=2 \
    -o max_concurrent=2
done
```

Before the full sweep, confirm each smoke wrote one task JSON with two
distinct samples, preserved a trajectory/runtime trace, and reached
`valid_scored` rather than an unclassified infrastructure error.

## 6. Full runs — three cells in parallel

Launch the three cells simultaneously in named `tmux` sessions. Each keeps
`max_concurrent: 3` from its config, for ~9 concurrent provider requests
total — the campaign's concurrency contract.

```bash
export RUN_DATE="$(date -u +%Y%m%d)"
mkdir -p logs

for agent in openclaw opencode zeroclaw; do
  RUN_ID="${RUN_DATE}-aime_2026-${agent}-qwen3.5-27b-v01"
  tmux new-session -d -s "aime32_${agent}" \
    "python -m alphadiana.cli run \
       configs/full_runs/aime2026_pass32_${agent}_qwen35_27b.yaml \
       -o run_id=${RUN_ID} 2>&1 | tee logs/${RUN_ID}.log"
done
tmux ls
```

Reusing a run ID resumes from AlphaDiana checkpoints; completed samples are
not redone. Do not add `--redo-all` to a resume. For a repaired rerun, bump
`v01` to the next free version.

Wall-clock is roughly `960 × avg_sample_minutes ÷ 3` per cell; take
`avg_sample_minutes` from the smoke. Raise concurrency only with explicit
`-o max_concurrent=N` overrides after checking vLLM queue headroom, and raise
OpenClaw's `-o agent.config.num_sandboxes=N` to match.

## 7. Completion checks

For each cell:

```bash
RUN_ID="${RUN_DATE}-aime_2026-openclaw-qwen3.5-27b-v01"   # repeat per agent
find "results/$RUN_ID/tasks" -maxdepth 1 -name '*.json' | wc -l   # must be 30
python - "$RUN_ID" <<'PY'
import json, sys
run_id = sys.argv[1]
rows = [json.loads(l) for l in open(f"results/{run_id}.jsonl") if l.strip()]
assert len(rows) == 960, f"expected 960 rows, got {len(rows)}"
by_task = {}
for r in rows:
    by_task.setdefault(r["task_id"], []).append(r)
assert len(by_task) == 30
bad = {s for r in rows if (s := r.get("score_status")) != "valid_scored"}
passed = sum(any(r.get("correct") for r in v) for v in by_task.values())
print(f"pass@32 = {passed}/30 = {passed/30:.4f}; non-valid statuses: {bad or 'none'}")
PY
```

The task count must be 30 and the aggregate JSONL must hold 960 rows with 32
samples per task. `strict_report: true` makes the runner exit non-zero when
samples are missing or non-valid. Task JSON files store sample lists — inspect
`data[0]`, not the JSON root.

## 8. Upload to the private HF dataset

Authenticate with a token that can write
`T-MARS/alphadiana-benchmark-results`. Keep full runs below `full_run/` and
use the local run ID as the folder name:

```bash
export HF_REPO=T-MARS/alphadiana-benchmark-results
export HF_HUB_WRITE_TOKEN=hf_...

RUN_ID="${RUN_DATE}-aime_2026-openclaw-qwen3.5-27b-v01"   # repeat per agent
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

Never overwrite an existing `vNN`. Bump to the next free two-digit version for
a repaired or repeated run.

## 9. Path-specific notes

- All three cells share this checkout's single ROCK service instance; parallel
  launch is supported, and the runner's ownership preflight rejects a stale
  ROCK instance from another checkout.
- ZeroClaw owns downstream streaming through its native CLI; its config has no
  separate streaming key. OpenClaw uses `stream: true`, OpenCode
  `streaming: true`.
- `task_retries: 2` mirrors the 2026-05-29 AIME cells so recoverable
  sandbox/gateway failures retry without rerunning ordinary timeouts
  (OpenClaw/OpenCode additionally set `task_retry_on_recoverable_only: true`,
  matching their base cells).
- Timeout-scored-zero semantics from
  [`configs/full_runs/README.md`](../../configs/full_runs/README.md) apply
  unchanged: harness timeouts persist as scored-zero samples, which lowers
  pass@32 rather than blocking completion.
- This runbook targets the Docker/ROCK runtime. Re-formalize before
  substituting Podman.
