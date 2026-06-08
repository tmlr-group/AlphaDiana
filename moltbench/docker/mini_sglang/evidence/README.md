# mini-SGLang — Level-3 reproduction evidence

Committed artifacts from real runs on an **A800 (podman 3.4.4, Qwen3-4B)**, as the
baseline-reproduction + anti-hacking evidence required for MoltBench admission **Level 3**
(Design-4 §16). Large/binary artifacts (`*_workspace.tar`, raw `server.log`) are NOT
committed — they are reproducible; see `moltbench/docs/mini-sglang-reproduce.md`.

## Contents

```
smoke_easy_qwen3-4b_20260608/   no-agent episode (MOLTBENCH_AGENT=none): baseline -> official
  manifest.json                 ConfigLock (versions, seed, model, verifier hashes, hardware)
  baseline_eval.json            in-situ baseline on pristine seed
  official_eval.json            official eval on the (unchanged) candidate
  results.json / score.json     C/G/K + correctness gate
  throughput_metrics.json       median + ci95 + per-pass + cold
  latency_metrics.json warmup_policy.json
  events.jsonl                  MoltEvent stream (run_eval_call / verifier_result)
  file_diffs.jsonl              per-attempt diff vs pristine seed (empty: 0 attempts)
  model_calls.jsonl model_cost.json    harness K aggregation (0: no agent)
  verifier_integrity.json       status: OK (verifier unmodified)
  audit_report.md               integrity assertions + artifact checklist
probes/
  probe_F_verifier_tamper.json + .log    tamper /eval -> TAMPERED -> run invalid
  probe_D_*                              valid baseline, broken candidate -> score 0
  PROBES.md                              full A–F protocol + outcomes
```

## Headline numbers (smoke, GPU 3, no agent)

| field | value |
|---|---|
| baseline_throughput | **4162.32 tok/s** (in-situ, warmup-excluded median) |
| official OG | **1.130** — see note (NOT a real gain) |
| correctness | **PASS** (num_error == 0) |
| files changed | **0** (`file_diffs.jsonl` — confirms no code was modified) |
| per-run ci95 | 14.69 tok/s (tight within a run) |
| verifier integrity | **OK** |
| ConfigLock | present (seed_commit 20fcd7f, verifier hashes, A800/535.183.01 fingerprint) |

`python moltbench/validate_task.py artifacts <smoke_dir>` → **24/24 PASS**.

> **Reading OG=1.13 with 0 code changes (important).** This is *not* a gain — `file_diffs.jsonl`
> shows the candidate is byte-identical to the seed. It is **cross-restart serving drift**:
> baseline and official are two separate server restarts on a shared GPU, and 4B throughput
> drifts run-to-run even though each run's own ci95 is tight. This is exactly the serving-noise
> reality the task is built around (Design-5 §12): a single-run OG is noisy, which is why the
> verifier reports CI, why a real reference optimization (PR#97) showed no *robust* gain, and
> why a leaderboard number must be a repeated-measurement distribution, not one episode. An
> earlier run on GPU 2 gave baseline 4679 / OG 1.008 — same code, different draw.

## How this was produced

See `moltbench/docs/mini-sglang-reproduce.md` (§2 smoke, §5 verify, §8 probes). Image:
thin overlay of the delivery image with the updated `/eval` verifier
(`localhost/mini-sglang-agents:l3`). The episode is environment-agnostic; only the
host mounts (CUDA, model cache) and the GPU id change.
