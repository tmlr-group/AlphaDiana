---
sidebar_position: 5
---

# ZeroClaw

ZeroClaw drives the native `zeroclaw agent` Rust CLI inside a live ROCK sandbox
(or a Podman runtime). For each task the harness generates a per-task
`config.toml`, shells out to the binary, captures its stdout plus a
runtime-trace, and optionally runs a second post-solve `memory_store` agent turn
for persistent-memory experiments.

The key idea: the agent tool loop lives entirely inside the Rust binary.
AlphaDiana sets the ceiling (`max_tool_iterations` is a TOML knob, not a Python
loop) and reads back the result. It never counts tool calls itself.

For the AIME 2026 walkthrough, see the [AIME benchmark page](../benchmarks/aime).
Sibling harnesses:
[direct_llm](./direct-llm), [opencode](./opencode), [openclaw](./openclaw).

## Registration and dispatch

`ZeroClawAgent(Agent)` has `name = "zeroclaw"` and is registered at module load
via `AgentRegistry.register("zeroclaw", ZeroClawAgent)` (a call, not a
decorator). The runner imports `alphadiana.harness.zeroclaw.agent` for its
side-effect registration during `Runner.setup()`.

`solve()` has three execution paths:

| Path | Trigger | Method |
|------|---------|--------|
| Podman runtime | `runtime_backend: podman` | `_run_via_podman_runtime()` (HTTP POST to bridge gateway) |
| Native ROCK sandbox | default; live sandbox present | `_run_in_sandbox()` (main path) |
| Legacy single-host gateway | removed | rejected via `_UNSUPPORTED_SINGLE_PATH_KEYS` |

The native path requires a live sandbox or container session, otherwise it
raises ("now requires a live sandbox/container session"). The removed single-host
gateway keys (`disable_tools`, `multimodal_via_proxy`, `use_gateway_in_sandbox`,
`gateway_api_base`, `gateway_pool`, `gateway_token`, `rock_sandbox_url`,
`sandbox_id`) are rejected unless running under Podman.

## Native runtime flow

`_run_in_sandbox` performs, in order:

1. `pwd`, then `_prepare_paths()` builds a `.alphadiana_zeroclaw/<execution_id>/`
   tree.
2. `_prepare_sandbox_workspace` writes `task.txt` and the generated
   `config.toml`.
3. `_ensure_sandbox_binary` confirms `zeroclaw` is preinstalled in the image (or
   uploads it from `binary_source_image`).
4. Optionally start a `LogprobCaptureProxy` on the host and rewrite the
   in-sandbox `OPENAI_BASE_URL` / `OPENAI_API_KEY` to point at it.
5. Run `_build_run_command` via
   `sandbox.execute_long_running(cmd, wait_timeout=request_timeout + 60, wait_interval=10)`.
6. Read back stdout / stderr / runtime-trace files, build the response with
   `_build_cli_response`, classify errors and timeouts.
7. Best-effort `_memory_store_via_agent` (see [Memory](#memory)).

### The generated CLI command

`_build_run_command` shells the binary directly (no bridge on the native path):

```bash
cd <workspace> && \
  prompt=$(cat task.txt) && \
  timeout <request_timeout> \
    zeroclaw --config-dir <home>/.zeroclaw agent \
      --model <model> \
      --temperature <t> \
      --session-state-file state/zeroclaw-session-state.json \
      -m "$prompt" \
  > stdout 2> stderr
```

The `-m` prompt is a single combined string: the effective system prompt, then
`Problem:\n<problem_text>`. `_build_problem_text` appends a
`--- Workspace Attachments ---` file list and `--- Image Inputs ---`
`[IMAGE:<path>]` markers when present. The `request_messages` array (optional
system message + user message) is recorded in the result artifact but is not how
the CLI is invoked.

### config.toml generation

`_build_config_toml` emits a full TOML config per task. The sections:

| Section | Contents |
|---------|----------|
| top level | `default_provider`, `default_model`, `default_temperature`, `provider_timeout_secs` |
| `[runtime]` | `reasoning_enabled`, `reasoning_effort` (only when set) |
| `[security.sandbox]` | `enabled`, `backend` (only when set) |
| `[memory]` | sqlite + embedding recall (only when enabled, see [Memory](#memory)) |
| `[observability]` | `runtime_trace_mode`, `runtime_trace_path = "state/runtime-trace.jsonl"`, `runtime_trace_max_entries = 200` |
| `[autonomy]` | `level = "full"`, `workspace_only`, `allowed_commands`, hardcoded `forbidden_paths`, `max_actions_per_hour`, `max_cost_per_day_cents = 10000`, `block_high_risk_commands = false`, `shell_timeout_secs` |
| `[shell_tool]` | `timeout_secs` |
| `[agent]` | `max_tool_iterations` |

`max_tool_iterations` (default 100) is written as `[agent]\nmax_tool_iterations = N`
and consumed by the Rust binary's internal tool loop. It is **not** enforced in
Python. The `forbidden_paths` list is fixed:
`/etc, /usr, /bin, /sbin, /lib, /opt, /boot, /dev, /proc, /sys`.

## Memory

Memory is implemented as a **separate second agent invocation** after the solve,
not as part of the solve loop. A "memory" task therefore costs roughly 2x model
turns and depends on the model voluntarily calling the `memory_store` tool.

### Shared HOME

When `persistent_memory: true`, `_prepare_paths` shares HOME across tasks at
`<root>/.alphadiana_zeroclaw/_shared/home`, so the sqlite memory db survives
across tasks in the same long-lived sandbox. This is what makes sequential
memory accumulation possible. Without it, each task gets its own
`<base>/home`. The zeroclaw config dir is always `<home>/.zeroclaw`.

### The `[memory]` section

`_build_memory_section` emits the `[memory]` TOML block **only** when both
`persistent_memory: true` **and** `memory_embedding.base_url` are set:

```toml
[memory]
backend = "sqlite"
auto_save = true
embedding_provider = "custom:<base_url>"
embedding_model = "qwen3-embed-0.6b"
embedding_dimensions = 1024
search_mode = "hybrid"
```

Without an embedding `base_url`, zeroclaw memory falls back to FTS-only (no
`[memory]` section, sqlite default). The `memory_embedding` config is a nested
dict:

| Key | Default |
|-----|---------|
| `base_url` | (required to enable embedding recall) |
| `model` | `qwen3-embed-0.6b` |
| `dimensions` | `1024` |
| `search_mode` | `hybrid` |

### The post-solve memory_store turn

`_memory_store_via_agent` runs a second `zeroclaw agent -m` turn after the solve,
wrapped in `timeout 120`, prompting the model to call the `memory_store` tool.
It is gated by `persistent_memory` and skipped when
`task.metadata['memory_mode'] == 'frozen'` (the transfer-experiment test phase
recalls but does not write). It stashes and restores the solve runtime-trace
(`.solve` / `.store` copies) so the store turn does not truncate it. On success
it increments `_memory_task_count` and runs `zeroclaw memory stats` plus
`zeroclaw memory list | head -40` for verification logging.

### oracle_feedback

`oracle_feedback` (default `false`) changes only the store prompt:

- **`true`**: the store turn is shown the official answer (`task.ground_truth`)
  and the model's own attempt, asked to self-grade CORRECT/WRONG, and store a
  four-tuple (problem, own answer plus correctness, official answer,
  feedback/lesson). Wrong attempts become labeled negative examples.
- **`false`**: stores a bare insight/technique. Preserves the v1 behavior
  bit-for-bit.

### Recall injection and memory_mode

`_has_memories()` returns true only when `persistent_memory` is on **and**
`_memory_task_count > 0`. When true, the next task's system prompt is augmented
with a hint to use `memory_search` before solving (then restored after capturing
the effective system prompt, so the recorded prompt matches `request_messages`).

`task.metadata['memory_mode']` toggles write behavior:

| Value | Behavior |
|-------|----------|
| `build` (default) | solve, then write memory |
| `frozen` | recall only, no store (transfer-experiment test phase) |

### Best-effort store (exp1 None-task fix)

The post-solve `memory_store` is wrapped in `try/except` and is best-effort. A
store failure (for example, a ROCK sandbox auto-cleared after a long solve, or
`run_in_session` raising) must **not** discard an already-solved answer. The code
comment cites this as the documented root cause of the exp1 ZeroClaw `None`
tasks.

## Podman runtime

When `runtime_backend: podman`, `ZeroClawPodmanRuntimeManager` builds a
`PodmanAgentSpec` (`adapter_name = "zeroclaw-podman"`,
`run_command = python <bridge.py>`, an HTTP healthcheck on `/models` accepting
200/404/405, `gateway_token` auth). Auto-rock-sandbox creation is skipped for
Podman. In this mode `disable_tools` and `gateway_token` are the only otherwise
unsupported keys that are allowed.

The ROCK-bridge path passes `max_tool_iterations` via the env var
`ZEROCLAW_MAX_TOOL_ITERATIONS`, which the bridge reads as `MAX_TOOL_ITERATIONS`.
`ZeroClawRuntimeManager._runtime_env()` translates config into `ZEROCLAW_*` env
vars (provider, timeout, max_tokens, reasoning, `MAX_TOOL_ITERATIONS`,
`MAX_ACTIONS_PER_HOUR`, `WORKSPACE_ONLY`, `DISABLE_TOOLS`).

## Configuration reference

Core `agent.config` keys (defaults shown):

| Key | Default | Notes |
|-----|---------|-------|
| `model` / `api_base` / `api_key` | env fallback | `OPENAI_MODEL_NAME` / `OPENAI_BASE_URL` / `OPENAI_API_KEY` |
| `provider` | auto | resolved by `_resolve_zeroclaw_provider` |
| `temperature` | `0.0` | |
| `request_timeout` | `1200` | also drives `provider_timeout_secs` and shell timeouts |
| `provider_max_tokens` / `max_tokens` | unset | |
| `reasoning_enabled` / `reasoning_effort` | unset | emits `[runtime]` |
| `max_tool_iterations` | `100` | TOML knob, not a Python loop |
| `max_actions_per_hour` | `200` | |
| `workspace_only` | `false` | |
| `runtime_trace_mode` | `"none"` | forced to `"full"` when logprobs enabled |
| `system_prompt` | unset | recorded as the effective prompt |

Memory keys:

| Key | Default |
|-----|---------|
| `persistent_memory` | `false` |
| `oracle_feedback` | `false` |
| `memory_embedding` | `{base_url, model, dimensions, search_mode}` |

ROCK-mode keys:

| Key | Example |
|-----|---------|
| `rock_image` | `zeroclaw-reasoning:0.6.9` |
| `rock_memory` | `4g` |
| `rock_cpus` | unset |
| `rock_startup_timeout` | `600` |
| `admin_base_url` | `${ROCK_BASE_URL}` |
| `proxy_base_url` | `${ROCK_PROXY_URL}` |

The values above are illustrative examples, not code defaults. The runner's
actual fallbacks are `rock_memory = "2g"` and `rock_startup_timeout = 300`.

The runner auto-creates the sandbox when `rock_image` is set, so `sandbox: null`
is correct. Binary sourcing is controlled by `binary_source_image` (default
`zeroclaw-reasoning:0.6.9`), `binary_source_path`
(`/usr/local/bin/zeroclaw`), and `binary_source_engine` (`docker` | `podman`).

## Provider resolution

`_resolve_zeroclaw_provider`:

- a `provider` starting with `custom:` is kept as-is;
- `openrouter`, or an `api_base` containing `openrouter`, resolves to `openrouter`;
- any non-`openai.com` `api_base` resolves to `custom:<base>`;
- otherwise it falls back to the configured provider or `openai`.

## Answer extraction and error handling

`_build_cli_response` sets the answer to `answer_override` (the SWE-bench git-diff
patch) when present, otherwise `extract_answer_candidate(sanitized_output)`
(looks for `\boxed{}`). The raw output is sanitized by `_sanitize_cli_output`,
which strips ANSI plus runtime/provider log lines.

Error and timeout classification:

- exit code 124 produces a timeout response (`answer=None`,
  `finish_reason='timeout'`, metadata `zeroclaw_timeout_scored_zero=True`);
- a non-zero exit is classified by `_classify_cli_error_output`
  (`proxy_error` / `provider_error` / `cli_error`) and raised with a partial
  response;
- stdout starting with `error:`, or empty output with logprobs, is handled as
  scored-zero.

For SWE-bench tasks, `_is_swe_bench_task` collects the answer as a `git diff HEAD`
from the repo workdir (`patch_source='git_diff'`), falling back to
`_extract_patch_from_text` (`patch_source='raw_output_patch'`).

## logprob capture

When enabled (or when `provider_proxy_normalize_system_messages` is set), a
`LogprobCaptureProxy` runs on the host, the in-sandbox CLI is pointed at it, and
records are drained into token-entropy stats via
`finalize_logprob_capture(harness="zeroclaw", ...)`. Enabling logprobs forces
`runtime_trace_mode` to `"full"`.

## Running

### Installing the binary

The harness expects `zeroclaw` on `PATH` (in the image for ROCK, or on the host
for local mode). To build it from source you need a Rust toolchain:

```bash
cargo install zeroclawlabs
```

The agent also accepts an in-config fallback. When `command -v zeroclaw` fails
in the sandbox and no `binary_source_image` upload succeeds,
`_ensure_sandbox_binary` raises and points you at `agent.config.install_command`;
set it so the agent runs the install itself:

```yaml
agent:
  config:
    install_command: "cargo install zeroclawlabs"
```

### Local mode (no ROCK)

For a no-infra smoke run (no Docker image, no `start_zeroclaw.sh`, no ROCK
services) use the `local` sandbox. `LocalSession` shells `zeroclaw` directly on
the host via `subprocess` in a `tempfile.mkdtemp(prefix="alphadiana_local_")`
workspace.

A dedicated example config exists at
`configs/examples/zeroclaw_aime2026_local_smoke.yaml`. Note: `ZeroClawAgent.solve`
raises without a live sandbox, and a zeroclaw config with no `rock_image` does
**not** auto-create a ROCK sandbox (`_needs_auto_rock_sandbox` returns false), so
local mode must request the `local` sandbox explicitly rather than leaving
`sandbox: null`:

```yaml
sandbox:
  name: local
```

Then validate and run:

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5

alphadiana validate configs/examples/zeroclaw_aime2026_local_smoke.yaml
alphadiana run      configs/examples/zeroclaw_aime2026_local_smoke.yaml
```

### Stronger host isolation

By default the ROCK worker can mount the host project tree and `.venv` into the
sandbox. To keep the host tree out, set both knobs together:

```bash
# before starting ROCK: pip (or uv) worker env instead of the default 'local'
export ROCK_WORKER_ENV_TYPE=pip
bash scripts/start_zeroclaw.sh
```

```yaml
agent:
  config:
    rock_use_kata_runtime: true   # ROCK uses Kata instead of privileged Docker
```

`rock_use_kata_runtime` maps to the sandbox's `use_kata_runtime` flag in the
runner's auto-sandbox config.

### ROCK mode

ROCK mode prerequisites:

```bash
source scripts/activate.sh

# build the reasoning image (zeroclaw preinstalled)
docker build -f alphadiana/harness/zeroclaw/deploy/Dockerfile -t zeroclaw-reasoning:0.6.9 .

# start host ROCK services, then export the local ROCK URLs
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

Validate then run:

```bash
alphadiana validate configs/examples/zeroclaw_aime2026.yaml
alphadiana run      configs/examples/zeroclaw_aime2026.yaml
```

`bash scripts/start_zeroclaw.sh` runs in a subprocess and cannot export
`ROCK_BASE_URL` / `ROCK_PROXY_URL` back to your shell, so `source
scripts/rock_env.sh` before launching.

A minimal ROCK config:

```yaml
agent:
  name: zeroclaw
  config:
    model: "${OPENAI_MODEL_NAME}"
    api_base: "${OPENAI_BASE_URL}"
    api_key: "${OPENAI_API_KEY}"
    request_timeout: 1200
    max_tool_iterations: 50
    rock_image: "zeroclaw-reasoning:0.6.9"
    rock_memory: "4g"
    rock_startup_timeout: 600
    admin_base_url: "${ROCK_BASE_URL}"
    proxy_base_url: "${ROCK_PROXY_URL}"

benchmark:
  name: aime
  config:
    dataset: "MathArena/aime_2026"
    split: "train"
    max_tasks: 1   # smoke default; remove for a full run

sandbox: null      # auto-created by the runner when rock_image is set
```

## Results layout

- Per-task JSON: `results/<run_id>/tasks/<task_id>.json`
- Per-run JSONL and the result store: written via
  `alphadiana/analysis/io/result_store.py`
- Dashboard: `results/<run_id>/status/dashboard.txt`
- Local logs: `.cache/logs/*.log`

The artifact captures only the initial prompt / `request_messages` and a
runtime-trace summary. It does **not** record the in-sandbox tool loop
step-by-step; the alphadiana artifact is intentionally small.

## Notes

- Two distinct ROCK paths share base config but behave differently:
  `ZeroClawRuntimeManager` bootstraps an HTTP bridge inside a ROCK sandbox, while
  the native `ZeroClawAgent._run_in_sandbox` bypasses the bridge entirely and
  shells `zeroclaw` directly. The bridge is only the gateway/HTTP path.
- The `system_prompt` is recorded as the *effective* prompt (with the memory
  recall hint appended when memories exist), so a grep over results can be
  polluted by this injected text.

See also: [evaluation axes](../concepts/evaluation-axes),
[harness-aware evaluation](../concepts/harness-aware-evaluation),
[isolation and fairness](../concepts/isolation-and-fairness).
