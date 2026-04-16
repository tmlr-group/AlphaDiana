# Exec Plan: PR25 3x3 Stabilization

Last updated: 2026-04-16
Owner: Codex
Status: blocked on `terminal_bench2_openclaw`

## Goal

Make PR25 merge-ready on the local `pr-25-head` worktree by:

- fixing the reproduced PR25 blockers
- making the three benchmark modes (`openclaw`, `direct_llm`, `opencode`) usable across the three new benchmarks
- recording exact local smoke evidence in durable docs

## Non-Goals

- Pushing to GitHub
- Merging into `main`
- Claiming benchmark quality from 1-task smoke runs

## Acceptance Criteria

- `list-benchmarks` includes `external_benchmark`
- external_benchmark regression collection passes again
- 3x3 smoke matrix completes end-to-end with local run IDs recorded in docs
- `docs/three-benchmarks-pipeline.md` reflects the actual local process and evidence

## Work Plan

1. Reconcile the local PR25 worktree with previously validated blocker fixes.
2. Add missing runtime/config support for the full 3x3 matrix.
3. Run targeted regression tests and smoke runs.
4. Update durable docs with exact commands, configs, run IDs, and caveats.

## Progress Log

- 2026-04-15 23:00 Confirmed active worktree was `/tmp/AlphaDiana-dev-pr25` on `pr-25-head`, not the earlier scratch worktree.
- 2026-04-15 23:20 Ported back the missing PR blocker fixes: `scripts/full_pipeline.py`, `external_benchmark` CLI registration, terminal-bench dependency/setup fixes, ROCK env precedence fixes.
- 2026-04-15 23:35 Added `opencode`, `terminal_bench2_openclaw`, and `terminal_bench2_opencode` smoke paths plus new matrix configs.
- 2026-04-15 23:45 Added deterministic smoke selectors for IMO/HLE via `dataset_index`.
- 2026-04-16 00:06 Completed the 3x3 matrix and recorded all run IDs.
- 2026-04-16 00:08 Re-ran the targeted regression suite; it passed after fixing lazy imports in `openclaw_deploy/deploy.py`.
- 2026-04-16 08:55 Re-ran the full 3x3 matrix with the current operator-provided OpenAI-compatible API key/base and `minimax-m2.5`; all nine cells completed `1/1` and wrote JSONL results under `pr25_live2_20260416_*` run IDs.
- 2026-04-16 09:55 Tightened the acceptance criterion to require visible model output. Raised OpenCode/OpenClaw terminal-bench timeouts to 1800s and reran affected cells. `opencode` now passes all three cells with visible model output. `terminal_bench2_openclaw` still produces no visible planner output within 1800s and completes `0/1`.

## Decision Log

- 2026-04-15 23:42 Chose `imo_answerbench` `dataset_index=367` because the default first item was too slow for bounded smoke.
- 2026-04-15 23:48 Chose `opencode` 120-second smoke bounds so the CLI paths would finish and score instead of hanging indefinitely.
- 2026-04-16 00:05 Added `continue_on_planner_error` to `terminal_bench2_openclaw` smoke so planner silence degrades to verifier-backed reward `0` instead of a hung run.
- 2026-04-16 09:55 Reversed the fallback decision for strict smoke validation: `terminal_bench2_openclaw` now uses `continue_on_planner_error=false`, because a planner timeout without model output must count as a failed smoke path.

## Outcome

- Final status: blocked on `terminal_bench2_openclaw`
- What changed:
  - PR25 blocker fixes were applied on the local PR worktree.
  - `opencode` smoke timeouts were raised to 1800s; the strict rerun now shows visible model output for all three `opencode` cells.
  - `terminal_bench2_openclaw` was rerun with a 1800s request timeout and still produced no visible planner output; it is not merge-ready under the strict criterion.
  - Durable docs were updated with exact run IDs, timeout conventions, and the current blocker.
- Follow-up:
  - Fix or redesign the `terminal_bench2_openclaw` planner path before treating PR25 as fully merge-ready.
