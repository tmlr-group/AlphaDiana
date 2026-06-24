---
sidebar_position: 10
---

# Repository File Map

A quick orientation to where things live. Paths are relative to the repository root.

## The `alphadiana/` package

| Path | What it holds |
|---|---|
| `alphadiana/cli.py` | The `alphadiana` Click CLI: `run`, `validate`, `report`, `batch`, `env`, `list-benchmarks`. |
| `alphadiana/engine/runner.py` | `Runner`, the top-level orchestrator (load tasks, dispatch, score, persist, report). |
| `alphadiana/engine/config/experiment_config.py` | `ExperimentConfig` dataclass + `from_yaml` (env expansion, override merge, agent env defaults). |
| `alphadiana/engine/config/validator.py` | `ConfigValidator`: hard errors vs warnings before a run starts. |
| `alphadiana/engine/task_dispatcher.py` | Sequential vs `ThreadPoolExecutor` dispatch, task retries with backoff. |
| `alphadiana/engine/sandbox/` | Sandbox backends: `base.py` (ABCs), `local.py`, `rock.py`, `podman`, `swebench_container`, plus `pool.py`. |
| `alphadiana/harness/` | The agents (harnesses). See below. |
| `alphadiana/benchmarks/` | Benchmark loaders. See below. |
| `alphadiana/scorer/` | Generic scorers: `exact_match`, `numeric`, `math_verify`, `llm_judge` (+ base/registry). |
| `alphadiana/analysis/` | Result store, report generation, reliability, trajectory/behavioral metrics, logprob tooling. |
| `alphadiana/utils/` | Shared helpers (`math_answer`, etc.). |
| `alphadiana/results/` | Empty package; the real `ResultStore` lives in `alphadiana/analysis/io/result_store.py`. |

## Harnesses (`alphadiana/harness/`)

| Path | Agent (`agent.name`) |
|---|---|
| `harness/base.py` | `Agent` ABC + `AgentResponse` dataclass. |
| `harness/registry.py` | `AgentRegistry` (string-name resolution). |
| `harness/direct_llm.py` | `direct_llm`: single-turn no-harness baseline. |
| `harness/opencode/` | `opencode`: wraps the `opencode` CLI (host/docker/podman controllers, session/compact/freeze). |
| `harness/openclaw/` | `openclaw`: ROCK-sandbox gateway agent with lancedb memory. |
| `harness/zeroclaw/` | `zeroclaw`: native `zeroclaw` Rust CLI in a ROCK sandbox, sqlite/vector memory. |
| `harness/proxies/` | `logprob_proxy`, `tool_filter_proxy`, `harness_strip`, `preservation`. |
| `harness/skills/` | Skill bundles (`advanced-maths`, `anthropic-bundle`) mounted into sandboxes. |

## Benchmarks (`alphadiana/benchmarks/`)

`base.py` defines `BenchmarkTask(task_id, problem, ground_truth, metadata, attachments)`
and the `Benchmark` ABC; `registry.py` holds `BenchmarkRegistry`. Each subdirectory is
one benchmark, registered by name: `aime`, `gpqa_diamond`, `hle`, `mmmu_pro`,
`swe_bench`, `swebench_pro_os`, `terminal_bench2`, `custom`, `imo_answerbench`,
`external_benchmark`.

## Outside the package

| Path | What it holds |
|---|---|
| `configs/` | Run configs. `configs/examples/` are smoke/debug (one pinned task); `configs/full_runs/` are full benchmark runs. |
| `configs/schema.yaml` | The authoritative YAML field reference. |
| `docs/` | This documentation. |
| `scripts/` | Operational scripts (service bring-up, capture, guards). |
| `installation.sh` | Environment setup. |
