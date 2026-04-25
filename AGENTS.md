# AGENTS.md

Entry map for coding agents. Keep this file short.
Do not read the whole repo by default; expand context only as needed.

## Open what matches the task

- Setup / env / first run -> `README.md`, `docs/getting_started.md`, `docs/setup_detail.md`
- Benchmark smoke / pilot / support -> `context/current_eval_status.md`, `docs/benchmarks/README.md`, then the target benchmark doc
- SWE-bench Pro -> `docs/benchmarks/swebench-pro.md`
- Full runs -> `context/current_eval_status.md`, `configs/full_runs/README.md`, `context/phase12-harness-logprob-smokes/full_run_context/README.md`, then the target config
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
- Current task result files under `results/<run_id>/.../tasks/*.json` are sample lists even when `num_samples=1`; read `data[0]` before checking fields such as `score_status`, `error`, or `trajectory`.
- For real runs, keep the raw shell log under `logs/<run_id>.log` and check that file when a run appears to stop silently.
- If unsure about repo/runtime behavior such as checkpointing, retries, or scoring semantics, verify it by reading the relevant code and, when cheap, by a minimal real run before assuming.
- When that verification yields a reusable repo fact, record it in `AGENTS.md` if it is broadly useful; otherwise record it in the relevant `context/*` note so it can be found later.
- `python -m alphadiana.cli run` resumes from checkpoint by default. Without `--redo-all`, completed task JSONs are skipped and only remaining tasks are evaluated.
- Resume semantics are scorer-aware: only scorer-matching records with `score_status=valid_scored` count as completed checkpoint artifacts. `score=None`, preserved failures, verifier anomalies, or old results from a different scorer do not qualify.
- On current main, OpenCode provider/tool-choice failures are preserved as error records rather than being parsed into synthetic answers. Historical April 22 `qwen3vl` artifacts with `predicted="400"` are pre-fix audit evidence only.
- For local vLLM-backed runs, do not insert request-side token-budget shims or prompt truncation just to avoid context overflow. Call the configured provider directly and preserve `ContextOverflowError` / `VLLMValidationError` as provider-side task errors.
- `imo_answerbench` must use `scorer.name: imo_verify`. The validator rejects `math_verify` for this benchmark, and historical `math_verify` IMO results should be treated as audit-only evidence.
- For `terminal_bench2`, treat `metadata.verifier_status="skipped_duplicate"` as valid only when the task also records `metadata.verifier_reward_observed=true` and normal score fields. Current main leaves `metadata.reward=null` when no reward was actually observed.
- On OpenRouter `Qwen/Qwen3.5-27B`, short DirectLLM canaries may still consume large hidden reasoning budgets unless `agent.config.extra_body.reasoning.enabled=false` is set. Use that override for terse transport probes when you do not want invisible reasoning tokens to dominate latency.
- In `openclaw`, `agent.config.request_timeout` does not widen the streaming read timeout by itself. Unless `agent.config.stream_idle_timeout` is also set, the client still uses a 180s default stream-idle timeout.
- In `openclaw`, use `agent.config.stream_total_timeout` when validating local streaming providers. A stream that keeps producing chunks can avoid idle timeout indefinitely; current OpenClaw raises a preserved timeout error when the total stream budget is exceeded.
- For long local-Qwen thinking-on smokes, size timeouts from token throughput. This host can be around 30 tokens/sec, so a 25K output cap can need 15 minutes plus agent/tool overhead; a 64K cap can need 35+ minutes.
- For long local-Qwen thinking-on smokes from agent-operated shells, run the foreground command through `tee` and add a simple heartbeat line every minute. Background `nohup ... &` jobs can be reaped by the shell runner before writing any log. If the outer process is killed, completed task JSONs remain resumable by checkpoint, but in-flight streaming tasks may have no task JSON or sidecar.
- For AIME single-task smokes, prefer `benchmark.config.dataset_index` / `dataset_indices` over split slicing when using example configs that may already set `benchmark.config.max_tasks`. Current AIME loader avoids appending `[:max_tasks]` to sliced splits, but `dataset_index=16` is the clearest way to target AIME 2026 `aime_17`.
- On local `Qwen/Qwen3.5-27B` OpenClaw/ZeroClaw logprob pilots, set `agent.config.enable_thinking=false` when the goal is transport validation. AlphaDiana injects `chat_template_kwargs.enable_thinking=false` through the logprob proxy; this prevents hidden reasoning from dominating, but IMO can still produce long visible derivations under a 64K output budget.
- On local `Qwen/Qwen3.5-27B` AIME 2026 `aime_17` with `enable_thinking=true` and `max_tokens=25000`, OpenCode and ZeroClaw can reach the 25K cap. OpenCode exits normally with captured logprobs; current ZeroClaw may preserve `runtime_error` / `empty_response` because the CLI emits no assistant text, but AlphaDiana still stores float/int16 logprob sidecars and sandbox artifacts.
- On OpenCode logprob-enabled local-Qwen pilots, `agent.config.enable_thinking=true` is injected into the actual `/v1/chat/completions` request body as `chat_template_kwargs.enable_thinking=true`; verify it from `metadata.logprob_proxy_request_overrides` and the preserved `opencode_config.json` artifact, not only from the YAML override.
- On shared hosts, treat ROCK isolation as a precondition for any ROCK-backed run: use a checkout-specific conda env, checkout-specific ports / instance name / Redis container / short `RAY_TMPDIR`, and run `python -m alphadiana.cli env` before launch. A healthy admin/proxy on the configured ports is not sufficient if those ports belong to another checkout.
- All ROCK/OpenClaw/ZeroClaw startup paths (`scripts/start_openclaw.sh`, `scripts/start_zeroclaw.sh`, `scripts/quickstart.sh`, `scripts/setup_alphadiana_rock.sh`) run `python3 scripts/security_guard.py --check` as a preflight and abort on HIGH/CRITICAL findings. Do not bring up Redis/Ray/ROCK by calling `docker run redis`, `ray start`, or `run_rock_admin_local.py` directly without first running the guard; prior regressions produced 0.0.0.0 Redis + Ray GCS bindings on shared hosts exactly because `quickstart.sh` historically skipped this check. Use `SECURITY_GUARD_BYPASS=1` only with explicit authorization.
- In agent-operated shells, `bash scripts/quickstart.sh` is a bootstrap helper, not a daemon supervisor. If the shell runner reaps background children when the command exits, keep `ray start --head --block`, ROCK admin, and ROCK proxy in dedicated long-lived PTY sessions for the duration of the evaluation instead of assuming the quickstart-spawned daemons will survive.
- When starting ROCK admin manually, point it at the already-running checkout-owned Ray cluster via a runtime `ROCK_CONFIG` whose `ray.address` is the active `ROCK_RAY_PORT`. Without that explicit address, admin can fall back to a local default Ray init and later die with `GCS unavailable` even though the checkout's Ray head is healthy.
- In `swebench_docker`, reuse local base/runtime images when they already exist; do not force `docker pull` before building the derived runtime image unless the source image is actually missing locally.
- `scripts/.alphadiana_env` is local ignored state, not a repo truth source. If it points at a foreign checkout or missing ROCK root, treat it as stale and regenerate it with `bash scripts/setup_alphadiana_rock.sh` or `bash scripts/quickstart.sh` rather than trusting the old values.
- `source scripts/activate.sh` also sources the repo `.env`. Any later wrapper that sets provider vars with shell defaults such as `OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://localhost:8011/v1}"` will keep the inherited `.env` or parent-shell values instead of switching to local vLLM. For one-off local-model pilots, set the target `OPENAI_*` values explicitly after `source scripts/activate.sh` or `unset OPENAI_BASE_URL OPENAI_API_KEY OPENAI_MODEL_NAME` first, then confirm the effective provider in the launched `python -m alphadiana.cli run ...` command or the worker process environment.
- In a checkout without a bootstrapped checkout-local conda env, `source scripts/activate.sh` can print warnings and still return successfully in normal shell mode, but it may abort a `set -euo pipefail` launcher. For one-off OpenCode local-vLLM smokes, either bootstrap the env first or source it outside strict mode, then set `OPENAI_*` explicitly.
- On local `Qwen/Qwen3.5-27B` pilots, `nvidia-smi` showing the vLLM workers resident on GPUs 0/1 but `GPU-Util=0%` does not imply the pilot is still waiting to reach the model. Check the vLLM server log first: repeated `VLLMValidationError` request rejections or immediate empty-SSE failures can keep the model loaded in memory while doing effectively no decode work. In that state, treat the run as broken rather than as a long warmup.
- On local `Qwen/Qwen3.5-27B` `SWE-bench Verified` runs, current `zeroclaw 0.6.9` still logs `Unknown config key ignored: "provider_max_tokens"` and can fail immediately with `Request exceeds model context window and cannot be reduced further` even when `max_tool_iterations=12`, `reasoning_enabled=true`, and `reasoning_effort=high` are set. Treat that knob as non-effective on this path and expect a preserved `provider_error` record rather than a recovered patch.
- For task-container OpenClaw runtimes such as `openclaw_swe_bench.yaml`, do not default the inner gateway to `gateway.bind=custom` with `customBindHost=127.0.0.1`. The container-local gateway process can look healthy while every host-published `http://127.0.0.1:<port>/v1/*` probe fails with `connection reset by peer`. Default to a non-loopback bind for published-port access and only use a custom bind host when explicitly requested.
- For ROCK-backed OpenClaw/ZeroClaw runtimes, resolve published sandbox ports from the admin `get_status` API, not from a sandbox SDK client that has already been repointed at the proxy URL. Otherwise `published_base()` can 404 on proxy-side `get_status`, silently fall back to `/proxy/v1`, and hide whether the host-published gateway path is actually working.
- If `ray start --head` aborts with `Session name ... does not match persisted value ... Perhaps there was an error connecting to Redis`, treat it as stale state in the checkout-isolated Redis / `RAY_TMPDIR`, not as a model or ROCK code bug. Recreate the checkout's Redis container and clear that checkout's `RAY_TMPDIR` before retrying Ray.
- If a local ROCK-backed path logs `ROCK proxy failed` or `http proxy failed` after roughly two minutes, suspect the local ROCK proxy implementation before blaming the external model API. In this repo, older `ref/ROCK` copies hardcoded a `120s` timeout in `rock/sandbox/service/sandbox_proxy_service.py:http_proxy()`.
- Current main generic `agent.name: zeroclaw` is sandbox-only: it runs the stock `zeroclaw agent` CLI inside the live ROCK sandbox for both text and image-backed benchmark tasks. Image attachments are uploaded into the workspace and referenced from the prompt via `[IMAGE:<absolute sandbox path>]`; the transport marker is `metadata.transport=zeroclaw_cli_sandbox`.
- Current main no longer supports the old generic ZeroClaw path-selection knobs such as `agent.config.disable_tools`, `use_gateway_in_sandbox`, `multimodal_via_proxy`, `gateway_api_base`, or `gateway_pool`. Historical April 22 `disable_tools` / `zeroclaw_sandbox_native_vision_proxy` artifacts are audit-only evidence from the pre-refactor wrapper.
- Before uploading failure-path results outside the checkout, verify sandbox metadata is sanitized. Historical local April 22 ZeroClaw smoke artifacts can contain provider API keys inside `sandbox_metadata.command_history.command`; current main redacts those env assignments before persisting failure records.
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
