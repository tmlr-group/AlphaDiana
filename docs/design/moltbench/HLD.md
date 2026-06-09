# MoltBench High-Level Design

**Status:** Draft v0.1  
**Date:** 2026-06-09  
**Scope:** Base MoltBench implementation inside AlphaDiana, with AutoKernel first and a retrosynthesis planning task family as the chemistry design exemplar.

## 1. Positioning

MoltBench is a stateful, auditable, seed-project benchmark for evaluating agentic self-evolution under hidden verification, long-horizon project state, memory, drift, budget constraints, and full trajectory recording.

MoltBench is **not** a one-shot coding benchmark and **not** a final-answer leaderboard. Every score must be reproducible from an `EvaluationSuitcase` containing the config snapshot, workspace snapshots, trajectory, model/tool/memory/verifier logs, budget records, normalized metrics, audit report, and failure labels.

## 2. North-Star Contract

Every MoltBench run follows:

```text
ProjectEpisode -> Stages -> EvalAttempts -> EvaluationSuitcase
```

The mechanism is:

```text
materialize seed workspace -> agent edits project -> run_eval -> verifier feedback -> keep/rollback/revise -> repeat under budget -> export auditable suitcase
```

The official score is a vector, not just a scalar:

| Metric | Meaning | Hard rule |
|--------|---------|-----------|
| C | correctness / completion | if C fails, G receives no reward |
| G | verified gain over baseline | normalized per task family, preferably log-ratio for ratio metrics |
| S | stability / regression control | final and best snapshots must preserve prior verified behavior |
| M | memory / provenance correctness | memory must be attributable and must not leak private eval data |
| K | cost / budget efficiency | normalized by attempts, model calls, tokens, runtime, and hardware metadata |

## 3. Short-Term MVP Scope

The first implementation should be small enough to debug but principled enough to extend.

### In scope

- ProjectEpisode, Stage, EvalAttempt, AttemptBudget, EvaluationSuitcase schemas.
- Seed-project loader with immutable seed metadata and writable isolated workspace.
- Snapshot manager using git or content-addressed filesystem snapshots for initial, per-attempt, best, and final states.
- `run_eval` wrapper with `fast_eval` and `official_eval` modes.
- Agent adapters for DummyAgent, DirectLLM, OpenClaw-style, and OpenCode-style systems.
- Event-sourced recorder for model calls, tool calls, file diffs, memory events, verifier results, sandbox events, budget events, rollback events, and artifacts.
- Vector scoring pipeline for C/G/S/M/K.
- ArtifactStore and reproducibility metadata.
- Terminal-first CLI and minimal report generation.
- AutoKernel first; QJL second; Mini-SGLang third.
- Retrosynthesis planning task family design and validator skeleton, without claiming public leaderboard readiness.

### Out of scope for MVP

- Public leaderboard launch.
- Training/evolution runtime that consumes private verifier artifacts.
- Full remote cluster scheduler.
- Browser/GUI sandboxes.
- Broad scientific discovery benchmark suite beyond pilot seed families.
- Agent internet/retrieval access to inspect the latest upstream seed repository during official eval.

## 4. Package Layout

Proposed AlphaDiana modules:

```text
alphadiana/moltbench/
├── schema/            # Pydantic contracts and JSON schema snapshots
├── registry/          # seed, episode, agent, verifier, metric, sandbox registries
├── data/              # seed metadata loaders and release manifests
├── workspace/         # materialization, snapshot, diff, rollback, size guard
├── sandbox/           # local/docker-gpu/podman/ROCK adapters via existing runtime boundaries
├── agents/            # AgentAdapter implementations
├── eval/              # EpisodeRunner, AttemptRunner, RunEvalManager, VerifierExecutor
├── recording/         # event store, trace recorder, model/tool/memory/file logs
├── scoring/           # C/G/S/M/K metrics, aggregation, failure labels
├── artifacts/         # suitcase builder, artifact store, reproducibility manifest
├── cli.py             # moltbench run/eval/inspect/replay/compare/report/export-suitcase
└── families/          # autokernel, qjl, minisglang, retrosynthesis, ...
```

Do not scatter MoltBench-specific lifecycle logic across existing benchmark adapters. Existing AlphaDiana container/runtime helpers may be reused behind narrow interfaces.

## 5. Frozen Contracts for Phase 17

Phase 17 freezes the shared bus before logic is built. Changes to these objects after Phase 17 require explicit review and JSON-schema snapshot updates.

### ProjectEpisode

```text
ProjectEpisode
├── episode_id
├── family
├── seed_project
├── task_description
├── stages
├── verifier_config
├── allowed_tools
├── sandbox_config
├── attempt_budget
├── contamination_policy
├── recording_policy
└── scoring_config
```

### EvalAttempt

```text
EvalAttempt
├── attempt_id
├── stage_id
├── workspace_snapshot_before
├── workspace_snapshot_after
├── run_eval_result
├── agent_decision
├── budget_delta
├── file_diff_refs
├── memory_refs
└── artifact_refs
```

### EvaluationSuitcase

```text
EvaluationSuitcase
├── config_snapshot
├── seed_project_metadata
├── initial_workspace_snapshot
├── final_workspace_snapshot
├── best_workspace_snapshot
├── trajectory_logs
├── model_raw_io_logs
├── tool_call_logs
├── memory_logs
├── file_diff_logs
├── verifier_logs
├── sandbox_logs
├── budget_records
├── normalized_metrics
├── audit_report
├── reproducibility_metadata
└── failure_labels
```

### MoltEvent

Events are append-only and event-sourced:

```text
MoltEvent
├── event_id
├── run_id
├── episode_id
├── stage_id
├── attempt_id
├── agent_id
├── event_type
├── timestamp
├── payload
├── content_hash
├── parent_event_id
└── visibility
```

Required event types: `model_call`, `tool_call`, `memory_read`, `memory_write`, `file_read`, `file_write`, `file_diff`, `run_eval_call`, `verifier_result`, `sandbox_event`, `scheduler_event`, `budget_event`, `rollback_event`, `artifact_event`, `error_event`, `human_intervention`.

## 6. Runtime Separation

MoltBench must keep four runtimes separate:

1. **Benchmark construction runtime**: builds seed projects, stages, hidden verifiers, baselines, drift/memory gates, and release manifests.
2. **Evaluation runtime**: runs frozen agents on frozen episodes, enforces budgets/policies, collects trajectories, and scores.
3. **Analysis runtime**: inspects suitcases, computes aggregate metrics, labels failures, and generates reports.
4. **Training/evolution runtime**: consumes only eligible trajectories and never sees private evaluator records.

No module in training/evolution may import hidden verifier implementations or private test data.

## 7. Contamination and Network Rules

Official MoltBench evaluation is a frozen benchmark mode:

- The agent can read public task files and visible tests only.
- The agent must not read hidden verifier source, hidden targets, hidden stock, private tests, or official evaluator implementation.
- The agent must not use internet/retrieval to inspect the latest upstream seed repository or benchmark solutions unless a task explicitly marks retrieval as allowed and non-leaderboard-eligible.
- Network access is denied by default for official eval; exceptions must be recorded in `ContaminationPolicy` and the suitcase.
- API keys stay outside writable workspaces and must never appear in persisted command histories or artifacts.
- Any private-data exposure marks `leaderboard_eligible=false`.

## 8. Retrosynthesis Task Family Contract

The retrosynthesis family evaluates whether an agent can improve planner code, not hand-author routes.

- **Domain:** Chemistry.
- **Seed substrate:** Syntheseus-compatible planner project; optional comparison substrate AiZynthFinder.
- **Core task:** Improve a predefined multi-step retrosynthesis planner from target molecules to purchasable precursors under fixed time/model-call/search budgets.
- **Editable surface:** planner/search algorithm, route value function, expansion policy, pruning, route scoring, stock interface, cycle detection, stopping, and route diversity selection.
- **Locked data:** target molecules, purchasable stock, one-step model, hidden target set, maximum search iterations, maximum model calls, maximum runtime.
- **Output:** route tree or route set per target with reactions, intermediate molecules, purchasable precursors, route cost, route depth, solved flag, and model-call count.
- **Verifier metrics:** solved rate, route depth, route cost, diversity, model calls, runtime, invalid-route rate, and stock-validity rate.
- **Reward-hacking mitigations:** hidden stock, plausibility filters, cycle detection, call-budget normalization, uniqueness checks, and route-quality metrics.

## 9. Development Rules

- Lock schemas before adapters: contract tests and JSON snapshots come first.
- Prove every support claim with a real run and task-level artifacts; config inspection is not evidence.
- Keep hidden verifiers outside writable workspaces.
- Treat `run_eval` as the only feedback API; agents should not invoke verifier internals.
- Preserve intermediate artifacts for every integrated agent.
- Run agents inside the configured sandbox/container runtime, not on the host, except explicit local debug modes marked non-official.
- For each task family, write a family runbook before implementation and a verifier-audit checklist before live runs.
- Every new result status or score field must be documented and covered by tests.
