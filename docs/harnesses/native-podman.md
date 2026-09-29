# Pi and DeepSeek Harness in rootless Podman

Select `agent.name: pi` or `deepseek_harness`. These are generic text/file agents,
not Harbor/TB2 adapters. Each solve gets a disposable rootless Podman container,
workspace, home and session. Do not configure a second sandbox. No host fallback
exists. The host imports neither JavaScript candidates nor the DeepSeek SDK.

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
    # The endpoint limit is 49152; leave room for tokenizer-estimate skew.
    context_window: 47104
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

Only documented fields are accepted: image, model, api_base, api_key,
system_prompt, timeout, max_tokens, context_window, reasoning, runtime (podman only), and the
candidate fields below. `_execution_id` is reserved for the evaluator. Unsupported
model reasoning settings fail in the native harness. DeepSeek uses its official
`sdk-minimal` profile and `deepseek-official` adapter; an endpoint must implement
that adapter's compatible chat protocol. Pi uses an explicit OpenAI-compatible
provider. Set `context_window` below the serving endpoint's model limit by a
safety margin appropriate for client/server tokenizer skew, so Pi compacts
multi-turn sessions before `prompt + max_tokens` exceeds the provider limit.
No host credentials/configuration are discovered by the solvers.

## Candidate modules

Supply `candidate_root`, `candidate_entry` (relative .ts/.js/.mjs module), and
`candidate_digest` together. Compute the digest using
`alphadiana.harness._internal.podman_agent.tree_digest(Path(root))`.
Pi expects an extension's default export. DeepSeek expects a Cordis plugin's
`apply` export, loaded by an explicit local-module patch. Candidate code may use
relative sibling modules and runtime-installed dependencies; install any extra
fixed dependencies when building a new image, never during evaluation.
Symlinks and special files are rejected. The complete candidate is read-only
mounted; runtime compilation/loading happens in the container.

The driver passes problem and public attachments only. Attachments are copied
under `/work/attachments`; ground truth and task metadata are never serialized.
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

## Self-validation

```bash
python -m pytest -c pyproject.toml tests -q -rs
ALPHADIANA_NATIVE_IMAGE=sha256:<built-image-id> \
  python -m pytest -c pyproject.toml tests/test_native_runtime.py -q
```

The runtime tests use actual Pi/DeepSeek tools and a deterministic HTTP model
fixture. They demonstrate integration, plugin loading and cleanup, not live
model quality. Without the image variable those checks explicitly skip.

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
