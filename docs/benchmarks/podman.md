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

The repository image and Podman configs pin OpenClaw `2026.3.14`. Runtime
preflight compares the installed binary with `agent.version` and stops with a
clear version-mismatch error before any benchmark request is made.

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

On a shared host, choose another free ZeroClaw bridge port or use rootless
networking to isolate the gateway. The same `bridge_port` value is used for the
listener, exposure, probe, and published URL:

```bash
-o agent.config.podman_network=slirp4netns:allow_host_loopback=true \
-o agent.config.bridge_port=18080 \
-o agent.config.capture_logprobs=true
```

The runtime keeps Podman host aliases, loopback names, the provider host, and
the logprob-proxy host in both `NO_PROXY` and `no_proxy`. Existing outbound
proxy settings remain available for remote providers; users normally do not
need to clear them for a local run.

## Run and audit the matrix

Validate before making real provider requests:

```bash
export PODMAN_SCALE_RUN_PREFIX=podman_scale_$(date +%Y%m%d_%H%M%S)
bash scripts/run_podman_scale_readiness.sh validate
bash scripts/run_podman_scale_readiness.sh gate
```

`gate` attempts every matrix cell, preserves each exit code and raw log, and
then runs the artifact/status audit. It exits nonzero when a child command
fails, a task result is missing or not `valid_scored`, or required provenance,
logs, or artifacts are absent. Use `pilot` only when you intentionally want to
collect execution evidence separately, and use `audit` to re-check an existing
run prefix.

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
  repo-level support because this repository checkout excludes the matching
  result and reviewer-evidence archives.
- Existing benchmark pages document stronger, benchmark-specific historical
  evidence where available.
- No new claim is made here for OpenClaw, a full scale-readiness matrix, global
  default promotion, or removal of Docker/ROCK paths.
