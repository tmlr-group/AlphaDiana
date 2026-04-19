# AGENTS.md

Entry map for coding agents. Keep this file short.
Do not read the whole repo by default; expand context only as needed.

## Open what matches the task

- Setup / env / first run -> `README.md`, `docs/getting_started.md`, `docs/setup_detail.md`
- Benchmark smoke / pilot / support -> `context/current_eval_status.md`, `docs/benchmarks/README.md`, then the target benchmark doc
- SWE-bench Pro -> `docs/benchmarks/swebench-pro.md`
- Full runs -> `configs/full_runs/README.md` + the target config
- Results / dashboard -> `docs/dashboard.md`
- New milestone / reviewer evidence -> `context/README.md` + the matching `context/` folder
- Architecture / conventions -> `.planning/codebase/*.md`

Skip `docs/archive/` and `context/archive/` unless the task is explicitly historical.

## Repo roles

- `docs/` = user-facing runbooks, setup guides, and dashboards
- `context/` = reviewer-facing evidence, local validation notes, and dated support snapshots
- `.planning/` = active plans, architecture, research
- `configs/examples/` = smoke / debug configs
- `configs/full_runs/` = full-run entry points

## Hard rules

- Prove support with real runs, not config inspection.
- Use real APIs for benchmark / agent validation. Query the developer/user this information when empty.
- Inspect task-level results under `results/<run_id>/...`.
- For real runs, keep the raw shell log under `logs/<run_id>.log` and check that file when a run appears to stop silently.
- If unsure about repo/runtime behavior such as checkpointing, retries, or scoring semantics, verify it by reading the relevant code and, when cheap, by a minimal real run before assuming.
- When that verification yields a reusable repo fact, record it in `AGENTS.md` if it is broadly useful; otherwise record it in the relevant `context/*` note so it can be found later.
- `python -m alphadiana.cli run` resumes from checkpoint by default. Without `--redo-all`, completed task JSONs are skipped and only remaining tasks are evaluated.
- In `openclaw`, `agent.config.request_timeout` does not widen the streaming read timeout by itself. Unless `agent.config.stream_idle_timeout` is also set, the client still uses a 180s default stream-idle timeout.
- Make sure preserving intermediate artifacts for integrating new agents.
- Make sure agent running in the container runtime for integrating new agents.
- Never commit secrets or absolute local paths.

## Reporting

- Task status: `error`, `score=1`, `score=0`
- Trajectory status: `pass`, `abnormal`
- Benchmark-specific fields such as `reward`, patch files, or verifier outputs are supporting evidence, not the universal top-level status.

## Documentation contract

Any real experiment that changes support status, commands, caveats, or evidence must update docs in the same change:

- Any code change or experiment that changes observable support status, recommended commands, config semantics, caveats, or stored evidence must update `docs/*` and `context/*` in the same change.
- Keep these doc/context updates minimal and necessary. Do not churn unrelated files when the repo state has not changed.
- `docs/*` for user-facing commands, config semantics, expected outcomes, and caveats
- `context/<milestone>/*` for run IDs, evidence, and debug trail
- `context/README.md` when adding a new milestone folder
- `docs/benchmarks/README.md` when adding or changing a benchmark runbook
- `context/current_eval_status.md` when recommended paths or known limitations change

Docs and context must agree.

## PR contract

Make the PR understandable without private files. Include:

- the exact smoke / pilot commands used
- any config differences from `README.md`
- a concise local validation summary
- key run IDs, matrices, or task-level evidence

Do not paste raw shell dumps or inaccessible local paths into the PR body.

## Git hygiene

Before merge or rebase, fetch the latest `main` and inspect command output.
Do not claim sync succeeded if auth, permission, or network errors occurred.
Keep both `main` and your branch usable.

## Default loop

1. Read this file.
2. Open the relevant docs and configs.
3. Validate the command or config path, the environment.
4. Run the real smoke, pilot, or full run.
5. Inspect results, trajectories, and artifacts.
6. Update the necessary `docs/*` and `context/*` files when the repo state or support evidence changed.
7. Write a reviewer-readable summary.

## If this grows

Only add rules that matter in nearly every session.
Move benchmark-specific, procedural, or directory-local guidance to the matching doc, a nested `AGENTS.md` / `CLAUDE.md`, or a reusable skill / hook.
