---
sidebar_position: 5
---

# ZeroClaw

The generic `zeroclaw` harness runs the stock Rust `zeroclaw agent` CLI inside a live container/sandbox session. It also supports the current Podman runtime backend. The normal generic path is sandbox-only: `sandbox: null` without a configured runtime manager is not a local host mode and raises before execution.

## Dispatch

`ZeroClawAgent.solve()` selects:

- `runtime_backend: podman` — use the Podman runtime manager and its gateway bridge;
- otherwise — require a live sandbox and run the native CLI inside it.

The generic harness does not fall back to executing ZeroClaw directly on the host. Do not publish a “local mode” by changing the sandbox to `local`: ZeroClaw preparation and run commands require shell constructs such as `&&`, redirection, and command substitution, while `LocalSession` deliberately rejects those metacharacters.

## Per-task runtime flow

For a sandbox-backed task, the harness:

1. validates model/provider settings and the `zeroclaw` binary;
2. creates task-scoped workspace, HOME, XDG, config, state, and attachment paths;
3. uploads generated `config.toml`, task prompt, skills, and attachments;
4. runs `zeroclaw --config-dir <task-home/.zeroclaw> agent ...` with a per-task session-state file;
5. reads stdout, stderr, session state, and the bounded native runtime trace;
6. builds normalized trajectories and response metadata;
7. optionally runs a post-solve memory-store turn;
8. preserves workspace artifacts and any repository patch.

Without persistent memory, HOME, XDG directories, `ZEROCLAW_CONFIG_DIR`, and session state are isolated per execution. This prevents concurrent generic tasks from sharing ZeroClaw state accidentally.

## Generated command

The command runs from the task workspace and has this shape:

```text
timeout <request_timeout>
  zeroclaw --config-dir <home/.zeroclaw>
  agent --model <model> --temperature <temperature>
  --session-state-file <workspace/state/zeroclaw-session-state.json>
  -m <prompt>
```

The harness wraps the command in a shell to set the task-specific environment and redirect stdout/stderr. This is why a shell-restricted local sandbox is not compatible.

## Native config and safety controls

Generated `config.toml` includes provider/model selection, optional reasoning settings, optional ZeroClaw sandbox settings, runtime tracing, autonomy controls, shell timeouts, and tool-iteration limits. It sets the runtime trace to `state/runtime-trace.jsonl` and bounds it with `runtime_trace_max_entries = 200`.

The generated autonomy/tool policy is an agent configuration, not a host security guarantee. The outer sandbox/container boundary remains essential.

## Persistent memory

Memory is opt-in and implemented in the current source:

| Key | Effect |
| --- | --- |
| `persistent_memory` | Uses a shared HOME below the sandbox root across tasks |
| `memory_embedding` | Configures the ZeroClaw memory provider when `base_url` is present |
| `oracle_feedback` | Reveals the official answer to the post-solve store prompt |

With persistent memory enabled, the harness shares the ZeroClaw HOME and persistent workspace, emits a `[memory]` block when the embedding base URL is configured, and may prompt later tasks to search memory after at least one store attempt.

After an eligible solve, `_memory_store_via_agent()` runs a second ZeroClaw turn asking the model to call `memory_store`. `task.metadata.memory_mode: frozen` skips writes for transfer-test tasks. `oracle_feedback` changes the stored record to include the official answer and self-grading, so it must be disclosed as a different experimental condition.

The store turn is best-effort. Memory being configured or a store prompt being issued does not prove a successful tool call; inspect the solve/store traces and memory artifacts.

## Podman runtime

`runtime_backend: podman` uses the Podman runtime manager, starts or reuses its bridge, sends the task through the bridge, and collects container artifacts and provider-proxy observations. Result metadata records Podman/container provenance and distinguishes this transport from the ordinary sandbox CLI path.

Use the current checked-in Podman readiness config and runbook for image, socket, networking, and model settings. Do not translate an older ROCK or host example mechanically.

## Provider resolution

Common provider inputs are `model`, `provider`, `provider_api_base`/`api_base`, and `api_key`, with the standard OpenAI environment fallbacks where implemented. The harness resolves a ZeroClaw provider name compatible with the base URL and writes it into the generated TOML.

For local vLLM, set provider variables explicitly after repository activation and verify the effective worker environment. A loaded GPU model with no decode activity may indicate immediate provider validation failures rather than warmup.

## Runtime trace and trajectories

ZeroClaw's native `runtime-trace.jsonl` is more than a prompt/final-answer summary. The harness:

- parses up to the configured 200 entries;
- normalizes tool/runtime events and reasoning events;
- preserves the raw trace as a workspace artifact;
- records runtime record counts/presence;
- falls back to request, stdout, stderr, and final response when no usable trace exists.

When logprob capture is enabled, runtime trace mode is forced to full. For persistent-memory runs, the solve trace is stashed before the store turn and both solve/store evidence are preserved separately.

The trace is bounded and may be incomplete. For precise claims, compare the normalized trajectory with the raw trace, CLI output, and provider request summaries.

## Errors and checkpointing

The harness distinguishes:

- provider/tool/control-plane errors — preserved as rerunnable errors;
- non-timeout runtime failure or empty assistant output — invalid/rerunnable according to classification;
- timeout exit (`124`) or supported runtime-only timeout evidence — returns `answer=None`, `finish_reason: timeout`, `zeroclaw_timeout_scored_zero=true`, scores zero, and is checkpoint-complete;
- provider length exhaustion with no assistant text — when classified as a true
  empty assistant after length exhaustion, records
  `zeroclaw_empty_assistant_scored_zero=true`, scores zero as
  `valid_scored`, and is checkpoint-complete; other provider/control-plane
  failures remain rerunnable errors.

Verifier behavior for `terminal_bench2_zeroclaw` has additional reward requirements described in [Scoring & Results](../architecture/scoring-and-results).

## Config reference

| Area | Keys |
| --- | --- |
| Selector | `runtime_backend` (`podman` or ordinary sandbox path) |
| Provider | `model`, provider/base URL/key settings, sampling and provider token controls |
| Runtime | `request_timeout`, provider timeout, tool iteration limits, reasoning settings |
| Sandbox policy | workspace-only, allowed commands, ZeroClaw security-sandbox settings |
| Memory | `persistent_memory`, `memory_embedding`, `oracle_feedback` |
| Observability | runtime trace mode and shared logprob/proxy settings |
| Assets | `system_prompt`, `skill_folder`, image attachments |

## Running

Use a validated ROCK- or Podman-backed config for the intended benchmark:

```bash
python -m alphadiana.cli validate path/to/zeroclaw-config.yaml
python -m alphadiana.cli run path/to/zeroclaw-config.yaml
```

For long local-Qwen thinking runs, use a named `tmux` supervisor, preserve the raw log with `tee`, and size timeouts from measured token throughput. Checkpoint resume will skip completed scored rows and retry only incomplete work.

## Artifacts to inspect

- `zeroclaw_output.txt` and `zeroclaw_stderr.log`;
- `runtime-trace.jsonl` plus normalized trajectories;
- per-task session state and generated config;
- provider request summaries and logprob sidecars;
- solve/store traces and memory stats when persistent memory is enabled;
- sandbox/container provenance and repository patch.

## Related pages

- [Harnesses Overview](./)
- [Sandboxes & Isolation](../architecture/sandboxes)
- [Observability & Proxies](../architecture/observability)
