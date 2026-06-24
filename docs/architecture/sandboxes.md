---
sidebar_position: 4
---

# Sandboxes & Isolation

A sandbox is the task-scoped runtime an agent uses while solving a benchmark
task. AlphaDiana drives every backend through one pair of abstract base
classes, so the [engine and runner](./engine-and-runner) can treat a local
subprocess, a ROCK container, a Podman container, and an official SWE-bench
image identically. Sandbox code lives under
`alphadiana/engine/sandbox/` (`base.py`, `local.py`, `rock.py`, `podman.py`,
`swebench_container.py`, `pool.py`, `registry.py`).

This page describes the abstraction, the backends, the ROCK lifecycle, the
concurrency pool, and how the runner chooses an isolation mode (including the
special path for OpenClaw). For the reviewer-facing, paper-safe framing of what
"isolation" does and does not claim, see
[Isolation and fairness](../concepts/isolation-and-fairness).

## The abstraction

Two ABCs in `alphadiana/engine/sandbox/base.py` define the contract.

`Sandbox` is the provider. It exposes a `name`, a `setup(config)` that reads the
sandbox config block, a `create_session()` that returns a live session, and a
`teardown()` (no-op by default) for provider-level cleanup.

`SandboxSession` is a single live environment. Its surface is intentionally
small:

| Method | Purpose |
|---|---|
| `session_id` | Unique id for the session. |
| `execute(command) -> ExecutionResult` | Run a shell command; returns `exit_code`, `stdout`, `stderr`, `wall_time_sec`. |
| `upload(filename, content: bytes)` | Write a file into the sandbox. |
| `download(filename) -> bytes` | Read a file back out (`read_text` decodes for convenience). |
| `reset()` | Reset state between tasks without tearing down. Default is a no-op. |
| `close()` | Close and clean up the session. |
| `metadata() -> dict` | Provider info recorded into the result record. |

`execute` returns the `ExecutionResult` dataclass (`exit_code`, `stdout`,
`stderr`, `wall_time_sec`). `reset()` is the seam that lets the runner and the
pool recycle one session across multiple tasks instead of paying full startup
cost per task.

Backends register themselves into the `SandboxRegistry` with the
`@register_sandbox(name)` decorator and are resolved by string name at
`Runner.setup()` time. Adding a backend is a register plus an import line; no
runner change is needed.

## Backends

| Name | Class (`alphadiana/engine/sandbox/`) | Runtime | Notes |
|---|---|---|---|
| `local` | `LocalSandbox` / `LocalSession` (`local.py`) | Host subprocess | Rejects shell metacharacters and guards against path traversal. No containment. |
| `rock` | `ROCKSandbox` / `ROCKSession` (`rock.py`) | ROCK Docker sandbox | Resource-limited, server-managed TTL, the default for the standard text and multimodal benchmark paths. |
| `podman` | `PodmanSandbox` / `PodmanSession` (`podman.py`) | Podman container | Rootless container alternative to Docker. |
| `swebench_container` | `SWEBenchContainerSandbox` / `SWEBenchContainerSession` (`swebench_container.py`) | Official per-task SWE image | `create_session(task=...)` takes the task so it can select the correct per-instance image. |

`swebench_container` is the only backend whose `create_session` takes a task
argument, because each SWE-bench instance has its own image. The runner detects
this and passes the task through; all other backends use the zero-argument
`create_session()`.

## ROCK sandbox

`ROCKSandbox.setup()` (`rock.py`) reads the sandbox config and stores it for
each session. The keys it accepts:

| Config key | Default | Meaning |
|---|---|---|
| `admin_base_url` | (required) | ROCK admin endpoint that starts/stops sandboxes. |
| `proxy_base_url` | (required) | ROCK proxy endpoint that fronts the running sandbox. |
| `image` | `DEFAULT_SANDBOX_IMAGE` | Container image for the sandbox. |
| `memory` | `2g` | Memory limit. |
| `cpus` | `0.5` | CPU request. |
| `limit_cpus` | `None` | Optional hard CPU cap. |
| `startup_timeout` | `300` | Seconds to wait for the requested resource profile to come up. |
| `fallback_startup_timeout` | `180` | Timeout used for smaller fallback profiles (floored at 30). |
| `auto_clear_seconds` | `3600` | Server-side container TTL (see below). |
| `start_retries` | `1` | Attempts per resource profile. |
| `reset_between_tasks` | `True` | Whether `reset()` cleans the workspace between tasks. |
| `proxy_timeout` | `1800` | Proxy request timeout passed to the SDK client. |
| `network_mode` | `None` | Set `host` to resolve the host IP for sandbox-internal URLs. |
| `use_kata_runtime` | `False` | Use the Kata runtime for stronger isolation. |

### Session lifecycle

`create_session()` builds a `ROCKSession`, whose constructor immediately starts
the sandbox. `_start_sandbox()` tries an ordered list of resource profiles: the
requested `(memory, cpus)` profile gets the full `startup_timeout`, and smaller
fallback profiles get `min(startup_timeout, fallback_startup_timeout)`. Each
profile is retried up to `start_retries` times. Only the requested profile is
written into the process-wide profile cache; caching a tiny fallback profile
would poison later sessions for the same image. Once a profile succeeds the
bash session is resolved and the session is ready.

`reset()` cleans the workspace and per-session paths (when
`reset_between_tasks` is set) so the same container can serve the next task.
`close()` cleans up and then stops the container, retrying the stop up to three
times. If the stop never succeeds the container is logged as potentially leaked,
which is where the server-side TTL becomes the safety net.

### Startup window and the ~85s run cap

ROCK startup is the flaky part of the lifecycle. Under a contended host the
startup window stretches and the SDK can hang, which is why `startup_timeout`
defaults to 300 seconds and `_start_sandbox` wraps `sandbox.start()` in its own
`attempt_timeout + 10s` guard. Separately, ROCK's synchronous `run_in_session`
caps each individual call at ~85 seconds; treat that ~85s as the per-call
synchronous run cap to budget for, not sandbox spin-up time. Cells launched with
too many concurrent sandboxes against one ROCK admin can lose samples to startup
flakiness rather than to model error. When a sandbox is abandoned (for example a
`close()` timeout during pool teardown) `auto_clear_seconds` is the
server-side TTL that lets ROCK eventually reclaim it.

## SandboxPool

`SandboxPool` (`pool.py`) is a fixed-size pool of pre-created sessions used for
concurrent evaluation. The runner creates it only when all of the following
hold:

- a sandbox is configured (`sandbox.name` is set),
- `max_concurrent > 1`,
- the backend reports `supports_pooling()` is true, and
- the agent is not `openclaw` (OpenClaw handles its own concurrency, see below).

When created, the pool pre-builds `size = max_concurrent` sessions. The runner's
per-item closure calls `acquire()` to borrow a session and, in its `finally`
block, `release()` to return it. `release()` resets the session before handing
it back, and if `reset()` throws it transparently replaces the session to keep
the pool size stable. `discard_and_replace()` drops a session the runner has
judged unusable and creates a fresh one in its place. `teardown()` closes every
session concurrently with a 30-second per-session timeout.

If the backend does not support pooling, or concurrency is 1, the runner falls
back to a shared session reused sequentially, or a per-task session, depending on
the isolation mode.

## Isolation modes

The runner records which strategy it used in
`run_metadata["isolation_mode"]`. The value summarizes how sessions were
allocated for the run:

| Mode | When |
|---|---|
| `shared_gateway` | No sandbox configured; tasks share one gateway. |
| `explicit_sandbox` | `sandbox.name` is set in the config. |
| `auto_single_sandbox` | The runner auto-created a single ROCK sandbox. |
| `predeployed_pool` / `partial_predeploy` | A pool of ROCK sandboxes was predeployed (e.g. for an auto-deploy agent). |
| `fresh_predeployed_pool` / `partial_fresh_predeployed_pool` | Predeployed pool in fresh-per-task mode with background replenishment. |

The `strict_isolation` config field is the fail-closed switch. With
`strict_isolation: true` any sandbox auto-create or predeploy failure becomes a
hard `RuntimeError` instead of silently degrading to a shared gateway (which
would let one task's workspace leak into the next). It is the load-bearing knob
behind benchmark-fairness claims, so production benchmark configs set it.

```yaml
# Standard ROCK-backed benchmark config
sandbox:
  name: rock
  config:
    admin_base_url: http://127.0.0.1:8001
    proxy_base_url: http://127.0.0.1:8002
    memory: 2g
    cpus: 0.5
    startup_timeout: 300
    auto_clear_seconds: 3600
strict_isolation: true
max_concurrent: 4
```

## OpenClaw gateway-pool special-casing

OpenClaw is the one harness the pool logic excludes by name. It manages
concurrency itself through a multi-sandbox **gateway pool** rather than a
`SandboxPool`, because running multiple OpenClaw tasks inside one sandbox would
cause workspace contention.

When the config sets `sandbox: null` and the agent is a gateway auto-deploy
agent, the runner predeploys N ROCK sandboxes, each running one in-sandbox
gateway, and threads the result into `agent_config["gateway_pool"]`. The sandbox
count is `num_sandboxes` if given, otherwise
`ceil(max_concurrent / OPENCLAW_CONCURRENCY_PER_SANDBOX)`, where
`OPENCLAW_CONCURRENCY_PER_SANDBOX` is a hard constant of 1: each OpenClaw
sandbox handles exactly one concurrent task, so concurrency comes from the
number of sandboxes, not threads per sandbox. After predeploy the runner
auto-lowers `max_concurrent` to the available sandbox capacity, so the configured
value is an upper bound, not a guarantee.

A fresh-per-task variant (`reuse_predeployed_sandboxes: false`) adds a
self-healing pool: background daemon threads recreate sandboxes as tasks consume
them, governed by `standby_sandboxes` and `predeploy_replenish_concurrency`, with
a lease-probe (`GET /models`, status `< 500` is healthy) before a session is
handed to a task.

The OpenClaw integrity guard rejects responses that are session-tainted,
stream-incomplete, or carry heartbeat taint text, recording the result as an
error rather than a spurious score. This guards against leaked cross-task state
on the shared gateway pool. See [OpenClaw](../harnesses/) and the
[engine and runner](./engine-and-runner) page for how this fits into the
per-item solve loop.

```yaml
# OpenClaw: no top-level sandbox; the runner predeploys a gateway pool
sandbox: null
agent:
  name: openclaw
  config:
    rock_admin_url: http://127.0.0.1:8001
    rock_proxy_url: http://127.0.0.1:8002
    rock_image: <gateway-image>
    num_sandboxes: 4
    reuse_predeployed_sandboxes: true
max_concurrent: 4
strict_isolation: true
```

## Per-benchmark isolation matrix

What actually runs where, per harness:

| Benchmark | `openclaw` | `opencode` | `zeroclaw` |
|---|---|---|---|
| `terminal-bench-2` | Docker task container + Dockerized controller | Docker task container + controller process (host/docker/podman) | Docker task container + Dockerized controller |
| `SWE-bench Pro` | official per-task SWE container (`swebench_container` backend, `swebench_docker` agent) | official per-task SWE container (`swebench_container` backend, `swebench_docker` agent) | official per-task SWE container (`swebench_container` backend, `swebench_docker` agent) |
| `MMMU-Pro` | ROCK sandbox | controller process (host/docker/podman) | ROCK sandbox |
| `IMO-AnswerBench` | ROCK sandbox | controller process (host/docker/podman) | ROCK sandbox |
| `GPQA-Diamond` | ROCK sandbox | controller process (host/docker/podman) | ROCK sandbox |
| `HLE` | ROCK sandbox | controller process (host/docker/podman) | ROCK sandbox |

`opencode` runs its CLI via a controller process whose mode defaults to `host`;
set `controller_mode: docker` or `controller_mode: podman` for a disposable
container controller. The container modes are a process and filesystem boundary,
not ROCK-level resource isolation. The checked-in opencode configs use
`controller_mode: podman`; the schema marks podman preferred, docker legacy.

### Paper-safe wording

It is accurate to say AlphaDiana runs supported benchmark tasks in disposable
task-scoped sandbox or container runtimes intended to keep benchmark-side
effects confined to the task environment in normal use. It is **not** accurate to
say every agent-benchmark pair always runs in a strong sandbox, or that the
runtime is "fully isolated from the host." This is a practical evaluation
boundary for reproducibility and host-side containment, not a formal security
guarantee. The full reviewer-facing framing lives in
[Isolation and fairness](../concepts/isolation-and-fairness).
