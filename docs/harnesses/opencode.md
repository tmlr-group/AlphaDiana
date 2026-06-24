---
sidebar_position: 3
---

# OpenCode

The OpenCode harness wraps the third-party `opencode` CLI (`opencode-ai` 1.3.2)
as an AlphaDiana agent. The entry class is `OpenCodeAgent(Agent)`
(`alphadiana/harness/opencode/agent.py:684`, `name = "opencode"`), registered via
`AgentRegistry.register("opencode", OpenCodeAgent)` at the bottom of the same
file. It dispatches across three controller modes plus an in-container
SWE-bench runtime, and layers a set of experimental memory mechanics on top of
the CLI's own session store.

A normal OpenCode run is byte-for-byte unchanged from the upstream CLI: every
memory lever defaults off.

## Solve Dispatch

`solve()` (`agent.py:848`) branches on the `runtime` config key:

- `runtime == "swebench_container"` -> `_solve_in_container(task, sandbox)`
  (requires a sandbox; raises otherwise). See
  [SWE-bench In-Container](#swe-bench-in-container).
- otherwise -> `_solve_cli(task)` (`agent.py:1487`), the main path for
  AIME / GPQA / HLE / IMO / MMMU.

## Controller Modes

`_solve_cli` then dispatches on `controller_mode`. The supported set is
`_SUPPORTED_CONTROLLER_MODES = {host, docker, podman}` (`agent.py:62`), validated
in `setup()`; an unknown mode raises `ValueError`.

| Mode | Path | What runs |
|------|------|-----------|
| `host` | `subprocess.Popen(...)` | `opencode` as a direct host process |
| `docker` | `_run_in_docker` (`agent.py:1070`) | ephemeral `--rm` controller container |
| `podman` | `_run_in_podman` (`agent.py:1312`) | rootless controller container |

The default controller image is mode-dependent:
`alphadiana/tb2-opencode-controller:latest` for docker
(`_DEFAULT_DOCKER_CONTROLLER_IMAGE`, `agent.py:63`) and
`alphadiana-opencode-podman:latest` for podman (`_DEFAULT_PODMAN_CONTROLLER_IMAGE`,
`agent.py:64`). The checked-in benchmark configs set `controller_mode: docker`;
`host` remains available for local debugging.

### Host vs Docker

```
# Host mode
AlphaDiana Runner
  -> subprocess.Popen("opencode run --format json ...")
  -> opencode runs as a host process with full filesystem access

# Docker mode (controller_mode=docker)
AlphaDiana Runner
  -> docker run --rm
       --network=<controller_network>      # default host
       --user=UID:GID                       # match host user, no root-owned files
       -v <workdir>:<workdir>
       -e HOME=<workdir>/.controller-home   # writable HOME (avoid Bun EACCES mkdir /.local)
       -e OPENAI_API_KEY=... -e OPENAI_BASE_URL=...
       -e XDG_CONFIG_HOME=<workdir>/xdg-config
       -e OPENCODE_DISABLE_CHANNEL_DB=1
       <image>
       node /usr/lib/node_modules/opencode-ai/bin/opencode run --format json ...
  -> opencode runs inside a disposable controller container
```

Key properties of docker/podman mode:

- `--user=UID:GID` matches the host user, preventing root-owned files on cleanup.
- `HOME` lives inside the mounted workdir, giving the Bun runtime a writable home.
- `--network=host` lets the container reach the LLM endpoint.
- Only the temporary workdir is bind-mounted; arbitrary host paths are not
  exposed.

This is process and filesystem containment, not a formal security sandbox.
There are no CPU/memory quotas and the container keeps normal network access.
For full isolation use [OpenClaw](../harnesses/openclaw) (ROCK sandbox); for the
zero-isolation baseline see [direct_llm](../harnesses/direct-llm).

The podman path reuses the shared engine abstraction
(`PodmanAgentRuntime` / `PodmanAgentSpec` / `PodmanCLI` from
`alphadiana.engine.container_runtime`). On teardown it collects a container-log
excerpt and surfaces provenance (`container_engine=podman`,
`transport=opencode_cli_podman`) via `error_provenance_metadata()`.

## CLI Invocation and Custom Provider

The CLI command shape (`agent.py:1678`) is:

```bash
opencode run --format json --dir <workdir> --title <task_id> \
  [--model custom/<model_name>] [--variant] [--agent] \
  [--session <id>] [--file <attachment>]... \
  -- <prompt>
```

The model is always declared as a `custom` OpenAI-compatible provider written to
`<workdir>/xdg-config/opencode/opencode.json` (`agent.py:1538`), and `cli_model`
is `custom/<provider_model_name>`. The provider file has roughly this shape:

```jsonc
{
  "provider": {
    "custom": {
      "api": "openai",
      "options": {
        "apiKey": "...",
        "baseURL": "...",
        "timeout": 1200000,                 // timeout * 1000 (ms)
        // optional: temperature, top_p, max_tokens, streaming,
        // chat_template_kwargs (enable_thinking lives here)
      },
      "models": {
        "<name>": {
          "name": "<name>",
          "tool_call": true,
          "limit": { "context": 65536, "output": 32000 },  // optional
          // optional: attachment + modalities for multimodal
        }
      }
    }
  },
  "model": "...",
  "small_model": "..."
}
```

The CLI environment (`agent.py:1660`) sets `OPENAI_API_KEY`, `OPENAI_BASE_URL`
(the logprob proxy URL when capture is on), `XDG_CONFIG_HOME=<workdir>/xdg-config`
and `OPENCODE_DISABLE_CHANNEL_DB=1`, and strips `ALL_PROXY` / `HTTP(S)_PROXY` /
`OPENAI_MODEL_NAME`. `OPENCODE_DISABLE_CHANNEL_DB=1` is load-bearing: newer
opencode suffixes the sqlite filename by build channel, which would break
session chaining across containers; pinning to plain `opencode.db` keeps every
container opening the same file.

### Answer Extraction

`_parse_opencode_output` returns `(assistant_text, events, session_id)`. The
answer prefers a boxed / explicit-answer regex (`_EXPLICIT_ANSWER_RE:66`,
`_BOXED_RE:70`). The `transport` metadata is one of `opencode_cli` (host),
`opencode_cli_container` (docker), or `opencode_cli_podman` (podman), set at
`agent.py:1811`. A timeout (returncode `-1`) yields an `OpenCodeTimeout`
error type.

### Logprob Capture

`resolve_logprob_capture_config(config)` (`agent.py:764`) gates a
`LogprobCaptureProxy` (`agent.py:1606`) placed in front of the upstream. When on,
the provider `baseURL` is rewritten to `proxy_url + "/v1"`, request overrides
(temperature / top_p / max_tokens / chat_template_kwargs) are applied, and
proxy-captured records are merged in when in-trace extraction finds none.

## Memory Levers

The harness exposes several overlapping "memory" mechanisms. They are distinct
and compose:

| Lever | Mechanism |
|-------|-----------|
| `persistent_memory` | harness-side `_memory_bank` prompt injection **and** a pinned shared workdir/HOME (enables the native session store) |
| `--session` chaining | continues the same native opencode session across tasks |
| `compact_after_task` | post-task `/compact` via the summarize API (docker-only) |
| `fresh_session` | keep prompt-injection memory, drop `--session` chaining |
| `memory_freeze` | build/frozen snapshot-and-restore of HOME for transfer experiments |
| `oracle_feedback` | a self-grading reflection turn that sees ground truth |

`persistent_memory` is **not** opencode-native memory. It is a harness-side
`_memory_bank: list[dict[str, str]]` of `{task_id, answer, summary}` entries
(`summary = assistant_text[:300]`) appended after each solved task
(`agent.py:1028-1033`). On each task it prepends a
`[MEMORY FROM PREVIOUS PROBLEMS] ... [END MEMORY]` block to `task.problem`
(`agent.py:1640-1653`). Setting `persistent_memory: true` also pins one shared
workdir (`tempfile.mkdtemp(prefix="opencode-persistent-")`) before task 1
(`agent.py:1495`) so all tasks share `--dir` / HOME / the opencode data dir,
which is what makes the opencode-native session store persist.

### Session Chaining

When `persistent_memory` is set, a prior `self._last_session_id` exists, and
`fresh_session` is **not** set, the harness adds `--session <last_session_id>`
(`agent.py:1694`) so opencode continues the same native session. After each run
it parses the new `session_id` and advances `self._last_session_id`
(`agent.py:1780`).

### fresh_session

`fresh_session` (`agent.py:772`) keeps the `_memory_bank` prompt injection
(cross-task memory) but does **not** pass `--session`, so each task starts a
fresh opencode session. This prevents one task's intra-task context balloon from
poisoning the whole chained session (the exp2-OC overflow failure mode). In this
mode memory is carried forward via the bank only (`agent.py:2092`).

### compact_after_task

`compact_after_task` (`agent.py:766`) runs `_compact_session_in_docker`
(`agent.py:1145`) after a non-frozen task. It is **docker-only** (warns and
no-ops elsewhere, `agent.py:1156`). It spins a short-lived
`opencode serve --port <4100+rand>` container, polls `/session`, then POSTs
`/session/<id>/summarize` with body `{providerID: "custom", modelID: <model>,
auto: false}` — the backing API for the `/compact` slash command. It only fires
when not `fresh_session`.

### memory_freeze (build / frozen snapshot)

`memory_freeze` (`agent.py:779`) is the exp3 transfer mode, keyed on per-task
`task.metadata["memory_mode"]` (default `"build"`, read at `agent.py:1705`).

- `build` tasks chain, compact, and snapshot normally. After each build task,
  `_snapshot_persistent_home` (`agent.py:1212`) copytrees
  `<workdir>/.controller-home` -> `.frozen-home-snapshot`.
- `frozen` tasks call `_restore_persistent_home` (`agent.py:1228`) **before**
  running (so they fork from the train-phase memory) and are **not** chained
  forward, compacted, or snapshotted — frozen test tasks stay independent of
  each other.

The build/frozen labels come from the `custom` benchmark: each problem item
carries `memory_mode: build|frozen` ->
`BenchmarkTask.metadata["memory_mode"]`
(`alphadiana/benchmarks/custom/benchmark.py:36`).

### oracle_feedback

`oracle_feedback` (`agent.py:786`) is exp3-v2. After solving a build task and
before compaction/snapshot, `_run_oracle_feedback_turn` (`agent.py:1431`)
appends one same-session turn that reveals `task.ground_truth`, asks the model to
self-grade CORRECT/WRONG, and write a lesson. The reflection then rides into the
compaction summary and the freeze snapshot. It is best-effort (never raises into
`solve`) and runs only on non-frozen tasks. The original answer is scored
*before* the truth is leaked, so this is a train-phase reflection device, not an
answer leak into scoring.

### Native autocompact margin

`context_limit` / `output_limit` (`agent.py:792-793`), when set, are written into
the provider `model.limit` `{context, output}` (`output` defaults to `32000`,
`agent.py:1521-1528`). Set `context` below the true window (e.g. `< 65536`) so
opencode's native proactive autocompact fires before the provider hard wall.

## SWE-bench In-Container

When `runtime == "swebench_container"`, `_solve_in_container` (`agent.py:857`)
runs opencode *inside the task sandbox* rather than in a controller container.
`OpenCodeContainerRuntimeManager` (`alphadiana/harness/opencode/container_runtime.py:157`)
installs opencode into the sandbox if needed (`_install_opencode_if_needed`),
writes the provider config (`_write_opencode_config`), runs the task
(`run_task`), and collects artifacts including a sqlite summary
(`collect_artifacts`). The result is reduced to a git patch, using
`_SWE_BENCH_SYSTEM_PROMPT` (`agent.py:83`) by default. This is the container-agent
path for SWE-bench Verified-Mini (distinct from the official standalone SWE-agent
path used by SWE-bench Pro).

## Config Reference

### Controller

| Key | Default | Description |
|-----|---------|-------------|
| `controller_mode` | `host` (configs set `docker`) | `host` \| `docker` \| `podman` |
| `controller_image` | mode-dependent (see above) | controller image |
| `controller_network` | `host` | docker/podman network mode |
| `runtime` | `""` | `""` for the CLI path, `swebench_container` for in-container |

### Core agent.config

Read by `OpenCodeAgent.setup()` (`agent.py:708`):

| Key | Default | Notes |
|-----|---------|-------|
| `model` / `model_name` / `api_model` | — | provider model name |
| `api_base` / `api_key` | — | use `sk-EMPTY`, not literal `EMPTY` |
| `tool_call` | `true` | opencode requires tool calling |
| `timeout` | `1200` | seconds; written to provider as ms |
| `temperature` / `top_p` / `max_tokens` | — | sampling |
| `enable_thinking` | — | via `options.chat_template_kwargs` |
| `variant` / `agent` / `agent_md_path` / `agent_md_content` | — | opencode agent selection |
| `system_prompt` | — | prepended system text |
| `skill_folder` | — | mounted skill bundle (see [Skills](../harnesses/skills)) |
| `opencode_bin` | `opencode` | CLI binary name |
| `streaming` | — | provider streaming |
| `print_logs` / `log_level` | — | diagnostics |

### Memory-experiment keys

All default off, so a normal run is unchanged:

| Key | Default | Description |
|-----|---------|-------------|
| `persistent_memory` | `false` | `_memory_bank` injection + pinned shared workdir/HOME |
| `compact_after_task` | `false` | docker-only `/compact` via summarize API |
| `fresh_session` | `false` | keep bank, drop `--session` chaining |
| `memory_freeze` | `false` | build/frozen HOME snapshot-restore |
| `oracle_feedback` | `false` | self-grading reflection turn (reveals ground truth) |
| `context_limit` | — | provider `model.limit.context` (autocompact margin) |
| `output_limit` | — | provider `model.limit.output` (default `32000`) |

## Quick Start

```bash
# 1. Build the docker controller image (one-time)
docker build --network host \
  -f alphadiana/benchmarks/terminal_bench2/deploy/dockerfiles/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .

# 2. Run any opencode config with docker isolation
python -m alphadiana.cli run configs/examples/opencode_minimax_imo_answerbench.yaml \
  -o run_id=imo_opencode_docker_test \
  -o agent.config.controller_mode=docker \
  --redo-all
```

The docker image installs Ubuntu 22.04, Node.js 22, `opencode-ai` 1.3.2, and
standard shell tools. For the podman controller:

```bash
podman build \
  -f alphadiana/harness/opencode/deploy/Containerfile.podman-controller \
  -t alphadiana-opencode-podman:latest .
```

For host mode, install the CLI directly (Node.js 22+):

```bash
conda create -n node22 -y -c conda-forge nodejs=22
conda activate node22
npm install -g opencode-ai@1.3.2
export PATH="/path/to/node22/bin:$PATH"
```

### Example config

```yaml
agent:
  name: opencode
  config:
    controller_mode: docker
    # controller_image: alphadiana/tb2-opencode-controller:latest
    # controller_network: host
    model_name: qwen/qwen3-235b-a22b-2507
    api_base: ${OPENAI_BASE_URL}
    api_key: ${OPENAI_API_KEY}
    timeout: 1800
```

## Multimodal Support

When a task has image attachments, the harness saves them to the workdir via
`write_attachments()`, declares `modalities: {input: [text, image]}` in the
provider config, and passes images via `--file` flags. This works in host,
docker, and podman mode (the workdir is bind-mounted, so files are reachable
inside the container).

The model must support **both** vision input and tool calling. A text-only
tool-calling model still works for text tasks; a vision model without tool
calling does not work with opencode at all.

## Notes and Gotchas

- The docker controller container is ephemeral per task (`--rm`, fresh name per
  call) even under `persistent_memory`. Cross-task state survives only via the
  bind-mounted shared workdir/HOME, not a reused container.
- `compact_after_task` and the persistent long-lived container
  (`_ensure_persistent_container`) are docker-only. `memory_freeze`
  snapshot/restore is a host-side copytree of `<workdir>/.controller-home` and
  works under docker and podman because HOME is bind-mounted.
- The `micro_runs/Memory/*opencode*.yaml` configs use a **different** memory
  system (keys `memory_plugin`, `memory_plugin_version`, `memory_hf_endpoint`,
  `memory_cache_root`, `memory_max_chars`) that `agent.py` does not read. Those
  native-plugin runs are processed via
  `scripts/oc_capture_to_alphadiana.py`, not the `persistent_memory`
  `_memory_bank` path.
- Results are written by the result store
  (`alphadiana/analysis/io/result_store.py`); the runner and config live under
  `alphadiana/engine/`.

## Related Files

| File | Role |
|------|------|
| `alphadiana/harness/opencode/agent.py` | `OpenCodeAgent`, controller dispatch, memory levers |
| `alphadiana/harness/opencode/container_runtime.py` | `OpenCodeContainerRuntimeManager` (SWE-bench) |
| `alphadiana/harness/opencode/deploy/Containerfile.podman-controller` | podman controller image spec |
| `alphadiana/benchmarks/terminal_bench2/deploy/dockerfiles/Dockerfile.opencode-controller` | docker controller image |

See also: [direct_llm](../harnesses/direct-llm),
[OpenClaw](../harnesses/openclaw), [ZeroClaw](../harnesses/zeroclaw),
[Skills](../harnesses/skills).
