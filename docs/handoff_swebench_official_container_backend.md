# SWE-bench Official Container Backend Handoff

This document is a handoff for a fresh context window. The goal is to replace the current ROCK-oriented OpenClaw sandbox path with an official SWE-bench instance-container backend.

## Copy-Paste Prompt For The New Context

Implement a new execution path in this repo that runs OpenClaw and similar agents directly inside official SWE-bench instance containers instead of using ROCK or the existing AlphaDiana sandbox workflow.

Requirements:

- Do not extend the current ROCK/OpenClaw sandbox path.
- Do not depend on `openclaw_deploy`, `rock_agent_config_path`, `openclaw_config_path`, or any ROCK admin/proxy service.
- Use official SWE-bench instance images as the execution environment for the agent.
- Use the `swebench` Python package to resolve/build the task environment instead of hardcoding Docker image names or layout assumptions.
- Keep the benchmark dataset loader based on `SWE-bench/SWE-bench_Verified`.
- Keep the result format compatible with the current AlphaDiana result store.
- Prefer minimal, local changes over a large refactor, but change interfaces where needed if the current abstractions are too ROCK-specific.

Deliverables:

1. A new sandbox backend, e.g. `swebench_container`, that launches one official SWE-bench instance container per task.
2. A task-aware sandbox/session lifecycle so the backend can create the correct instance container from `task.metadata`.
3. An OpenClaw runtime path that installs or reuses OpenClaw inside that container and talks to it without ROCK.
4. A working example config for SWE-bench that uses the new backend and does not mention ROCK.
5. Tests for the new task-aware sandbox lifecycle and any non-network parsing logic.
6. Updated docs explaining the new path and explicitly stating that ROCK is not used for SWE-bench container runs.

Do not try to preserve the old `configs/examples/openclaw_swe_bench.yaml` semantics if they are ROCK-specific. Replace them with a clean container-native config.

## Why This Change Is Needed

Current state:

- `alphadiana/agent/openclaw.py` assumes an OpenAI-compatible gateway and heavily prefers ROCK auto-deploy.
- `alphadiana/agent/openclaw_runtime.py` is specifically a ROCK runtime bootstrapper.
- `alphadiana/runner/runner.py` contains special-case logic for predeploying ROCK sandboxes and OpenClaw gateway pools.
- `configs/examples/openclaw_swe_bench.yaml` still points at a ROCK image and ROCK config files.
- `alphadiana/benchmark/swe_bench.py` and `alphadiana/scorer/swe_bench_scorer.py` already exist, so the repo has partial SWE-bench support, but the execution layer is still wrong for the desired architecture.

Target state:

- SWE-bench tasks run in official SWE-bench instance containers.
- OpenClaw is started inside those containers.
- AlphaDiana talks to the agent running in the task container, not to a ROCK sandbox.

## Important Structural Constraint

The current sandbox abstraction is not sufficient:

- `Sandbox.create_session()` takes no task argument.
- Official SWE-bench instance containers are task-specific, because the repo, commit, and environment come from `task.metadata`.

So the new context should explicitly change the sandbox/session lifecycle. A minimal viable approach:

- Change `Sandbox.create_session()` to `create_session(task: BenchmarkTask | None = None)`.
- Update existing backends to accept the new optional argument and ignore it where irrelevant.
- Update `Runner` so task-specific backends receive the task when creating a session.

If a capability flag is cleaner, add one. Example:

- `Sandbox.requires_task_on_create() -> bool`
- `Sandbox.supports_shared_session() -> bool`
- `Sandbox.supports_pooling() -> bool`

But do not keep pretending all backends are task-agnostic. The SWE-bench container backend is not.

## Files That Matter

- `alphadiana/sandbox/base.py`
- `alphadiana/sandbox/registry.py`
- `alphadiana/runner/runner.py`
- `alphadiana/agent/openclaw.py`
- `alphadiana/agent/openclaw_runtime.py`
- `alphadiana/benchmark/swe_bench.py`
- `alphadiana/scorer/swe_bench_scorer.py`
- `configs/examples/openclaw_swe_bench.yaml`
- `alphadiana/config/validator.py`
- `configs/schema.yaml`
- `pyproject.toml`

## Proposed Implementation Shape

### 1. Add a new sandbox backend

Create `alphadiana/sandbox/swebench_container.py`.

Responsibilities:

- Use the `swebench` package to create or resolve the official environment for a single instance.
- Start one long-lived container per task instance.
- Expose `execute`, `upload`, `download`, `read_text`, `close`, `reset`, and `metadata`.
- Store useful metadata such as:
  - `instance_id`
  - `repo`
  - `base_commit`
  - `container_id`
  - `container_name`
  - `image_name`
  - detected repo/workdir path

Implementation notes:

- Do not hardcode official image naming conventions.
- Resolve image/build context through `swebench` harness helpers such as `make_test_spec` and related harness utilities.
- Keep `rm_image=False` by default so the official image cache survives repeated runs.
- Prefer `docker` Python SDK for container lifecycle and file copy where practical.
- The container should remain alive for the duration of a single task and then be destroyed.

### 2. Make sandbox sessions task-aware

Update the abstraction and runner so the backend can create a task-specific container.

Runner changes should include:

- Do not pre-create a shared session for `swebench_container`.
- Do not create a `SandboxPool` for `swebench_container`.
- Create and close the session inside `solve_fn` for each task.
- Pass the current task into `create_session(task=task)`.

This should be implemented cleanly, not as a one-off branch only for one config file.

### 3. Add a container-native OpenClaw runtime path

Do not reuse the ROCK runtime bootstrap as-is.

Recommended shape:

- Add a new runtime helper, e.g. `alphadiana/agent/openclaw_container_runtime.py`.
- It should:
  - upload the OpenClaw config into the task container
  - install OpenClaw if missing, or reuse a preinstalled binary if available
  - start the gateway inside the container
  - expose a reachable API base for the host-side `OpenClawAgent`
  - collect logs and workspace artifacts from the container

Practical transport options:

- Preferred: publish a host port for the task container and let `OpenClawAgent` call `http://127.0.0.1:<mapped_port>/v1`.
- Acceptable: keep all gateway traffic inside `docker exec` and build a tiny adapter, but this is usually more invasive.

Do not keep references to:

- ROCK admin URL
- ROCK proxy URL
- sandbox IDs derived from ROCK paths
- `rock_agent_config_path`
- `rock_image`

The agent config for this path should look container-native, not ROCK-native.

### 4. Simplify `OpenClawAgent` selection logic

`alphadiana/agent/openclaw.py` currently mixes:

- direct gateway mode
- ROCK auto-deploy mode
- ROCK gateway pool mode

Add a clean branch for the new backend instead of threading more ROCK logic into it.

Suggested config keys for the new path:

- `runtime: "swebench_container"`
- `container_gateway_port: 8000`
- `container_gateway_host: "127.0.0.1"`
- `gateway_token`
- `model`
- `request_timeout`
- `max_attempts`
- `openclaw_config_path`
- `install_openclaw_command`

If the existing `openclaw` agent becomes too tangled, split out a dedicated agent implementation such as `openclaw_container`. That is acceptable if it keeps the design cleaner.

### 5. Replace the SWE-bench example config

Replace `configs/examples/openclaw_swe_bench.yaml` with a container-native version.

It should roughly look like:

```yaml
run_id: "openclaw-swe-bench-verified"

agent:
  name: openclaw
  version: "2026.x"
  config:
    runtime: "swebench_container"
    openclaw_config_path: "openclaw_deploy/openclaw.json"
    container_gateway_port: 8000
    gateway_token: "OPENCLAW"
    model: "openclaw"
    max_tokens: 8192
    request_timeout: 3600
    max_attempts: 3

benchmark:
  name: swe_bench
  config:
    dataset: "SWE-bench/SWE-bench_Verified"
    split: "test"
    max_tasks: 1

sandbox:
  name: swebench_container
  config:
    namespace: "swebench"
    force_rebuild: false
    keep_container: false
    keep_logs: true
    install_openclaw_command: "npm install -g openclaw@2026.3.7"

scorer:
  name: swe_bench
  config:
    timeout: 1800
    cache_level: "env"
    namespace: "swebench"
    force_rebuild: false
    log_dir: "./swe_bench_logs"

max_concurrent: 1
output_dir: "./results"
```

The exact field names can differ, but the config must not mention ROCK.

### 6. Update config validation

`alphadiana/config/validator.py` currently validates API agents in a way that assumes either:

- `api_base`, or
- ROCK auto-deploy config

That must be expanded to support the new container runtime. For example:

- if agent is `openclaw` and `runtime == "swebench_container"`, require `sandbox.name == "swebench_container"` and `openclaw_config_path`
- do not require ROCK config in that case

### 7. Update dependencies

`pyproject.toml` currently does not declare the packages required by the SWE-bench scorer/backend.

Add at least:

- `docker`
- `swebench`

Place them in the appropriate extras and, if justified, in `all`.

## Scope Boundaries

Do not do these in the first pass:

- A giant agent abstraction rewrite
- Dashboard UX changes beyond keeping compatibility
- Cloud execution support
- Multi-container orchestration for one task

Do these:

- One container per task
- One OpenClaw runtime per task container
- Sequential execution first
- Concurrency only after the basic path is correct

## Testing Expectations

Add unit tests where feasible for:

- task-aware session creation plumbing
- config validation for the new runtime
- parsing / normalization helpers that do not require Docker

If integration tests are practical, add a gated test that requires Docker and skips otherwise.

Minimum manual validation target:

1. `alphadiana validate configs/examples/openclaw_swe_bench.yaml`
2. one-task smoke run on a SWE-bench Verified instance
3. confirm result JSONL is written
4. confirm the agent actually ran inside the official instance container, not ROCK
5. confirm the scorer still runs

## Acceptance Criteria

The work is complete when all of the following are true:

- A SWE-bench run can be started without any ROCK service running.
- The active sandbox backend is `swebench_container`.
- The task container comes from official SWE-bench harness resolution, not a custom repo cache flow.
- OpenClaw runs inside the task container and produces a patch.
- The result store records container metadata and artifacts.
- The example config and docs no longer instruct users to use ROCK for SWE-bench runs.

## Notes For The Implementer

- There is already partial SWE-bench support in:
  - `alphadiana/benchmark/swe_bench.py`
  - `alphadiana/scorer/swe_bench_scorer.py`
  - patch extraction logic in `alphadiana/agent/openclaw.py`
- The main design pressure is not the dataset or scorer. It is the execution backend and lifecycle.
- Keep the implementation narrow and explicit. Avoid mixing the new container-native path back into the ROCK auto-deploy branches more than necessary.
