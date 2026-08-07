# Memory Scope Micro Configs Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add and verify nine release-grade Memory micro configs covering three memory scopes across OpenClaw, OpenCode, and ZeroClaw.

**Architecture:** Add a small runner-level `memory_scope` contract over the three existing harness-native persistent-memory implementations. Stateful runs dispatch sequentially; cross-sample runs recreate agent and sandbox state at task boundaries, while cross-task runs retain it for the full run.

**Tech Stack:** Python 3.10+, pytest, PyYAML, AlphaDiana experiment runner, ROCK-backed harnesses.

---

### Task 1: Define and test the memory-scope contract

**Files:**
- Modify: `alphadiana/engine/runner.py`
- Create: `tests/test_memory_scopes.py`

1. Write failing tests for accepted scopes, invalid scopes, stateful concurrency,
   and cross-sample task-boundary detection.
2. Run `pytest tests/test_memory_scopes.py -q` and confirm failure.
3. Add the minimal pure helpers to `runner.py`.
4. Run the focused test and confirm it passes.

### Task 2: Implement cross-sample lifecycle reset

**Files:**
- Modify: `alphadiana/engine/runner.py`
- Modify: `tests/test_memory_scopes.py`

1. Write a failing test with fake agents/sessions proving that sample 0 of a new
   task resets state while later samples do not.
2. Add runner helpers to recreate the configured agent and shared sandbox at the
   boundary.
3. Preserve existing behavior when `memory_scope` is absent.
4. Run the focused test suite.

### Task 3: Add the nine reference configs

**Files:**
- Create: `configs/micro_runs/Memory/intra_task/aime2026_opencode_qwen35_27b.yaml`
- Create: `configs/micro_runs/Memory/cross_sample/*.yaml`
- Create: `configs/micro_runs/Memory/cross_task/*.yaml`
- Modify: the two existing Qwen intra-task configs as needed for an explicit scope
- Modify: `configs/micro_runs/README.md`
- Modify: `configs/README.md`
- Modify: `docs/concepts/evaluation-axes.md`

1. Recover the proven A800 settings without credentials or host-specific paths.
2. Keep benchmark, model, scorer, and sampling settings matched across harnesses.
3. Document that the nine configs are smoke/reference cells, not the full paper matrix.
4. Validate every YAML through the CLI.

### Task 4: Remove the duplicate standalone memory example

**Files:**
- Delete: `configs/memory_experiments/exp1_zw_aime_memory_seq.yaml`
- Modify: documentation references to the old path

1. Move its useful ZeroClaw settings into the cross-task reference config.
2. Remove the duplicate directory and update every tracked reference.
3. Verify `git grep memory_experiments` returns no stale release path.

### Task 5: Verify locally and on A800

**Files:**
- No source changes unless verification exposes a bug.

1. Run Python AST parsing and focused pytest.
2. Run all nine CLI validations.
3. Run the existing release tests and website typecheck/build.
4. On A800, preflight provider and ROCK services without printing credentials.
5. Run reduced real smokes and inspect task metadata, traces, and native memory
   diagnostics for scope correctness.
6. Commit and push only `clean-website` after all required checks pass.
