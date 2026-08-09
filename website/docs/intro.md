---
sidebar_position: 1
slug: /intro
---

# Welcome to AlphaDiana

**AlphaDiana** is a *harness-aware* evaluation framework for reasoning agents on
verifiable tasks. It measures not just *which model* you run, but *which agent
scaffold (harness)* you wrap it in, because on the same benchmark the harness can
matter as much as the model itself.

## Why AlphaDiana?

A single LLM behaves very differently depending on the scaffold around it. The same
`Qwen3.5-27B` checkpoint can solve a competition problem when run as a raw
single-turn call and then fail the same problem when wrapped in a tool-using agent
that over-deliberates, or vice versa. AlphaDiana exists to make that gap measurable:

- **Decouple model from harness.** Every run names an *agent* (the harness) and a
  *benchmark* separately. Matched cells make harness-condition differences
  measurable, but the complete prompt, transport, runtime, and budget contract
  must be reported before interpreting a delta.
- **A first-class no-harness baseline.** The `direct_llm` agent is a single-turn
  chat with no tools and no multi-turn loop. It is the reference against which every
  harness is measured, and it surfaces the *agent scaffold tax*: the cases where the
  scaffold costs more than it adds.
- **Auditable isolation.** Supported tool-using paths can use task-scoped
  sandboxes, and every run records its realized `isolation_mode`.
  `strict_isolation` makes ROCK auto-create/predeploy failures fail closed; it
  does not place every harness in a sandbox by itself.

## What a run does

A run is one YAML config. The `Runner` orchestrates it end to end:

1. **Load tasks** from a benchmark (AIME, GPQA-Diamond, HLE, MMMU-Pro, SWE-bench,
   TerminalBench2, IMO-AnswerBench, or an inline `custom` problem
   set).
2. **Run an agent** on each task: the sandbox-free `direct_llm` baseline, or one
   of the `opencode` / `openclaw` / `zeroclaw` harnesses. Supported tool-using
   paths use task-scoped `local`, ROCK, Podman, or SWE task-container runtimes.
3. **Score** each answer against ground truth with a pluggable scorer
   (`numeric`, `math_verify`, `exact_match`, `llm_judge`, or a benchmark-specific one).
4. **Persist** one JSON record per `(task, sample)` to `results/<run_id>.jsonl`
   (plus per-task artifacts, trajectories, and logprob sidecars) and emit a report
   with accuracy, Pass@k, and Avg@k.

## Key ideas

- **Registry-driven.** Agents, benchmarks, sandboxes, and scorers are all resolved
  by string name from four registries. Adding a backend means registering it and
  importing it; there is no plugin auto-discovery to fight.
- **Checkpoint-resume off the result file.** Completion is inferred from the result
  JSONL itself (a scorer-matching record whose `score_status == valid_scored`).
  Provider/runtime failures and no-answer records remain retryable. Timeout-classified
  harness outcomes that are normalized to scored zero are valid scored records and
  are therefore checkpoint-complete. `--redo-all` bypasses the checkpoint.
- **Isolation modes.** A run records its `isolation_mode`
  (`shared_gateway`, `explicit_sandbox`, `auto_single_sandbox`, `predeployed_pool`,
  `fresh_predeployed_pool`, ...). Check this recorded value rather than inferring
  isolation from `strict_isolation` alone.
- **Observability.** Optional per-token logprob capture, normalized trajectories, and
  a two-tier `score_status` validity model that separates real model outcomes from
  infrastructure failures when computing metrics.
- **Evaluation axes.** Micro studies compare matched **Tool**, **Skill**, and
  **Memory** conditions. Because a condition can change both prompts and runtime
  behavior, its delta describes the intervention bundle rather than an isolated
  causal contribution.

## How it relates to training frameworks

AlphaDiana **evaluates**; it does not train. It runs inference-time agents and scores
them. This is the complement to RL/post-training systems such as AlphaApollo, which
can plug into AlphaDiana as one candidate agent and have its solves scored here.

## Where to go next

- **[Getting Started](./getting-started/)**: install, run your first benchmark, fix common issues.
- **[Concepts & Design](./concepts/)**: harness-aware evaluation, the scaffold tax, isolation and fairness.
- **[Architecture](./architecture/)**: the engine, registries, sandboxes, scoring, and observability proxies.
- **[Harnesses](./harnesses/)**: `direct_llm`, `opencode`, `openclaw`, `zeroclaw`, and skills.
- **[Benchmarks](./benchmarks/)**: the supported task sets and how to add one.
- **[Configuration](./configuration/)**: the YAML schema, CLI overrides, and run-id conventions.
- **[Dashboard](./dashboard)**: launch, monitor, browse, and compare local runs.
- **[Contributing](./contribution/)**: adding harnesses and benchmarks, engineering conventions.
