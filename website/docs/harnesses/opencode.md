---
sidebar_position: 3
---

# OpenCode

The generic `opencode` harness runs the OpenCode CLI and parses its JSON event stream. It supports a normal CLI path with `controller_mode: host|docker|podman` and a task-container path selected by `runtime: swebench_container`.

## Solve dispatch

`OpenCodeAgent.solve()` has two branches:

- `runtime: swebench_container` requires a live task-bound sandbox and runs OpenCode inside the SWE-bench repository container;
- otherwise the harness builds a task work directory and launches the CLI through the selected controller.

Controller mode is independent of the benchmark sandbox. Podman is preferred for current standard-reasoning paths that have validated readiness configs. Docker remains a legacy/baseline controller, and host mode is primarily for debugging.

## Controller modes

| Mode | Boundary | Notes |
| --- | --- | --- |
| `host` | OpenCode process on the host | Simplest, weakest task-runtime separation |
| `docker` | Ephemeral controller container | Uses the Docker controller image |
| `podman` | Rootless controller container | Uses the Podman-only controller image and records Podman provenance |

The controller receives the task directory, generated OpenCode config, provider environment, and attachments. Container execution is a stronger runtime boundary, not a formal security guarantee.

## Generated provider config

For the CLI path, the harness writes `xdg-config/opencode/opencode.json`. The custom OpenAI provider includes the resolved base URL, API key, timeout, optional sampling settings, streaming, thinking template arguments, logprob options, and model capabilities.

When `context_limit` is set, the model entry also contains:

```json
{
  "limit": {
    "context": 60000,
    "output": 32000
  }
}
```

`output_limit` is used only with `context_limit`; its default in that block is `32000`. This config declares OpenCode's proactive compaction margin and does not change the provider's `max_tokens` by itself.

## CLI invocation

The normal command shape is:

```text
opencode run --format json --dir <workdir> --title <task_id>
  [--model custom/<model>] [--variant <variant>] [--agent <agent>]
  [--session <prior_session>] [--print-logs] [--log-level <level>]
  [--file <attachment>]... -- <prompt>
```

`--session` is present only when persistent memory is enabled, a prior session ID exists, and `fresh_session` is false.

The harness sets `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `XDG_CONFIG_HOME`, and `OPENCODE_DISABLE_CHANNEL_DB=1`, then removes inherited proxy variables and `OPENAI_MODEL_NAME`. Disabling channel-specific DB suffixes keeps the persistent session store on a stable filename across controller invocations.

## Answer, events, and failures

OpenCode JSON events are parsed into assistant text, lifecycle/tool events, reasoning trajectory, and session ID. On abnormal exits the harness can prefer preserved partial model output over a shorter assistant event stream. Ordinary benchmark answers are extracted from the chosen model text; SWE-bench uses a patch extractor.

The important status split is:

- process timeout (`returncode=-1`) returns `answer=None`, `finish_reason: timeout`, and `opencode_timeout_scored_zero=true`; it scores zero and is checkpoint-complete;
- a non-timeout non-zero exit is an agent error;
- a structured provider/tool failure is preserved as the corresponding error;
- lifecycle-only or empty assistant output becomes `agent_empty_output`, not a synthetic answer.

`OpenCodeTimeout` is supporting metadata, not the universal top-level task status.

## Persistent memory

Memory controls are opt-in and implemented in the current generic harness:

| Key | Effect |
| --- | --- |
| `persistent_memory` | Pins one workdir/HOME and enables native session chaining |
| `fresh_session` | Starts a new native session per task while using the harness prompt-memory bank |
| `compact_after_task` | Runs the Docker-only summarize/compact flow after eligible tasks |
| `memory_freeze` | Snapshots the post-build persistent HOME and restores it for independent frozen tasks |
| `oracle_feedback` | Adds a ground-truth reflection turn during build tasks |
| `context_limit`, `output_limit` | Declares a proactive native compaction margin |

With ordinary persistent mode, the harness stores the latest native session ID and supplies it to the next task. With `fresh_session`, it does not chain `--session`; instead, accumulated task summaries are injected into the prompt. Transfer configs use `task.metadata.memory_mode: build|frozen` to decide whether state may advance.

`oracle_feedback` reveals the official answer to a post-solve reflection turn. This changes the experimental condition and must be reported explicitly; it is not a neutral runtime optimization.

## SWE-bench task-container path

`runtime: swebench_container` requires `sandbox.name: swebench_container`. The runtime manager executes OpenCode inside the prepared repository container, preserves stdout/stderr/session artifacts, and returns a git diff or extracted patch. Do not mix controller-mode claims from the generic CLI path into this task-container path.

## Logprobs and attachments

When logprob capture is enabled, the harness places the shared capture proxy in front of the configured provider and records request overrides plus captured records. Streaming can remain enabled because the proxy parses SSE.

Image attachments are copied into the work directory and passed with repeated `--file`. The generated model config declares image input capability for tasks that actually have image attachments.

## Config reference

Common keys include:

| Area | Keys |
| --- | --- |
| Runtime | `runtime`, `controller_mode`, `controller_image`, `controller_network` |
| Provider | `model`, `model_name`, `api_model`, `api_base`, `api_key` |
| Sampling | `temperature`, `top_p`, `max_tokens`, `streaming`, `enable_thinking` |
| CLI | `timeout`, `opencode_bin`, `variant`, `agent`, `print_logs`, `log_level` |
| Prompt/skills | `system_prompt`, `skill_folder`, `agent_md_path`, `agent_md_content` |
| Memory | `persistent_memory`, `fresh_session`, `compact_after_task`, `memory_freeze`, `oracle_feedback`, `context_limit`, `output_limit` |
| Observability | shared logprob-capture settings |

Unknown keys are not a reliable feature flag. Verify that a key is read by `OpenCodeAgent.setup()` and inspect preserved `opencode.json` plus task metadata in a real run.

## Example

```yaml
agent:
  name: opencode
  config:
    controller_mode: podman
    model: custom/${OPENAI_MODEL_NAME}
    model_name: ${OPENAI_MODEL_NAME}
    api_base: ${OPENAI_BASE_URL}
    api_key: ${OPENAI_API_KEY}
    timeout: 1200
    persistent_memory: false
```

Use the checked-in smoke/readiness config for the intended benchmark and controller. Build and runtime commands vary by path; a generic Docker command is not interchangeable with Podman or SWE-bench task-container execution.

## Artifacts to inspect

- `opencode_config.json` / generated `opencode.json`;
- JSON event stream, partial output, stderr, and session trace;
- `returncode`, answer source, controller provenance, and timeout metadata;
- provider request summaries and logprob sidecars when enabled;
- patch and container artifacts for SWE-bench.

## Related pages

- [Harnesses Overview](./)
- [Sandboxes & Isolation](../architecture/sandboxes)
- [Observability & Proxies](../architecture/observability)
