# Pi and DeepSeek Harness execution

Select `agent.name: pi` or `deepseek_harness`. These are generic text/file agents,
not Harbor/TB2 adapters. By default, each solve gets a disposable rootless Podman container,
workspace, home and session. Do not configure a second sandbox. Podman is the
default runtime. Host execution requires the explicit process configuration below.

## Build and select the immutable runtime

From the Diana checkout:

```bash
podman build -t localhost/diana-native:1 \
  -f alphadiana/harness/_internal/deploy/Containerfile \
  alphadiana/harness/_internal/deploy
podman image inspect localhost/diana-native:1 --format '{{.Id}}'
```

Set `HARNESS_IMAGE` to that image's full `sha256:<64 hex>` identity, not its tag.
Alternatively supply a locally installed `repository@sha256:<digest>`. Execution
never pulls an image. The image locks Pi 0.85.1, DeepSeek SDK/runtime 0.1.2rc1,
Node base digest, npm dependencies and Python wheels/hashes. Debian system
packages are resolved at build time; this is not a bit-identical rebuild promise.
Archive the built image for replay; its content identity covers those packages.

```yaml
agent:
  name: pi
  version: model-deployment-v1
  config:
    image: ${HARNESS_IMAGE}
    model: ${OPENAI_MODEL_NAME}
    api_base: ${OPENAI_BASE_URL}
    api_key: ${OPENAI_API_KEY}
    system_prompt: Solve the task using available tools.
    timeout: 120
    # The endpoint limit is 49152; code-heavy prompts can exceed Pi's estimate
    # by 8192 tokens, so retain additional headroom beyond that observed skew.
    context_window: 40000
benchmark:
  name: custom
  config:
    problems:
      - id: addition
        problem: What is 2 + 3? Return only the number.
        answer: '5'
scorer:
  name: exact_match
```

Shared fields are image, model, api_base, api_key, system_prompt, timeout,
max_tokens, reasoning, runtime (`podman` or `process`), training,
and the candidate fields below. Process mode adds the local paths documented below. `_execution_id` is reserved for the evaluator. Unsupported
model reasoning settings fail in the native harness. DeepSeek uses its official
`sdk-minimal` profile and `deepseek-official` adapter; an endpoint must implement
that adapter's compatible chat protocol. Pi uses an explicit OpenAI-compatible
provider. Pi also accepts `context_window`, `presence_penalty`, and `bash_timeout`;
DeepSeek rejects these Pi-only settings.

- Set `context_window` below the provider's model limit to account for tokenizer
  differences. It must exceed `max(max_tokens, 16384) + 4096`. Pi keeps its native
  16,384-token compaction reserve even when the solver has a larger output budget;
  recent context retention shrinks for smaller windows.
- `presence_penalty` is an optional finite number from -2 to 2, forwarded to each
  OpenAI-compatible request. Omit it to keep the provider default. Do not combine
  it with enabled `training`, whose capture policy owns the sampling parameters.
- `bash_timeout` is an optional positive integer in seconds, below the overall
  `timeout`, supported in Pi Podman images with GNU timeout and Bash. Each shell
  call gets this budget; a timed-out shell returns exit 124 to Pi, allowing the
  model to continue. GNU timeout sends TERM to the shell group, then KILL after
  five seconds if needed. The overall task deadline still applies.

No host credentials/configuration are discovered by the solvers.

## Explicit host process execution

For trusted tasks on Linux, select `runtime: process` and an installed executable.
Pi uses `pi_bin`; DeepSeek uses `dsh_python`, the Python interpreter containing
its SDK. Use absolute executable paths and an existing, owned `process_root`
directory with mode `0700`. Do not combine these settings with `image`.

```yaml
agent:
  name: pi
  config:
    runtime: process
    pi_bin: /absolute/path/to/pi
    process_root: /absolute/path/to/private/workspaces
    model: your-model
    api_base: http://127.0.0.1:8000/v1
    timeout: 120
```

Each solve creates a fresh work directory and HOME, copies public attachments,
and runs the shared JSON bridge in an owned process group. Success, failure and
timeout all clean up that group, including surviving child processes. Runtime
metadata records `process` and `host_process_workspace`. Host processes run with
the user's permissions; use this mode for trusted code. Podman provides container
isolation when required. Model settings and native token capture use the same
contracts in both modes.

Controllers that terminate an evaluator abruptly can reclaim its recorded process
groups with `native_process.cleanup_execution(execution_id, process_root)` from
`alphadiana.harness._internal`. Pass the evaluator's `_execution_id` and configured
root. Cleanup checks recorded leader identities and uses the inherited execution
marker to reclaim detached tool groups, including Pi shells. Other executions
are left running.

## Candidate modules

Supply `candidate_root`, `candidate_entry` (relative .ts/.js/.mjs module), and
`candidate_digest` together. Compute the digest using
`alphadiana.harness._internal.podman_agent.tree_digest(Path(root))`.
Pi expects an extension's default export. DeepSeek expects a Cordis plugin's
`apply` export, loaded by an explicit local-module patch. Candidate code may use
relative sibling modules and runtime-installed dependencies; install any extra
fixed dependencies when building a new image, never during evaluation.
Symlinks and special files are rejected. The complete candidate is read-only
mounted in Podman mode. Process mode resolves the candidate path from the caller's
working directory before launching in the task workspace, and loads it with host
permissions. Candidate identity is checked before execution.

The driver passes problem and public attachments only. Attachments are copied
under the task workspace: `/work/attachments` in Podman, or `work/attachments` in
the temporary process directory. Ground truth and task metadata are never serialized.
No host checkout, evaluator output directory or container socket is mounted.
Model networking uses slirp4netns; local model services can use
`host.containers.internal`. This is container filesystem/process isolation, not
a network egress filter. A malicious plugin can access its container's model
credential; use evaluation-scoped credentials.

Responses map final root assistant text, trajectory, finish reason and available
usage to AgentResponse. Missing usage is marked unavailable. Harness failures
raise a sanitized NativeHarnessError so Runner records an error rather than a
valid scored zero, except for the explicitly supported task deadline described
below. Cleanup failures also raise.
Pi role/content parsing is adapted from PR #78 at
198d4db0638a69e57a6de16cf2898ab3420cbe2a without Harbor reward logic.

For training-capable SFT or GRPO episodes, add the `training` block described in
[Pi native-token capture](pi-training.md). The host-side proxy captures native
prompt/output IDs and (for GRPO) behavior log probabilities from the configured
serving endpoint. It sends Pi only a normal chat-completion projection; no
answer re-tokenization is used. Every sample receives a fresh capture endpoint,
and any missing ID, policy attestation, usage mismatch, context reset or
incomplete termination enters the native error path.

### Delivery retries and model-call budget

An exact retry of the latest provider request replays its recorded response.
It retains the same native tokens and uses no additional model call. Changed
requests pass through normal transition validation. Failed captures and older
conversation states cannot replay.

Set `training.finalize_at_budget: true` to request a final answer on the last
permitted model call. The option defaults to `false`. When tools are present,
that last request uses `tool_choice: none`; messages and tool declarations stay
intact. A backend returning tool calls at that point fails validation. Record
this execution policy consistently for baseline and candidate runs.

## Self-validation

```bash
python -m pytest -c pyproject.toml tests -q -rs
ALPHADIANA_NATIVE_IMAGE="$HARNESS_IMAGE" \
ALPHADIANA_LIFECYCLE_IMAGE="$HARNESS_IMAGE" \
  python -m pytest tests/harness/test_native_runtime.py \
    tests/engine/test_native_cleanup_runtime.py -q
```

To exercise an installed Pi executable in process mode:

```bash
ALPHADIANA_PI_BIN=/absolute/path/to/pi \
  python -m pytest tests/harness/test_native_process.py \
    tests/harness/test_pi_context_window.py tests/harness/test_pi_bash_timeout.py -q
```

Put the executable's Node runtime on `PATH`. These process tests run the actual
CLI with a deterministic local model endpoint.

The runtime tests use actual Pi/DeepSeek tools and a deterministic HTTP model
fixture. They cover plugin loading, context compaction, sampling parameters,
shell timeout recovery, training capture and cleanup. Model quality requires
a live-model evaluation. Without the image variables the container checks skip.

### Authenticated endpoints

The pinned Pi version resolves model API key environment references with a `$`
prefix. The bridge uses `$DIANA_MODEL_API_KEY`, populated from private container
stdin; a bare environment variable name would be sent as a literal credential.
The deterministic runtime fixture verifies the Authorization header, including a
non-default test credential. Native containers disable Podman's implicit host
HTTP proxy forwarding; deployment does not inherit a localhost proxy by accident.

### Task deadlines and public inputs

An evaluator-enforced task execution deadline returns `answer=None`,
`finish_reason=timeout`, and `native_harness_timeout_scored_zero=true`. Like the
existing harness policy, this is a completed, valid scored-zero sample. Only the
remaining task budget passed to Podman start/attach (or budget exhaustion after
successful container creation) has this treatment. Container creation/cleanup
failures and provider/request failures remain evaluation errors. An outer Hebe
worker deadline remains an infrastructure failure, not a completed task.

Nested public attachment directories are explicitly staged as 0755 and files as
0644, even under host umask 077, then mounted read-only. This permission change is
limited to disposable public input; source workspaces and evaluator evidence are
not made public. Pi extension callback errors invalidate the result even when
Pi exits successfully and produces an answer. Extracted thinking uses the
normalizer's explicit reasoning type and survives result/trace persistence.


Completed native messages are projected onto Diana's persisted trajectory schema,
including tool call IDs, inputs, tool results, error flags and dedicated reasoning. Original Pi and
DeepSeek events remain in `response_json.native_events` for diagnosis. DeepSeek
usage sums completed assistant-message records; streaming usage chunks describe
the same requests and are not added again. Unreported usage remains unavailable.

An answer ending at the model token limit (`finish_reason=length`) is scored by
the configured scorer, like DirectLLM; an empty truncated answer is scored zero.
This is distinct from a provider/protocol failure and from the wall-clock task
budget, which returns a completed timeout with score zero.
