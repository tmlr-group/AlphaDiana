# MoltBench Implementation Phases

**Status:** Draft v0.2  
**Date:** 2026-06-09  
**Revision note:** Updated after repository evidence showed the AutoKernel/KernelBench Docker harness, scorer, docs, configs, and tests are already implemented. This plan is brownfield hardening, not greenfield build-out.

## Current Baseline

The repository already contains:

- `alphadiana/benchmark/moltbench.py` — registered MoltBench benchmark loader for KernelBench/AutoKernel tasks.
- `alphadiana/benchmark/moltbench_utils.py` — task loading, prompt/task JSON construction, artifact parsing, and `fast_p_*` helpers.
- `alphadiana/agent/moltbench_docker.py` — shared Docker GPU harness adapter for Claude Code, OpenClaw, OpenCode, and ZeroClaw agent containers.
- `alphadiana/scorer/moltbench.py` — correctness/speedup scorer with threshold flags and artifact metadata.
- `moltbench/docker/` — base and agent leaf Dockerfiles plus manifest-driven `run_task.sh`.
- `moltbench/seeds.yaml` — AutoKernel pinned seed metadata plus planned QJL/Mini-SGLang entries.
- `configs/examples/moltbench_*.yaml` — smoke and batch configs.
- `moltbench/docs/README.md` and `moltbench/docs/zeroclaw.md` — current user docs.
- `tests/test_moltbench_pipeline.py` and `tests/test_moltbench_regressions.py` — focused regression tests.

Focused validation on 2026-06-09:

```bash
python -m pytest tests/test_moltbench_pipeline.py tests/test_moltbench_regressions.py -q
# 33 passed in 0.88s
```

## Cross-Cutting Gates

Run on every future MoltBench phase:

- Do not break existing AutoKernel smoke/batch config compatibility.
- Preserve per-task/per-sample/per-execution artifact isolation.
- Preserve final independent verification before score extraction.
- Keep API keys and agent auth out of committed docs and public artifacts.
- Do not claim live support changes from static tests alone; support requires real runs and task-level artifact inspection.
- Do not claim hidden-verifier/private-test protection unless hidden files are structurally outside the writable workspace.
- Do not allow official agents to retrieve latest upstream seed repositories unless the run is explicitly non-leaderboard/debug.
- Keep docs/context support boundaries in sync with evidence.

## Phase 17 — Existing AutoKernel Harness Baseline · Complete

**Goal:** Reconcile planning with the already implemented MoltBench AutoKernel path.

**Implemented evidence:** benchmark loader, task JSON/prompt utilities, Docker harness adapter, Docker images/entrypoint, scorer, configs, docs, focused tests.

**Exit gate:** planning docs updated and focused tests pass.

**Status:** Complete on 2026-06-09.

## Phase 18 — Audit, Suitcase, and Contract Hardening

**Goal:** Add an additive audit/suitcase layer around the current harness without replacing it.

**Builds:**

- Per-run MoltBench audit manifest / suitcase index.
- Native metric -> C/G/S/M/K mapping.
- Explicit budget-field report for enforced vs unenforced dimensions.
- Optional formal contract wrappers around existing AlphaDiana result records.
- ContaminationPolicy metadata for official/debug modes.

**Exit gate:** a current AutoKernel smoke writes existing artifacts plus the new manifest/index, and an audit script fails when required files/fields are missing.

## Phase 19 — Live Evidence and Support Boundary Refresh

**Goal:** Refresh current live evidence for the AutoKernel-only support boundary.

**Builds:**

- Current smoke command and raw log.
- Task JSON inspection notes.
- Artifact inspection notes.
- Context/status update that separates static regression coverage from live support evidence.

**Exit gate:** either a live smoke passes and is documented, or the blocker is documented without changing support status.

## Phase 20 — QJL and Mini-SGLang Family Expansion

**Goal:** Promote planned seeds into implemented families using the current/hardened MoltBench harness contract.

**Builds:**

- Seed manifests with pinned commits and license/dependency metadata.
- Docker/runtime assets.
- Setup/eval commands.
- Output parsers and scorer mappings.
- Example configs.
- Focused tests and at least one smoke per family.

**Exit gate:** QJL and Mini-SGLang each produce auditable task artifacts and family-specific metric records without regressing AutoKernel.

## Phase 21 — Retrosynthesis Planning Family Pilot

**Goal:** Add a chemistry retrosynthesis task family where agents improve planner code under fixed call/search/runtime budgets.

**Builds:**

- Syntheseus-compatible seed package.
- Locked public/hidden targets and stock split.
- One-step model/call counter wrapper.
- Route output schema.
- Hidden verifier metrics.
- Reward-hacking fixtures.

**Exit gate:** public smoke and hidden mini-pilot complete with no hidden stock/target leakage and with cycle/invalid/duplicate/call-budget failures labeled.
