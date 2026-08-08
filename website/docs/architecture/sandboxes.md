---
sidebar_position: 4
---

# Sandboxes & Isolation

Sandboxes provide execution environments to agents. They are runtime boundaries and lifecycle abstractions, not a blanket security guarantee. Security depends on the selected backend, container configuration, mounts, network policy, host services, credentials, and the image being executed.

## Base contract

`SandboxSession` in `alphadiana/engine/sandbox/base.py` requires:

- `session_id`;
- `execute(command)`;
- `upload(filename, content)` and `download(filename)`;
- `close()`.

It also provides `reset()`, `metadata()`, and `read_text()`. The optional tool/injection surface is:

- `list_tools()` — MCP-style tool descriptions;
- `call_tool(name, arguments)` — tool invocation;
- `apply_turn_injections(turn_id)` — controlled per-turn environment injections.

Backends that do not expose tools raise `NotImplementedError`; the default injection implementation is a no-op.

`Sandbox.create_session()` is task-independent in the base interface. The runner deliberately passes `task=` to the task-bound `swebench_container` implementation.

## Backends

| Backend | Task-bound | Pool/shared session | Primary use |
| --- | --- | --- | --- |
| `local` | No | Backend-specific | Restricted local diagnostics; rejects shell metacharacters |
| `podman` | No | Backend-specific | Rootless local container execution |
| `rock` | No | Yes, when the configured path allows it | Remote/container agent sandboxes and gateways |
| `swebench_container` | Yes | No generic reuse across tasks | Repository-specific SWE-bench task container |

Do not point a shell-heavy harness at `local` merely by changing YAML. Commands that rely on `&&`, pipes, redirection, or command substitution are rejected by `LocalSession`.

## ROCK

ROCK exposes sandbox lifecycle and command/file operations through its service. AlphaDiana may create sessions on demand or predeploy OpenClaw gateway sessions. Runtime metadata records the sandbox identity and relevant transport information for later auditing.

For OpenClaw pools, the runner:

- deploys the requested pool before task dispatch;
- supplies one session per task according to the configured isolation mode;
- resolves published gateway ports from the ROCK admin status API;
- quarantines a session when evidence says the sandbox or gateway is dead;
- creates replacements when the live pool is depleted.

This behavior improves task separation and recovery. It does not establish a formal multi-tenant security boundary.

## SWE-bench task containers

`swebench_container` also requires the benchmark task at session creation so it can choose and prepare the task-specific repository image. Its boundary is the task container, and agents such as OpenCode run inside that live container when their runtime selector requests it.

## Isolation modes

Runner-level isolation controls determine whether a session is reused or refreshed. The exact choices are backend- and harness-dependent; a configuration name alone does not prove isolation. Validate the resulting metadata and task artifacts.

For current standard-reasoning controller paths, Podman is the preferred local container controller where a validated config exists. Docker remains a supported legacy/baseline controller for some paths, and host mode is a debugging option. Use wording such as “stronger task-runtime boundary,” not “full isolation.”

## Operational checks

Before a ROCK-backed run:

- run the repository security preflight through the supported startup scripts;
- verify that the active ports and Ray/Redis/ROCK services belong to this checkout;
- preserve raw run logs and sandbox metadata;
- inspect actual task artifacts before making support claims.

For Podman, record the socket, API version, image, network mode, and container identifiers available in result metadata. A container starting successfully does not prove provider reachability or benchmark correctness.

## Related pages

- [Engine & Runner](./engine-and-runner)
- [Registries](./registries)
- [OpenClaw](../harnesses/openclaw)
- [ZeroClaw](../harnesses/zeroclaw)
