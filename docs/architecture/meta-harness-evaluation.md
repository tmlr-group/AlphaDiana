# Meta-Harness evaluation contract

AlphaDiana is the evaluation layer for the Pi + Terminal-Bench 2 experiment.
The optimizer remains in `official_experiment`; it chooses candidates, keeps
the training frontier, and decides which candidate is sent for evaluation.
AlphaDiana does not choose a parent or promote a candidate.

## What is being evaluated

The current study is a deliberately constrained adaptation, not a faithful
implementation of Stanford's Meta-Harness protocol. The recorded protocol is:

| Dimension | Pi adaptation | Consequence |
|---|---|---|
| Search artifact | Self-contained TypeScript `ExtensionAPI` extension | The candidate cannot replace Pi core, provider plumbing, or Harbor infrastructure. |
| Parent rule | Proposer selects lineage from the full evaluated population | The aggregate training frontier is feedback and final selection, not a forced parent. |
| Candidates | One candidate per iteration | Candidate cardinality is part of the experiment budget. |
| Proposer workflow | Autonomous | Delegation, ordering, and concurrency are proposer choices and are telemetry, not acceptance requirements. |
| TB2 split | Project-specific training/final task sets | It must not be reported as the original TB2 protocol. |

The extension space is narrower than the original full Python `AgentHarness`.
Within Pi's documented API, a candidate can add lifecycle hooks, transform
system guidance, observe or gate tool events, register extension commands/tools,
and maintain extension-local state. The validator and runtime contract forbid
model/provider/request changes, credentials, direct process spawning, external
network calls, scored-task hints, and edits to Pi core. The space is therefore
broader than prompt-only search in principle, although the completed smoke
candidates used prompt-guidance hooks.

## Evaluation boundary

For each `(task, sample, retry)`, the Pi-Harbor adapter starts a fresh Harbor
job and imports its completed artifacts read-only. Harbor owns the task image,
workspace, timeout, and official verifier output. AlphaDiana's `terminal_bench2`
scorer uses Harbor's numeric reward when present. A completed trial without a
finite numeric reward remains in the denominator as score zero, matching the
reference Meta-Harness TB2 parser; metadata records that the raw reward was not
observed. Metrics such as
`answer_length` run after scoring over deep copies and cannot alter the score or
the agent runtime.

The adapter records `agent.config.protocol` in both the response metadata and
artifact manifest. Every record contains `original_meta_harness_claim: false`
so downstream reports have an explicit guard against the invalid “faithful
adaptation” wording. Optional `candidate_name`, `parent_names`, and
`frontier_name` fields make lineage auditable without changing execution. The
legacy single-parent `parent_name` field remains accepted for compatibility.

## Training versus final evaluation

The optimizer must run training and final evaluation as separate AlphaDiana
runs (or separate output roots). Final rewards, trajectories, and jobs must not
be mounted into the proposer workspace. The completed legacy run withheld final
rewards but did not technically isolate task names; its result therefore keeps
that caveat. New optimizer runs should set `protocol.phase: final` and rely on
the outer proposer namespace plus private validation configuration for task-name
isolation. A `final` protocol declaration is intentionally fail-closed unless
both `validation_rewards_withheld: true` and `task_names_withheld: true` are
set. AlphaDiana itself is not a secrecy boundary for an optimizer that mounts
its configuration into the proposer process.

This separation preserves runtime state: Harbor creates a disposable task
container, Pi runs with ambient sessions/extensions/skills/context disabled,
and AlphaDiana metrics execute only after Harbor completion.
