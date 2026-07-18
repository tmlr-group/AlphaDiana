---
sidebar_position: 1
---

# Architecture Overview

AlphaDiana turns one YAML experiment config into task-level result records and a run-level report. The implementation is registry-driven: the runner imports built-in modules, resolves the configured benchmark, agent, sandbox, and scorer, and then executes one work item for each `(task_id, sample_index)` pair.

```text
YAML + -o overrides
        |
        v
ExperimentConfig -> ConfigValidator
        |
        v
Runner.setup()
  benchmark registry
  agent registry
  optional sandbox registry
  scorer registry
        |
        v
Runner.run()
  load tasks -> expand samples -> checkpoint filter
  -> dispatch -> agent.solve(task, sandbox)
  -> score -> ResultStore
        |
        v
task JSON + JSONL + artifacts -> RunSummary + report.md
```

Source references in these pages use file paths and symbols rather than mutable source line numbers. The central implementation is `alphadiana/engine/runner.py` (`Runner.setup`, `Runner.run`, and `_run_decodingtrust_process_shards`).

## Configuration boundary

`ExperimentConfig` lives in `alphadiana/engine/config/experiment_config.py`. Its top-level fields include:

- component blocks: `agent`, `benchmark`, `scorer`, and optional `sandbox`;
- execution controls: `max_concurrent`, `num_samples`, retries, `redo_all`, and isolation settings;
- DecodingTrust process isolation controls: `parallel_strategy` and `process_shards`;
- output and reporting controls: `output_dir`, `strict_report`, and metadata.

`num_samples` is a per-run choice. The engine does not impose an AIME-specific sample count; pass@k experiments must set the intended value in their own config.

## Dispatch and isolation

The normal dispatcher supports bounded in-process concurrency. Sandboxes may provide a fresh session per task, a pooled session, or a benchmark-specific task-bound session. `swebench_container` and `decodingtrust` both require the current task when creating a session.

DecodingTrust is a special cross-cutting path:

- `sandbox.name: decodingtrust` disables pooling and shared sessions;
- in-process task concurrency is lowered to one because DTAP uses process-wide state;
- `parallel_strategy: process_shards` with `process_shards > 1` launches isolated child processes, assigns tasks round-robin, gives each shard separate ports and identifiers, and merges the shard result stores into the parent run;
- OpenClaw selects its DTAP-native path with `agent.config.runtime_backend: decodingtrust_openclaw_cli`.

See [Sandboxes & Isolation](./sandboxes) for backend-specific boundaries.

## Checkpoint semantics

Resume is scorer-aware. Without `--redo-all`, only records for the current scorer whose inferred status is `valid_scored` count as complete. Other records are rerunnable.

Supported harness timeouts are a deliberate exception to the intuition that every timeout is incomplete. DirectLLM, OpenCode, OpenClaw, and ZeroClaw can return `finish_reason: timeout` with `score=0`, `correct=false`, timeout metadata, and `score_status: valid_scored`; these rows are checkpoint-complete. Loading also normalizes legacy rows that contain explicit timeout evidence to this scored-zero form.

Provider failures, context overflow, control-plane failures, verifier anomalies, heartbeat/session taint, and other non-timeout errors remain incomplete and rerunnable. TerminalBench-2 additionally requires actual verifier reward evidence; `skipped_duplicate` is valid only with `verifier_reward_observed=true` and normal score fields.

## Results and reporting

`ResultStore` persists one sample per task JSON file entry and one JSONL record per `(task_id, sample_index)`. Result records include the response, score, status, trajectories, metadata, and references to larger artifacts. Reports compute accuracy, mean score, Pass@k, Avg@k, completion/error counts, and per-category variants.

DecodingTrust reports also expose denominator-scoped task-success and attack-success counts and rates. See [Scoring & Results](./scoring-and-results).

## Terminology

- **Dashboard UI**: the React analysis application.
- **live status file**: `status/dashboard.txt` written during a run.
- **homepage Dashboard section**: the website landing-page section.
- **Observability & Proxies**: this documentation area, not a dashboard.

## Next steps

- [Engine & Runner](./engine-and-runner) — lifecycle, concurrency, checkpointing, and process sharding.
- [Registries](./registries) — the complete live component inventory.
- [Sandboxes & Isolation](./sandboxes) — session contracts and backend boundaries.
- [Scoring & Results](./scoring-and-results) — validity, storage, metrics, and sharing precautions.
- [Observability & Proxies](./observability) — proxy capture and trajectory preservation.
