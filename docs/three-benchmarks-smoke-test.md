# Benchmark Execution Playbook: IMO-AnswerBench, HLE, terminal-bench-2

Use during development to stabilize the `openclaw × {imo_answerbench, hle}` and `terminal_bench2_docker × terminal_bench2` paths. Not for paper claims or formal runs.

For CLI usage, run `alphadiana run --help`. For environment setup, see `README.md`.

## Pass Criteria

### Smoke (1–2 tasks per benchmark)

All of the following must be true **for each benchmark**:

- `results/<run_id>/<run_id>.jsonl` contains task records
- `results/<run_id>/tasks/<task_id>.json` exists for each task
- The task record has **no error dict** (`"error": null`)
- Score fields are populated (`correct`, `score` are not None)

`correct=False` is acceptable — it reflects model capability, not pipeline failure.

### Pilot (5–10 tasks)

- Infra failures absent or rare
- Checkpoint/resume works (re-running the same `run_id` skips completed tasks)
- Timeout and concurrency are stable

## What Differs from Main README.md

| Aspect | Main README (AIME/custom) | These Three Benchmarks |
|--------|---------------------------|------------------------|
| **Agent** | `openclaw` with ROCK sandbox auto-deploy | IMO/HLE: `openclaw` with pre-deployed sandbox (`SANDBOX_ID`). TB2: `terminal_bench2_docker` (no sandbox) |
| **Sandbox lifecycle** | Auto-created by Runner | Must be pre-deployed via `openclaw_deploy/deploy.py` for IMO/HLE. TB2 uses host Docker directly |
| **Scorer** | `math_verify`, `numeric` | IMO: `math_verify`. HLE: `exact_match`. TB2: `terminal_bench2` (binary from reward.txt) |
| **External deps** | HuggingFace datasets | IMO: HuggingFace `Hwilner/imo-answerbench`. HLE: gated `cais/hle` (requires `HF_TOKEN`). TB2: local git clone + Docker images |
| **Python deps** | Core only | `tomli>=2.0` needed for TB2 (in `[benchmarks]` optional deps) |
| **Pre-flight** | `alphadiana env` checks ROCK | IMO/HLE: manual ROCK + sandbox health check. TB2: Docker daemon must be accessible |
| **Config `api_base`** | Auto-configured from `rock_image` | IMO/HLE: explicit ROCK proxy URL with `$SANDBOX_ID`. TB2: OpenAI-compatible endpoint via `$OPENAI_BASE_URL` |

## Environment Setup

### Common

```bash
# In project root
source scripts/activate.sh          # or manually:
#   source scripts/rock_env.sh
#   source .env

# Verify
alphadiana env                       # admin ✓  proxy ✓  redis ✓  docker ✓
alphadiana list-benchmarks           # Should show: aime, custom, hle, imo_answerbench, external_benchmark, terminal_bench2
```

### For IMO-AnswerBench and HLE

```bash
# Set model endpoint (OpenRouter example)
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-or-v1-...
export OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507

# Deploy OpenClaw sandbox (takes ~25s)
python openclaw_deploy/deploy.py \
  --agent-config openclaw_deploy/rock_agent_config.prebuilt.yaml \
  --image ghcr.io/tsrigo/openclaw-reasoning:20260320
# Output: Sandbox ID: <SANDBOX_ID>

export SANDBOX_ID=<paste-id-here>
export ROCK_PROXY_PORT=9045

# Verify gateway
curl -s -X POST "http://127.0.0.1:9045/apis/envs/sandbox/v1/sandboxes/${SANDBOX_ID}/proxy/v1/chat/completions" \
  -H "Content-Type: application/json" -H "Authorization: Bearer OPENCLAW" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"2+2"}],"max_tokens":10,"stream":false}'
# Expected: {"choices":[{"message":{"content":"4",...}}],...}
```

### For HLE (additional)

```bash
export HF_TOKEN=hf_...                         # Required — cais/hle is gated
export HF_ENDPOINT=https://hf-mirror.com       # China mirror (optional)
```

### For terminal-bench-2

```bash
# Clone task repo
git clone --depth=1 https://github.com/harbor-framework/terminal-bench-2.git /path/to/terminal-bench-2
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench-2

# Pre-pull Docker images
docker pull alexgshaw/db-wal-recovery:20251031
# If Docker Hub is blocked, use mirror:
#   docker pull hub.rat.dev/alexgshaw/db-wal-recovery:20251031
#   docker tag hub.rat.dev/alexgshaw/db-wal-recovery:20251031 alexgshaw/db-wal-recovery:20251031

# Install tomli (needed for task.toml parsing)
pip install tomli

# Set LLM endpoint (same as above, or different)
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-or-v1-...
export OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507
```

## Smoke Test Commands

### 1. IMO-AnswerBench (OpenClaw + math_verify)

```bash
alphadiana run configs/examples/openclaw_imo_answerbench.yaml \
  -o benchmark.config.max_tasks=2
```

**Expected output:**
```
Run completed: openclaw-qwen3-27b-imo-answerbench-001
  Accuracy:   0.5000      # Model-dependent — any numeric value is OK
  Mean Score: 0.5000
  Pass@1:    0.5000
  Avg@1:     0.5000
  Tasks:      2/2 completed
```

**Verify:**
```bash
ls results/openclaw-qwen3-27b-imo-answerbench-001/tasks/
# Expected: imo_answerbench_0.json  imo_answerbench_1.json
```

### 2. HLE (OpenClaw + exact_match)

```bash
alphadiana run configs/examples/openclaw_hle.yaml \
  -o benchmark.config.max_tasks=2
```

**Expected output:**
```
Run completed: openclaw-qwen3-27b-hle-001
  Accuracy:   0.0000      # HLE is extremely hard — 0.0 is expected
  Mean Score: 0.0000
  Tasks:      2/2 completed
```

**Verify:**
```bash
ls results/openclaw-qwen3-27b-hle-001/tasks/
# Expected: hle_1.json  hle_11.json  (task IDs depend on dataset ordering)
```

### 3. terminal-bench-2 (Docker exec relay + binary scorer)

```bash
alphadiana run configs/examples/terminal_bench2.yaml \
  -o benchmark.config.max_tasks=1
```

**Expected output:**
```
Run completed: terminal_bench2_smoke
  Accuracy:   0.0000      # Task difficulty — 0.0 is acceptable
  Mean Score: 0.0000
  Tasks:      1/1 completed
```

**Verify:**
```bash
# Task JSON exists
ls results/terminal_bench2_smoke/terminal_bench2_smoke/tasks/
# Expected: tb2_db-wal-recovery.json (or first file-operations task)
```

## Smoke Pass Criteria Checklist

For each benchmark, confirm:

- [ ] Command exits with code 0
- [ ] `Tasks: N/N completed` in output (no failed dispatches)
- [ ] `results/<run_id>/tasks/<task_id>.json` exists for each task
- [ ] Task JSON has `"error": null` (not an error dict)
- [ ] `correct` and `score` fields are populated (True/False and float, not None)
- [ ] Re-running the same command skips completed tasks (checkpoint works)

## Failure Classes (Additions to Main Playbook)

| Class | Typical signs | Check first |
|-------|---------------|-------------|
| Sandbox expired | `sandbox ... not started` | Redeploy: `python openclaw_deploy/deploy.py ...` |
| Gateway unhealthy | 405 on health check, but POST works | This is normal — OpenClaw gateway returns 405 on GET /models |
| Docker image missing | `Unable to find image` | Pre-pull: `docker pull alexgshaw/<task>:20251031` |
| Docker proxy blocked | `proxyconnect tcp: dial tcp 127.0.0.1:1087` | Clear daemon proxy or use mirror |
| HF_TOKEN missing | `401 Unauthorized` on dataset load | `export HF_TOKEN=hf_...` |
| tomli not installed | `ModuleNotFoundError: No module named 'tomli'` | `pip install tomli` |
| test.sh timeout | `[TIMEOUT after Ns]` | Increase `test_timeout_sec` in config (default 900s) |

## Verified Smoke Results (A800, 2026-04-15)

| Benchmark | run_id | Tasks | Correct | Accuracy | Error? | Agent | Model |
|-----------|--------|-------|---------|----------|--------|-------|-------|
| IMO-AnswerBench | `openclaw-qwen3-27b-imo-answerbench-001` | 2/2 | 1 | 0.5000 | No | OpenClaw | qwen/qwen3-235b-a22b-2507 |
| HLE | `openclaw-qwen3-27b-hle-001` | 2/2 | 0 | 0.0000 | No | OpenClaw | qwen/qwen3-235b-a22b-2507 |
| terminal-bench-2 | `terminal_bench2_smoke` | 1/1 | 0 | 0.0000 | No | Docker exec relay | qwen/qwen3-235b-a22b-2507 |

**Task-level verification:**

| task_id | correct | score | error | predicted | status |
|---------|---------|-------|-------|-----------|--------|
| `imo_answerbench_0` | True | 1.0 | null | `3` | O |
| `imo_answerbench_1` | False | 0.0 | null | `2` | X |
| `hle_1` | False | 0.0 | null | `B` | X |
| `hle_11` | False | 0.0 | null | `D` | X |
| `tb2_db-wal-recovery` | False | 0.0 | null | `0` | X |

All 5 tasks have: task JSON present, no error dict, score populated. Smoke passed.
