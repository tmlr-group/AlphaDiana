# Phase 9 Runbook — DirectLLM + GPQA-Diamond + Qwen3.5-27B + Logprobs

End-to-end operator playbook for running Phase 9. Assumes vLLM is already
serving `Qwen/Qwen3.5-27B` at `http://127.0.0.1:8011/v1` with
`--reasoning-parser qwen3`.

## Preflight

1. **Verify vLLM endpoint is up:**
   ```bash
   curl -sS http://127.0.0.1:8011/v1/models | python -m json.tool
   ```
   Expected: JSON response with `data[].id` equal to `Qwen/Qwen3.5-27B`.

   For Phase 9, using the shared local vLLM endpoint is acceptable; the
   Docker/container isolation requirement applies to agent runtimes such as
   OpenClaw, OpenCode, and ZeroClaw, not to this direct local vLLM service.

2. **Verify disk headroom (need > 20 GB free for logprobs JSONL files):**
   ```bash
   df -h ./results
   ```
   Expected: at least 20 GB available. If not, clean up old runs or change
   `output_dir` in the config to a larger filesystem.

3. **Use a writable Hugging Face dataset cache if needed:**
   ```bash
   export HF_ENDPOINT=https://hf-mirror.com
   export HF_DATASETS_CACHE=/tmp/alphadiana_hf_datasets_phase9
   ```
   This avoids lock-file failures when the shared `/path/to/datasets` cache is
   readable but not writable.

## Step 1 — Smoke Run (1 task, ~2 min)

For the direct vLLM GPQA script that stores trajectory/artifacts plus top-20
Int16 logprob probabilities, run:

```bash
python scripts/phase9_gpqa_top20_int16_entropy.py \
  --api-base http://127.0.0.1:8011/v1 \
  --api-key EMPTY \
  --model Qwen/Qwen3.5-27B \
  --run-id phase9_gpqa_direct_vllm_top20_int16_smoke \
  --max-tasks 1
```

Expected artifacts:

- `results/phase9_gpqa_direct_vllm_top20_int16_smoke/tasks/gpqa_0.json`
- `results/phase9_gpqa_direct_vllm_top20_int16_smoke/trajectories/gpqa_0.json`
- `results/phase9_gpqa_direct_vllm_top20_int16_smoke/artifacts/gpqa_0/response.json`
- `results/phase9_gpqa_direct_vllm_top20_int16_smoke/logprobs_int16/gpqa_0.jsonl`

`response.json` strips raw float provider logprobs; the canonical logprob
artifact is `logprobs_int16/<task_id>.jsonl`.

The legacy AlphaDiana-runner smoke remains:

```bash
alphadiana run configs/full_runs/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml
```

**Acceptance gate — do NOT proceed to the full run unless ALL checks pass:**

```bash
RUN_DIR=results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs_smoke
TASK_JSON=$(ls "$RUN_DIR"/tasks/*.json | head -1)

# A. token_entropy_stats is non-empty
python -c "import json; r=json.load(open('$TASK_JSON'))[0]; s=r['token_entropy_stats']; \
           assert s.get('n_tokens', 0) > 0, s; print('A ok:', s)"

# B. logprobs JSONL exists and is non-empty
python -c "import json; r=json.load(open('$TASK_JSON'))[0]; \
           assert r['logprobs_path'], r; print('B ok:', r['logprobs_path'])"
LP_FILE=results/$(python -c "import json; print(json.load(open('$TASK_JSON'))[0]['logprobs_path'])")
wc -l "$LP_FILE"   # must show > 0

# C. Reasoning-parser alignment — n_tokens within 10% of completion_tokens
python -c "
import json
r = json.load(open('$TASK_JSON'))[0]
n_tok = r['token_entropy_stats']['n_tokens']
comp = r['token_usage']['completion_tokens']
ratio = abs(n_tok - comp) / max(comp, 1)
assert ratio <= 0.10, f'MISALIGNED: n_tokens={n_tok} completion_tokens={comp} ratio={ratio:.2%}'
print(f'C ok: n_tokens={n_tok} completion={comp} ratio={ratio:.2%}')
"
```

If check C fails, escalate to engineering — do NOT run the full 2-3 hour
workload. The reasoning-parser / logprobs alignment issue needs investigation
before burning GPU time.

## Step 2 — Full Run (198 tasks, ~2-3 hours)

Direct vLLM GPQA script:

```bash
python scripts/phase9_gpqa_top20_int16_entropy.py \
  --api-base http://127.0.0.1:8011/v1 \
  --api-key EMPTY \
  --model Qwen/Qwen3.5-27B \
  --run-id phase9_gpqa_direct_vllm_top20_int16 \
  --max-tasks 198
```

This writes compact Int16 logprob files under
`results/phase9_gpqa_direct_vllm_top20_int16/logprobs_int16/`.

Legacy AlphaDiana-runner full run:

Open **two terminal panes** (or tmux windows).

**Pane A — the rollout:**

```bash
alphadiana run configs/full_runs/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs.yaml
```

**Pane B — the monitor (invoked by Codex via `codex:rescue`, or manually):**

```bash
python scripts/phase9_monitor.py \
  --run-dir results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs \
  --task-timeout 1800 \
  --silence-timeout 2700 \
  --max-consecutive-failures 5 \
  --poll-interval 30 \
  2>&1 | tee results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs/monitor.log
```

**What to watch for in the monitor log:**
- `ALERT: task <task_id> exceeded timeout` — investigate vLLM; may indicate a stuck generation.
- `ALERT: No new results in <N>s. Run may be stalled.` — check Pane A for errors; check vLLM health via `/v1/models`.
- `ALERT: N consecutive task failures. Investigate.` — monitor exits non-zero; **terminate the rollout** in Pane A (Ctrl-C) and triage.

## Step 3 — Post-Run Review

When Pane A exits cleanly (all 198 tasks completed):

```bash
python scripts/phase9_review.py \
  --run-dir results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs
```

This writes `codex_review.md` inside the run directory. The file contains:
- Overall accuracy (count correct / 198)
- Entropy summary (mean, p25, p50, p75, p90 across per-task entropy means)
- High-entropy errors (model was uncertain AND wrong — candidates for self-consistency / retry experiments in future phases)
- Low-entropy errors (model was confident AND wrong — likely systematic gaps; flag for qualitative review)

Codex (via `codex:rescue` or a separate session) can then read `codex_review.md`
and produce a narrative analysis.

## Recovery — if the run is interrupted

The rollout runner resumes via `completed_task_ids()` on the JSONL file — re-running
the same command skips already-completed tasks. Preserve
`results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs/` between runs.

## Cleanup

After archiving results, the logprobs JSONL directory can be compressed:

```bash
cd results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs
tar czf logprobs.tar.gz logprobs/ && rm -rf logprobs/
```

Compression typically reduces size by 5-10x.
