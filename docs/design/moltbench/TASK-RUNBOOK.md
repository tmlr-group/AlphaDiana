# MoltBench Task Implementation Runbook

**Status:** Draft v0.1  
**Date:** 2026-06-09  
**Audience:** maintainers adding a new MoltBench task family or seed episode.

## 1. Definition of a MoltBench Task

A MoltBench task is a seed-project evolution episode, not a prompt-answer item. A task is accepted only when it defines:

1. immutable seed project metadata;
2. public task material;
3. hidden verifier material;
4. allowed edit surface;
5. attempt budget;
6. `run_eval` behavior;
7. C/G/S/M/K metric mapping;
8. artifact and trajectory requirements;
9. contamination policy;
10. reproducibility metadata.

## 2. Required Task Design Document

Every new family creates `docs/design/moltbench/families/<family>.md` or an equivalent reviewed design file with these sections:

```text
1. Positioning
2. Seed substrate
3. Public task files
4. Hidden verifier files
5. Allowed modification scope
6. Disallowed actions
7. Attempt budget
8. run_eval contract
9. Output schema
10. Verifier graph
11. Metrics: C/G/S/M/K
12. Reward-hacking risks
13. Mitigations
14. Required artifacts
15. Minimal smoke episode
16. Official pilot matrix
17. Exit gates
```

## 3. Development Order

Use this order for each family:

1. **Schema pass:** add/extend family config models and output schemas.
2. **Seed package pass:** create a frozen seed manifest with source repo, base commit, license, lockfiles, image, setup script, baseline, and allowed edit scope.
3. **Workspace pass:** materialize `workspace/TASK.md`, `AGENTS.md`, `EVAL.md`, `MEMORY.md`, `repo/`, `artifacts/`, `logs/`, `trajectories/`, and `.moltbench/` state.
4. **Verifier pass:** implement hidden verifier outside writable workspace; expose only `run_eval` feedback.
5. **Scoring pass:** map verifier output to C/G/S/M/K and failure labels.
6. **Adapter pass:** validate DummyAgent first, then DirectLLM, then OpenClaw/OpenCode-style adapters.
7. **Suitcase pass:** export initial/final/best snapshots, logs, metrics, audit report, and reproducibility manifest.
8. **Smoke pass:** run a tiny public debug episode.
9. **Official pilot pass:** run a hidden-verifier pilot and inspect every task JSON/artifact.
10. **Docs/context pass:** update docs, context, and support status only after real evidence exists.

## 4. Standard Workspace Layout

```text
workspace/
├── TASK.md
├── AGENTS.md
├── EVAL.md
├── MEMORY.md
├── repo/
├── data/
├── artifacts/
├── logs/
├── trajectories/
└── .moltbench/
    ├── stage_state.json
    ├── attempt_state.json
    ├── best_snapshot/
    ├── memory/
    └── metadata.json
```

`TASK.md`, `AGENTS.md`, `EVAL.md`, and `MEMORY.md` are public. Hidden verifier files, hidden targets, hidden stock, private tests, and official evaluator source stay outside the writable workspace.

## 5. `run_eval` Contract

The agent-facing command is simple:

```bash
run_eval
```

The normalized result must include:

```json
{
  "attempt": 3,
  "stage_id": "stage_02_improvement",
  "correct": true,
  "score": 1.929,
  "metric": {},
  "wall_time_sec": 187.4,
  "verifier_status": "PASS",
  "feedback": "short public feedback",
  "error": null
}
```

Rules:

- `fast_eval` may use visible/smoke workloads.
- `official_eval` must use clean rebuilds, private tests, full verifier, and stable measurement.
- Attempt counters are host-controlled, not agent-controlled.
- Verifier timeout and attempt timeout are enforced by the harness.
- Best-state tracking is performed by the harness.
- Feedback must be useful but must not leak private target answers or hidden verifier details.

## 6. Required Verification Gates

Before a family can be marked ready:

- Config validation passes.
- DummyAgent smoke writes a complete suitcase.
- `run_eval` refuses attempts after budget exhaustion.
- Hidden verifier files are not present in the writable workspace.
- Snapshot restore reproduces the best-state metric.
- Official eval rerun from the final suitcase reproduces the same score within tolerance.
- Artifact audit finds config, seed metadata, initial/final/best snapshots, trajectory logs, model/tool/memory/file/verifier logs, budget records, normalized metrics, audit report, and failure labels.
- A deliberate reward-hack fixture is rejected or labeled ineligible.

## 7. Retrosynthesis Planning Family

### 7.1 Task Summary

- **Domain:** Chemistry.
- **Seed project substrate:** Syntheseus-compatible route-search planner.
- **Optional comparison substrate:** AiZynthFinder-compatible planner.
- **Objective:** Improve a predefined multi-step retrosynthesis planner from target molecules to purchasable precursors under fixed model-call, search-iteration, and runtime budgets.
- **Not allowed:** manually writing routes for known targets, editing hidden stock, editing hidden targets, editing evaluator code, or bypassing the one-step model/call counter.

### 7.2 Data Contract

```text
RetrosynthesisEpisode
├── targets_public
├── targets_hidden
├── stock_public
├── stock_hidden
├── one_step_model_ref
├── planner_entrypoint
├── max_search_iterations
├── max_model_calls
├── max_runtime_sec
├── route_output_schema
└── chemistry_validity_policy
```

### 7.3 Editable Surface

Agents may edit:

- planner/search algorithm;
- route value function;
- expansion policy;
- pruning rule;
- route scoring;
- stock interface wrapper, excluding hidden stock contents;
- cycle detection;
- stopping condition;
- route diversity selection;
- caching inside the budget counter.

Agents may not edit:

- hidden target set;
- hidden stock definition;
- one-step model weights or oracle outputs;
- evaluator scripts;
- call-counting wrappers;
- official scoring code;
- chemistry plausibility filters used by the locked verifier.

### 7.4 Output Schema

Each target emits:

```text
RouteResult
├── target_smiles
├── solved
├── routes
│   ├── reactions
│   ├── intermediates
│   ├── purchasable_precursors
│   ├── route_cost
│   ├── route_depth
│   ├── route_hash
│   └── plausibility_flags
├── model_call_count
├── search_iteration_count
├── runtime_sec
└── error
```

### 7.5 Verifier Metrics

- solved rate;
- route depth;
- route cost;
- route diversity;
- model calls;
- runtime;
- invalid-route rate;
- stock-validity rate;
- cycle rate;
- duplicate-route rate;
- hidden-target generalization gap.

### 7.6 Reward-Hacking Controls

| Risk | Required mitigation |
|------|---------------------|
| Exploiting stock definitions | hidden stock plus stock hash in suitcase |
| Shallow but implausible routes | locked plausibility filters and route-quality penalty |
| Route cycles | graph-level cycle detection and cycle failure labels |
| Overusing model calls | host-side call counter and hard attempt failure on overflow |
| Memorizing public targets | hidden target set and public/hidden split report |
| Duplicate route inflation | canonical route hashes and diversity uniqueness checks |
| Editing evaluator/counter | hidden verifier outside workspace and immutable runner mount |

### 7.7 Minimum Smoke

A minimal retrosynthesis smoke must include:

1. two public debug targets;
2. one tiny public stock;
3. deterministic stub one-step model;
4. DummyAgent that makes no changes and produces baseline score;
5. one agent edit that improves a visible planner fixture;
6. official hidden target mini-eval that writes a complete suitcase.

### 7.8 Exit Gate

Retrosynthesis pilot is ready only when:

- output schema validates for every target;
- hidden stock and hidden targets are absent from the workspace;
- call counts are host-observed;
- cycle/invalid/duplicate routes are labeled;
- baseline and improved planner scores are reproducible from suitcases;
- C/G/S/M/K vector is populated;
- reward-hacking fixtures fail or lose eligibility.

## 8. Review Plan Pattern

For each phase, create a `.github/reviews/<phase>-review-plan.md` that assigns:

- contract review;
- verifier review;
- sandbox/security review;
- artifact/suitcase audit;
- real-run evidence owner;
- cross-review partner;
- exit-gate coordinator.

Use severity prefixes in review comments:

- `[Blocker]` must be fixed before phase completion;
- `[Should]` should be fixed or explicitly justified;
- `[Nit]` optional.

Hard rule: any schema/frozen-contract change needs a second reviewer and regenerated JSON-schema snapshots.
