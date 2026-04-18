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
  Manual setup, troubleshooting, and environment details.
- [Dashboard](dashboard.md)
  Dashboard usage and deployment.
- [Benchmark Runbooks](benchmarks/README.md)
  User-facing runbooks for supported benchmark paths.

## Canonical Benchmark Docs

- [IMO-AnswerBench](benchmarks/imo-answerbench.md)
- [HLE](benchmarks/hle.md)
- [GPQA-Diamond](benchmarks/gpqa-diamond.md)
- [MMMU-Pro](benchmarks/mmmu-pro.md)
- [SWE-bench Pro](benchmarks/swebench-pro.md)
- [SWE-bench Verified](benchmarks/swebench-verified.md)
- [terminal-bench-2](benchmarks/terminal-bench-2.md)

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
- `context/P25-three-benchmarks/openclaw_opencode_hle_multimodal_validation_20260417.md`
  Dated HLE multimodal validation note.
- `context/pr26-swebench-verified/implementation-notes.md`
  Internal note for the older SWE-bench Verified / container path.

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
