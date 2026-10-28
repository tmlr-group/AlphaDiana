# Welcome to AlphaDiana

**AlphaDiana** is an open-source measurement system for LLM-based agents on
verifiable reasoning tasks. It runs configured agents against benchmarks,
scores their outputs, and saves execution evidence for analysis.

## Choose your starting point

| Goal | Guide |
| --- | --- |
| Install and run a one-task DirectLLM baseline | [Installation](getting-started/installation.md) → [Quick Start](getting-started/quick-start.md) |
| Run the README's OpenCode Python example | [Python API and analysis](getting-started/python-api.md) |
| Choose a native runtime or benchmark | [Harnesses](harnesses/) and [Benchmarks](benchmarks/) |
| Reopen, rescore, and analyze saved evidence | [Python API](getting-started/python-api.md#reopen-and-rescore) and [Scoring & Results](architecture/scoring-and-results.md) |
| Enable model-based action and failure annotations | [Optional model judgments](getting-started/python-api.md#optional-model-judgments) |
| Browse local runs and trajectories | [Dashboard](dashboard.md) |

## Why AlphaDiana?

AlphaDiana connects final scores with trajectories, failures, and resource use.
Researchers can compare complete agents under recorded conditions, inspect
their execution, and compare tool, skill, or memory settings. The saved
task, model, environment, budget, and evidence coverage support those comparisons.

## What a run does

A run is one YAML config. The `Runner` orchestrates it end to end:

1. **Load tasks** from a benchmark (AIME, GPQA-Diamond, HLE, MMMU-Pro, SWE-bench, TerminalBench2, IMO-AnswerBench, or an inline `custom` problem set).
2. **Run an agent** on each task: the sandbox-free `direct_llm` baseline, or a native harness such as OpenCode, OpenClaw, ZeroClaw, SWE-Agent, or Terminus2. Supported tool-using paths use task-scoped `local`, ROCK, Podman, or SWE task-container runtimes.
3. **Score** each answer against ground truth with a pluggable scorer (`numeric`, `math_verify`, `exact_match`, `llm_judge`, or a benchmark-specific one).
4. **Persist** one JSON record per `(task, sample, attempt)` to `results/<run_id>.jsonl` (plus per-task artifacts, trajectories, and logprob sidecars) and emit a report with accuracy, Pass@k, and Avg@k.

## Key ideas

- **Registry-driven.** Agents, benchmarks, sandboxes, and scorers are all resolved by string name from component registries. Adding a backend means registering it and importing it; there is no plugin auto-discovery to fight.
- **Checkpoint-resume off the result file.** Completion is inferred from the result JSONL itself (a scorer-matching record whose `score_status == valid_scored`). Provider/runtime failures and no-answer records remain retryable. Timeout-classified harness outcomes that are normalized to scored zero are valid scored records and are therefore checkpoint-complete. `--redo-all` bypasses the checkpoint.
- **Isolation modes.** A run records its `isolation_mode` (`shared_gateway`, `explicit_sandbox`, `auto_single_sandbox`, `predeployed_pool`, `fresh_predeployed_pool`, ...). Check this recorded value rather than inferring isolation from `strict_isolation` alone.
- **Observability.** Optional per-token logprob capture, normalized trajectories, and a two-tier `score_status` validity model that separates real model outcomes from infrastructure failures when computing metrics.
- **Evaluation axes.** The paper micro study compares matched **Tool** and **Skill** conditions; **Memory** is documented as a separate framework extension. Because a condition can change both prompts and runtime behavior, its delta describes the intervention bundle rather than an isolated causal contribution.

## How it relates to training frameworks

AlphaDiana **evaluates**; it does not train. It runs inference-time agents and scores them. This is the complement to RL/post-training systems such as AlphaApollo, which can plug into AlphaDiana as one candidate agent and have its solves scored here.

## Where to go next

- **[Getting Started](./getting-started/)**: install, run your first benchmark, fix common issues.
- **[Concepts & Design](./concepts/)**: harness-aware evaluation, the scaffold tax, isolation and fairness.
- **[Architecture](./architecture/)**: the engine, registries, sandboxes, scoring, and observability proxies.
- **[Harnesses](./harnesses/)**: `direct_llm`, `opencode`, `openclaw`, `zeroclaw`, and skills.
- **[Benchmarks](./benchmarks/)**: the supported task sets and how to add one.
- **[Configuration](./configuration/)**: the YAML schema, CLI overrides, and run-id conventions.
- **[Dashboard](./dashboard.md)**: launch, monitor, browse, and compare local runs.
- **[Contributing](./contribution/)**: adding harnesses and benchmarks, engineering conventions.

## Python API and analysis

See [Python API and analysis](getting-started/python-api.md) for execution, rescoring, extensible analysis, and vector figure exports.

See [Package paths](../alphadiana/README.md#path-migration) and [Analysis imports](../alphadiana/analysis/README.md#import-migration) for import migrations.
