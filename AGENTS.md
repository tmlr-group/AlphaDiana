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

## Context rules

- `context/current_eval_status.md` is the repo-level support truth source. Treat similarly named files inside `context/<workstream>/` as local historical or workstream-scoped status unless the repo-level snapshot explicitly points to them.
- Add any new long-lived top-level `context/` entry point to `context/README.md`. If a note is only useful for one workstream or PR, keep it inside that workstream folder instead of adding another top-level snapshot.
- Put new material under `context/`, not `contexts/`.
- In committed `docs/*` and `context/*`, prefer repo-relative paths, run IDs, and filenames. Do not commit absolute local worktree paths as durable references.
- When answering whether a path is "ready" or "supported", separate current repo-level status from historical evidence, and say when the referenced result artifacts are no longer present in the current checkout.
- When you add or materially change a doc/context entry point, update the relevant index files in the same change. Typical indexes are `docs/README.md`, `docs/benchmarks/README.md`, `context/README.md`, and the root `README.md` documentation table.

## Hard rules

- Prove support with real runs, not config inspection.
- Use real APIs for benchmark / agent validation. Query the developer/user this information when empty.
- Inspect task-level results under `results/<run_id>/...`.
- For real runs, keep the raw shell log under `logs/<run_id>.log` and check that file when a run appears to stop silently.
- If unsure about repo/runtime behavior such as checkpointing, retries, or scoring semantics, verify it by reading the relevant code and, when cheap, by a minimal real run before assuming.
- When that verification yields a reusable repo fact, record it in `AGENTS.md` if it is broadly useful; otherwise record it in the relevant `context/*` note so it can be found later.
- `python -m alphadiana.cli run` resumes from checkpoint by default. Without `--redo-all`, completed task JSONs are skipped and only remaining tasks are evaluated.
- Resume semantics are scorer-aware: only scorer-matching records with `score_status=valid_scored` count as completed checkpoint artifacts. `score=None`, preserved failures, verifier anomalies, or old results from a different scorer do not qualify.
- `imo_answerbench` must use `scorer.name: imo_verify`. The validator rejects `math_verify` for this benchmark, and historical `math_verify` IMO results should be treated as audit-only evidence.
- On OpenRouter `Qwen/Qwen3.5-27B`, short DirectLLM canaries may still consume large hidden reasoning budgets unless `agent.config.extra_body.reasoning.enabled=false` is set. Use that override for terse transport probes when you do not want invisible reasoning tokens to dominate latency.
- In `openclaw`, `agent.config.request_timeout` does not widen the streaming read timeout by itself. Unless `agent.config.stream_idle_timeout` is also set, the client still uses a 180s default stream-idle timeout.
- On shared hosts, treat ROCK isolation as a precondition for any ROCK-backed run: use a checkout-specific conda env, checkout-specific ports / instance name / Redis container / short `RAY_TMPDIR`, and run `python -m alphadiana.cli env` before launch. A healthy admin/proxy on the configured ports is not sufficient if those ports belong to another checkout.
- If a local ROCK-backed path logs `ROCK proxy failed` or `http proxy failed` after roughly two minutes, suspect the local ROCK proxy implementation before blaming the external model API. In this repo, older `ref/ROCK` copies hardcoded a `120s` timeout in `rock/sandbox/service/sandbox_proxy_service.py:http_proxy()`.
- Make sure preserving intermediate artifacts for integrating new agents.
- Make sure agent running in the container runtime for integrating new agents.
- Never commit secrets or absolute local paths.

## Execution style

- State assumptions and ambiguities before implementing. If multiple plausible interpretations exist, surface them instead of choosing silently.
- If relevant context still leaves the task unclear, ask instead of guessing.
- Prefer the simplest solution that fully solves the request. Avoid speculative abstractions, unused configurability, and impossible-scenario handling.
- Make surgical changes only. Match local style, do not refactor adjacent code, and only remove unused code that your change created.
- Turn non-trivial tasks into verifiable goals with a brief plan and explicit checks. For fixes or behavior changes, prefer a reproducer or test before and after when practical.

## Reporting

- Task status: `error`, `score=1`, `score=0`
- Trajectory status: `pass`, `abnormal`
- Benchmark-specific fields such as `reward`, patch files, or verifier outputs are supporting evidence, not the universal top-level status.

## Documentation contract

Any real experiment that changes support status, commands, caveats, or evidence must update docs in the same change:

- Any code change or experiment that changes observable support status, recommended commands, config semantics, caveats, or stored evidence must update `docs/*` and `context/*` in the same change.
- Keep these doc/context updates minimal and necessary. Do not churn unrelated files when the repo state has not changed.
- Index updates are part of the doc change, not optional follow-up cleanup.
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
