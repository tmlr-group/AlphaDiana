---
sidebar_position: 10
---

# Podman Runtime Readiness

AlphaDiana has opt-in rootless Podman paths for OpenClaw, ZeroClaw, OpenCode,
and several task-container benchmarks. Podman readiness is path-specific: a
passing AIME smoke proves that harness, image, network, provider, result, and
artifact path; it does not promote Podman to the global default or prove every
benchmark.

## Build the repository images

Build images from the checked-in definitions instead of trusting a mutable
local tag left by an older experiment:

```bash
podman build \
  -f alphadiana/harness/zeroclaw/deploy/Dockerfile \
  -t localhost/zeroclaw-reasoning:0.6.9 \
  alphadiana/harness/zeroclaw/deploy

podman build \
  -f alphadiana/harness/opencode/deploy/Containerfile.podman-controller \
  -t localhost/alphadiana-opencode-podman:latest \
  alphadiana/harness/opencode/deploy

podman build \
  -f alphadiana/harness/openclaw/deploy/Dockerfile \
  -t localhost/alphadiana-openclaw:latest \
  alphadiana/harness/openclaw/deploy
```

:::warning OpenClaw version mismatch
The checked-in OpenClaw image currently installs `openclaw@2026.3.7`, while
the scale-readiness YAMLs declare `agent.version: 2026.3.20`. Validation does
not compare that label with the binary. Do not treat an OpenClaw matrix cell as
reproducible evidence until the image pin and config version are aligned and
the binary version is recorded. This is included in the consolidated
[runtime follow-up](../contribution/runtime-followups).
:::

Verify the required executable and Python runtime before starting a pilot. This
avoids waiting through a gateway timeout when a local tag points at an
incompatible historical image:

```bash
podman run --rm --network none localhost/zeroclaw-reasoning:0.6.9 \
  sh -lc 'python --version && zeroclaw --version && python -c "import scipy"'

podman run --rm --network none localhost/alphadiana-opencode-podman:latest \
  bash -lc 'python --version && opencode --version && python -c "import scipy"'
```

## Provider and cache setup

For the checked-in scale-readiness matrix, the local provider is reached with
Podman host networking:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B

export HF_HOME=/path/to/writable/hf
export HF_DATASETS_CACHE=$HF_HOME/datasets
export HUGGINGFACE_HUB_CACHE=$HF_HOME/hub

curl -fsS "$OPENAI_BASE_URL/models"
```

Use a non-literal placeholder such as `sk-EMPTY`. Environment-default
resolution treats the literal `EMPTY` as blank, while path-specific validation
and runtime behavior varies; do not use it. The HF cache must be writable. If
a dataset is already cached and external access is intentionally disabled, add
`HF_HUB_OFFLINE=1` and `HF_DATASETS_OFFLINE=1`.

Run the repository security guard before starting any ROCK/OpenClaw/ZeroClaw
runtime. The OpenClaw matrix consumes `OPENCLAW_GATEWAY_TOKEN`, so export a
strong, non-production value before both the guard and pilot, and never commit
it:

```bash
export OPENCLAW_GATEWAY_TOKEN="$(python3 -c \
  'import secrets; print(secrets.token_urlsafe(32))')"
python scripts/security_guard.py --check
```

## Network choices

The scale-readiness configs use `podman_network: host`. This makes a loopback
vLLM endpoint reachable from inside the agent container, but the gateway's
listening port must be free on the host. Check it before launching:

```bash
ss -ltn | grep ':8080 '
```

On a shared host, rootless networking can isolate the gateway and publish a
dynamic host port. ZeroClaw can use this smoke-only override with the current
runtime:

```bash
-o agent.config.podman_network=slirp4netns:allow_host_loopback=true \
-o agent.config.bridge_port=8080 \
-o agent.config.capture_logprobs=true
```

The logprob proxy then advertises the provider through
`host.containers.internal`. Remove inherited HTTP proxy variables for this
local-only run, or ensure both `NO_PROXY` and `no_proxy` contain
`host.containers.internal`; otherwise the local request may be sent to the
shell's outbound proxy.

`bridge_port` does not currently change the ZeroClaw bridge process's hardcoded
listen port. Keep it at `8080` for the rootless mapping workaround. The code
follow-up and acceptance tests are tracked in
[Runtime follow-ups](../contribution/runtime-followups).

## Run and audit the matrix

Validate before making real provider requests:

:::caution Matrix is diagnostic until two follow-ups land
The launcher does not currently require `OPENCLAW_GATEWAY_TOKEN`; omitting the
export above can fall back to a weak default. It also does not reject the
OpenClaw image/config version mismatch. The commands below are suitable for
collecting diagnostics, but their OpenClaw cells are non-authoritative until
both checks are implemented. For bounded work, invoke the intended ZeroClaw or
OpenCode cell config directly instead of treating the full matrix as a gate.
:::

```bash
export PODMAN_SCALE_RUN_PREFIX=podman_scale_$(date +%Y%m%d_%H%M%S)
bash scripts/run_podman_scale_readiness.sh validate
bash scripts/run_podman_scale_readiness.sh pilot
bash scripts/run_podman_scale_readiness.sh audit
```

Until the fail-closed launcher follow-up is implemented, `pilot` records each
cell's exit code but returns success after failures. It is evidence collection,
not the readiness gate. Inspect its status TSV and raw logs, then run `audit` as
the required artifact/status check. Audit does not make OpenClaw cells
authoritative until the token and version preflights land; a shell exit code
from `pilot` alone is not a pass signal.

The full pilot is `3 harnesses x 4 benchmarks x 3 tasks`; it is not a quick
smoke. For a bounded diagnostic, run one checked-in cell directly and override
`benchmark.config.max_tasks=1`, the output budget, timeout, and a unique
`run_id`. Do not use the result as accuracy evidence when the budget is reduced.

Inspect `results/<run_id>/tasks/*.json` as a JSON list and read `data[0]`.
Execution-path success requires `error: null` and
`score_status: valid_scored`; `score` may be `0` or `1`. Also inspect the raw
`logs/<run_id>.log`, trajectory, provider exchange, and logprob sidecars.

## Full-run pre-flight checklist

Before a TerminalBench2 or other task-container sweep, complete these host
checks in addition to the benchmark-specific preflight:

- Confirm rootless Podman can create containers and that the user's kernel
  keyring/quota is not exhausted.
- Put results, task logs, and dual logprob sidecars on a large data volume; do
  not assume the home filesystem can hold a full sweep.
- Confirm every selected image digest and run its required binary/Python
  preflight. TerminalBench2 may use the thinner controllers under
  `docker/terminal_bench2/`; historical configs may call the OpenClaw image
  `localhost/alphadiana-openclaw-fixed:latest`.
- Confirm the network mode matches provider reachability. A loopback provider
  requires host networking unless the harness explicitly advertises a host
  proxy through rootless networking.
- Probe `/v1/models` from the same Podman network mode and watch vLLM running,
  waiting, KV-cache, and validation-error metrics while ramping concurrency.
- Check free disk/inodes and GPU ownership before launch. Never stop or reuse
  another user's processes or containers on a shared host.
- After an interrupted run, remove only containers owned by that run, verify
  its listener ports are released, inspect the raw log, and resume the same run
  ID from checkpoint rather than deleting completed task JSONs.

## Support boundary

- This runbook documents opt-in diagnostic paths; it does not itself establish
  repo-level support because this website checkout excludes the matching
  result and reviewer-evidence archives.
- Existing benchmark pages document stronger, benchmark-specific historical
  evidence where available.
- No new claim is made here for OpenClaw, a full scale-readiness matrix, global
  default promotion, or removal of Docker/ROCK paths.
