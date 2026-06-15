# Task Admission Review: moltbench_qjl_optimize

## Decision

**Level 2 Smoke-Test Ready, pending GPU revalidation.** The hardened
implementation is not leaderboard eligible yet. Memory and drift claims are
not eligible.

## Summary

QJL now runs candidate build and benchmark subprocesses as an unprivileged
user. Feedback and official seeds are disjoint. Final baseline, episode-best,
and final candidate evaluation runs in a fresh networkless verifier container.
The scorer verifies the host manifest and `score.json` hash.

## Passed Gates

- Static Python, shell, YAML, and JSON validation passes.
- QJL unit suite passes with 21 tests.
- C/G/S/M/K is explicit; M is `N/A`.
- Fake `results.json` and response scores are ignored.
- Non-editable source paths are rejected by host-side container diff.
- Nested CUDA files and symlinked candidate sources are rejected.
- Host-only verifier config is mode `0600`; suitcase root is mode `0750`.
- OpenClaw, OpenCode, and Claude Code share the same task loader and budgets.

## Reproducibility Check

- Revised image build: not run.
- Revised baseline: not run.
- Revised official eval: not run.
- The prior June 10, 2026 smoke predates this hardening and is historical only.

## Verifier Integrity Check

The read-only `/eval/run_eval` client submits only top-level
`qjl_kernel/csrc/*.cu`. Candidate subprocesses cannot write the host suitcase.
Official eval ignores agent result files, runs without network access, and
performs clean same-seed baseline/best/final rebuilds.

## Metric Check

- C: clean build plus hidden distortion below `0.52`.
- G: baseline runtime divided by official candidate runtime.
- S: final valid gain divided by episode-best valid gain.
- M: N/A.
- K: attempts, verifier calls, wall/GPU time, calls, tokens, and cost.

## Artifact Check

Suitcase finalization records config JSON/YAML, task snapshot, initial/final/best
CUDA snapshots, append-only logs, diffs, official results, score, audit report,
artifact hashes, and an explicit completeness result.

## Warnings

- Rebuild and rerun the complete GPU validation and adversarial probe matrix.
- Do not restore Level 3 until repeated official evaluation is stable.
