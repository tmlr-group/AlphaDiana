# Docs Index

Use this directory as a user-facing entry point. The canonical split is:

- setup and first-run docs under `docs/`
- benchmark runbooks under `docs/benchmarks/`
- current support snapshots, reviewer-facing evidence, and dated validation trails under `context/`

## Start Here

- [Current Eval Status](../context/current_eval_status.md)
  Cross-benchmark support snapshot, recommended smoke paths, and active limitations.
- [Getting Started](getting_started.md)
  End-to-end onboarding tutorial.
- [Setup Details](setup_detail.md)
  Manual setup, troubleshooting, shared-host ROCK isolation, and environment details.
- [Dashboard](dashboard.md)
  Dashboard usage and deployment.
- [Benchmark Runbooks](benchmarks/README.md)
  User-facing runbooks for supported benchmark paths.
- [Benchmark Isolation Notes](benchmark-isolation.md)
  Paper-safe summary of which benchmark paths are task-scoped sandboxes or
  containers today.
- [Full Local-vLLM Rollout (2026-04-19)](benchmarks/full-rollout-local-vllm-20260419.md)
  Staged `72`-run rollout manifest, preflight flow, and command generator for the local-vLLM campaign.

## Specialized Guides

- [OpenClaw Benchmark Reliability](benchmarks/openclaw.md)
  Current fresh-per-task sandbox, timeout, retry, heartbeat, and result-validity
  guidance for OpenClaw benchmark runs.
- [ZeroClaw AIME 2026 Runbook](zeroclaw_aime2026_runbook.md)
  Short, command-first runbook for the ZeroClaw AIME 2026 path.
- [ZeroClaw Local-Qwen Rerun Parameters](benchmarks/zeroclaw-local-qwen-rerun-20260428.md)
  April 28 parameter contract for rerunning the five non-coding ZeroClaw
  local-Qwen benchmark paths.
- [ZeroClaw AIME 2026 Tutorial](tutorial_zeroclaw_aime2026.md)
  More detailed ZeroClaw tutorial with setup and background.
- [OpenCode Docker Isolation](opencode-docker-isolation.md)
  Focused note on the Docker-controller isolation posture for the checked-in
  OpenCode benchmark paths.

## Canonical Benchmark Docs

- [IMO-AnswerBench](benchmarks/imo-answerbench.md)
- [HLE](benchmarks/hle.md)
- [GPQA-Diamond](benchmarks/gpqa-diamond.md)
- [MMMU-Pro](benchmarks/mmmu-pro.md)
- [SWE-bench Pro](benchmarks/swebench-pro.md)
- [SWE-bench Verified](benchmarks/swebench-verified.md)
- [terminal-bench-2](benchmarks/terminal-bench-2.md)
- [Full Local-vLLM Rollout (2026-04-19)](benchmarks/full-rollout-local-vllm-20260419.md)

If you are trying to run a benchmark, start from `context/current_eval_status.md`
and then the matching file in `docs/benchmarks/`.

## Historical / Deep-Dive Notes

These references are kept for deep dives, but they are not the primary runbook
entry points:

- [tutorial_openclaw_aime2024.md](tutorial_openclaw_aime2024.md)
  Historical AIME/OpenClaw tutorial. Prefer `README.md` and
  `docs/getting_started.md`.
- [quickstart_commands.md](quickstart_commands.md)
  Environment-specific local ops notes; not the canonical setup guide.
- [context/P25-three-benchmarks/README.md](../context/P25-three-benchmarks/README.md)
  Archived PR25 three-benchmark evidence bundle. Use only when you need the
  older workstream-specific review trail.
- [context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md](../context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md)
  Dated HLE multimodal validation note.
- [context/qwen-openrouter-pilots/README.md](../context/qwen-openrouter-pilots/README.md)
  Dated April 18-20 Qwen/OpenRouter pilot evidence for the benchmark rollout.
- [context/benchmark-rollout-deepseek-OpenAI-compatible-20260420/README.md](../context/benchmark-rollout-deepseek-OpenAI-compatible-20260420/README.md)
  Dated DeepSeek/OpenAI-compatible full-rollout launch and infra-repair note for the IMO/GPQA campaign.
- [context/pr26-swebench-verified/README.md](../context/pr26-swebench-verified/README.md)
  Entry point for the PR26 SWE-bench Verified evidence bundle.
- [context/pr29-add-swebench-pro/README.md](../context/pr29-add-swebench-pro/README.md)
  Entry point for the PR29 SWE-bench Pro reproduction context.

Archived one-off notes live under [archive/](archive/).

## What Belongs In `context/`

Move these into `context/`:

- dated smoke / pilot validation writeups
- cross-benchmark current status snapshots
- task-level evidence summaries
- local debug trails
- PR or milestone handoff notes

If a file is mainly about `run_id`, logs, task JSONs, or reviewer evidence, it
belongs under `context/`.
