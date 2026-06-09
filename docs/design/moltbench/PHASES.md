# MoltBench Implementation Phases

**Status:** Draft v0.1  
**Date:** 2026-06-09

This phase plan starts after the existing AlphaDiana planning history. Phase numbers intentionally start at 17.

## Cross-Cutting Gates

Run on every phase:

- Hidden verifier files are not mounted into writable workspaces.
- `EvaluationSuitcase` completeness audit passes for every real run.
- Budget records match observed model/tool/eval usage.
- Snapshot restore reproduces best-state metric.
- No agent path bypasses `run_eval` to call verifier internals.
- No official run gives the agent internet/retrieval access to inspect latest upstream seed repos unless explicitly marked non-leaderboard.
- Docs/context support claims are updated only after real evidence exists.

## Phase 17 — Contracts, Registries, and Suitcase Schema

**Goal:** Freeze the MoltBench schema bus before runtime logic.

**Builds:** `alphadiana/moltbench/schema`, registry skeletons, JSON-schema export, contract tests.

**Exit gate:** ProjectEpisode, EvalAttempt, AttemptBudget, MoltEvent, EvaluationSuitcase, ContaminationPolicy, and C/G/S/M/K metric records round-trip losslessly and export stable JSON schemas.

## Phase 18 — Workspace, Snapshot, Sandbox, and run_eval Kernel

**Goal:** Materialize isolated seed workspaces and expose a safe `run_eval` loop.

**Builds:** workspace manager, snapshot manager, sandbox adapter boundary, RunEvalManager, AttemptRunner, budget controller.

**Exit gate:** DummyAgent can run a toy episode, call `run_eval`, exhaust budget, rollback to best snapshot, and export a complete suitcase.

## Phase 19 — Agent Adapters and Event Recording

**Goal:** Run real agent systems through a standardized adapter boundary while recording observable events.

**Builds:** DummyAgent, DirectLLM, OpenClaw-style, OpenCode-style adapters; TraceRecorder; model/tool/file/memory event stores.

**Exit gate:** Two non-dummy adapters complete a toy episode in sandbox/container runtime and write model/tool/file/verifier/budget events with content hashes.

## Phase 20 — AutoKernel MVP Family

**Goal:** Prove the first real seed-project evolution benchmark family.

**Builds:** AutoKernel seed loader, public task, hidden verifier wrapper, fast/official eval split if needed, metric mapping, smoke configs, audit script, context report.

**Exit gate:** baseline and at least one agent run complete with task-level artifacts, reproducible suitcases, and C/G/S/M/K metrics.

## Phase 21 — Retrosynthesis Planning Family Design and Pilot

**Goal:** Add chemistry retrosynthesis as a principled task-family pilot without claiming leaderboard readiness.

**Builds:** Syntheseus-compatible seed package, route output schema, hidden-stock verifier, call counter, reward-hack fixtures, smoke and pilot configs.

**Exit gate:** public smoke and hidden mini-pilot complete; hidden stock/targets are protected; route validity/cycle/duplicate/call-budget checks are enforced; suitcase audit passes.

## Phase 22 — QJL / Mini-SGLang Expansion and Reporting

**Goal:** Expand beyond the first family and produce comparable reports.

**Builds:** QJL and Mini-SGLang seed manifests, family runbooks, verifier wrappers, dashboard/report additions, compare/replay CLI.

**Exit gate:** at least two families can be compared through the same CLI/report path, with support boundaries documented and no training/eval data leakage.
