# AlphaDiana AIME 2026 pass@64 × Qwen3.5-27B

This bundle formalizes three runs, launched **in parallel**:

| Run | Execution path | Config | num_samples | max_concurrent |
| --- | --- | --- | --- | --- |
| OpenClaw | AlphaDiana ROCK gateway | `configs/openclaw.yaml` | 64 | 3 |
| OpenCode | AlphaDiana docker controller | `configs/opencode.yaml` | 64 | 3 |
| ZeroClaw | AlphaDiana ROCK sandbox | `configs/zeroclaw.yaml` | 64 | 3 |

3 harnesses × `max_concurrent: 3` ≈ 9 concurrent provider requests — the
campaign's concurrency contract. There is deliberately no DirectLLM row.

Each config derives from the proven paper §5 Tool-axis cell
(`configs/micro_runs/Tool/aime2026_<agent>_qwen35_27b.yaml` in the AlphaDiana
repo) and documents exactly which fields differ: run_id, temperature
(0.0 → 0.6, required for meaningful pass@64), num_samples (4 → 64),
max_concurrent, and metadata.

The shared contract is recorded in `experiment-matrix.yaml`. Follow
`RUNBOOK.md`. The bundle contains no credentials.

## Files

- `RUNBOOK.md` — end-to-end setup, preflight, smoke, parallel launch,
  monitoring, validation, and upload instructions.
- `experiment-matrix.yaml` — machine-readable experiment contract.
- `configs/` — the three AlphaDiana configs.
- `scripts/preflight.sh` — endpoint (both model aliases), Docker, ROCK,
  disk-headroom, and config checks.
- `scripts/run.sh` — launches one run; `--smoke` runs 1 task × 2 samples.
- `scripts/run_all.sh` — launches the three full runs in parallel.
- `scripts/verify_outputs.py` — checks 30 tasks × 64 samples per run, reports
  pass@64, and fails if samples are byte-identical (temperature not applied).
- `scripts/upload.sh` — verifies, stages, and uploads one run to the private
  HF dataset repo without silently reusing an existing destination folder.

## Quick start

```bash
cd /path/to/AlphaDiana
source scripts/rock_env.sh
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=local-key
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
export RUN_VERSION=v01

cd deliverables/20260727-alphadiana-aime2026-pass64-qwen35-27b
bash scripts/preflight.sh
bash scripts/run.sh openclaw --smoke && bash scripts/run.sh opencode --smoke && bash scripts/run.sh zeroclaw --smoke
bash scripts/run_all.sh
```
