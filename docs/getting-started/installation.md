---
sidebar_position: 2
---

# Installation

This page covers installing AlphaDiana, optionally bringing up the ROCK
services that the sandboxed harnesses depend on, and setting the model-provider
environment variables that every run reads.

For the first end-to-end evaluation, continue to [Quickstart](./quick-start).
For per-harness configuration, see the harness pages such as
[direct_llm](../harnesses/direct-llm), [openclaw](../harnesses/openclaw),
[opencode](../harnesses/opencode), and [zeroclaw](../harnesses/zeroclaw).

## Prerequisites

| Requirement | Notes |
|---|---|
| OS | Linux |
| Python | >= 3.10, 3.11/3.12 recommended. Do **not** use 3.13 — ROCK depends on a library that supports only `<= 3.12`. |
| Conda | Used to create an isolated, checkout-local environment. |
| NVIDIA GPU | One card with 40GB+ VRAM (A100/A800) to serve a local model via vLLM. Not required when targeting a hosted API. |
| Docker | Required for the ROCK-backed harnesses (`openclaw`, `zeroclaw`). The current user must be in the `docker` group. |
| Model access | Either a local vLLM endpoint or an API key for a provider such as OpenRouter. |

A GPU is only needed if you serve the model yourself. `direct_llm` and the
agent harnesses can all point at a remote OpenAI-compatible endpoint instead.

## Install

Clone the repository and create the environment. The service bootstrap creates
a checkout-derived conda env (for example `alphadiana-9809e32f`) so that
multiple checkouts on the same host do not collide.

:::warning Current bootstrap prerequisite
`scripts/quickstart.sh` runs `scripts/security_guard.py --check` before the
setup helper gets a chance to generate an OpenClaw gateway token. In a fresh
shell you must export a strong token first. Do not use
`SECURITY_GUARD_BYPASS=1` for normal setup.
:::

```bash
git clone https://github.com/tmlr-group/AlphaDiana
cd AlphaDiana

# Required before quickstart's security preflight in the current release.
export OPENCLAW_GATEWAY_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"

# Checkout-local conda env + dependencies + Redis/Ray/ROCK services.
bash scripts/quickstart.sh
```

The preflight also rejects public Redis/ROCK bindings and other HIGH/CRITICAL
findings. If it stops, fix the reported issue and rerun the same command.

If you only want the package and its dependencies in a plain shared `alphadiana`
env (no ROCK service bring-up), use the standalone installer instead:

```bash
bash installation.sh
```

`installation.sh` honours `ALPHADIANA_ENV` (default `alphadiana`) and
`ALPHADIANA_PYTHON` (default `3.12`), creates the conda env if missing, then runs
`pip install -e .` plus the core runtime dependencies. On shared hosts prefer
`scripts/quickstart.sh`, which derives an isolated per-checkout env name.

### Activate the environment

Run this **once per terminal** before using AlphaDiana. It handles conda
activation, proxy cleanup, ROCK port loading, and `.env` loading:

```bash
source scripts/activate.sh
```

### Serve a model with vLLM (optional)

To run against a local model, install and start vLLM. Any OpenAI-compatible
server works.

```bash
pip install vllm

CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
    --model /path/to/Qwen3-8B \
    --host 127.0.0.1 \
    --port 8000 \
    --trust-remote-code
```

Harnesses that use tool calling (`openclaw`, and the SWE / terminal-bench
agents) need the server started with `--enable-auto-tool-choice
--tool-call-parser <parser>`, where `<parser>` matches the model (for example
`qwen3_coder` for Qwen3.5, `hermes` for Qwen3). `direct_llm` does not require it.
See [openclaw](../harnesses/openclaw) for details.

## ROCK bring-up (optional, for openclaw / zeroclaw)

`direct_llm` and host/Docker-controller OpenCode paths do **not** need ROCK.
Generic ZeroClaw still requires a live sandbox/container session. The ROCK preflight only fires
for ROCK-backed runs (`sandbox.name == 'rock'` or a gateway-autodeploy agent),
checking admin/proxy/Redis reachability and port ownership in
`alphadiana/cli.py`; non-ROCK runs skip it.

`scripts/quickstart.sh` brings these services up for you. For an already
installed checkout, the supported harness launchers are safer than assembling
the stack by hand: they allocate checkout-specific ports, bind Redis and ROCK
to loopback, start or reuse the intended Ray cluster, and inject that cluster's
GCS address into a runtime ROCK config.

```bash
source scripts/activate.sh
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"

# OpenClaw ROCK stack and one gateway sandbox. Provider flags avoid confusing
# the upstream provider URL with agent.config.api_base.
bash scripts/start_openclaw.sh \
  --model-base-url https://provider.example/v1 \
  --model-api-key <provider-key> \
  --model-name <provider-model>

# Or, for a ZeroClaw ROCK stack:
# OPENAI_BASE_URL=https://provider.example/v1 \
# OPENAI_API_KEY=<provider-key> \
# OPENAI_MODEL_NAME=<provider-model> \
# bash scripts/start_zeroclaw.sh
```

The generated allocation includes checkout-specific values such as:

| Service | Example port |
|---|---|
| Admin | 9016 |
| Proxy | 9027 |
| Redis | 20201 |
| Ray | 6388 |

For debugging the low-level sequence, first generate and load the same
checkout-local names and ports used by the launchers:

```bash
python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env
source scripts/rock_env.sh

docker start "$ROCK_REDIS_CONTAINER" 2>/dev/null || \
  docker run -d --restart unless-stopped \
    --name "$ROCK_REDIS_CONTAINER" \
    -p "127.0.0.1:${ROCK_REDIS_PORT}:6379" \
    redis/redis-stack-server:latest
```

Do not substitute `-p ${ROCK_REDIS_PORT}:6379`: without the `127.0.0.1:` host
prefix Docker publishes Redis on every interface. Do not use a fixed
`redis-stack` name either; `$ROCK_REDIS_CONTAINER` includes the user, checkout,
and path hash.

Ray must be started on `$ROCK_RAY_PORT` before admin/proxy. More importantly,
the config passed to admin and proxy must contain
`ray.address: "$ROCK_BIND_HOST:$ROCK_RAY_PORT"`. The checked-in launchers create
that runtime config before invoking `scripts/run_rock_admin_local.py`; the base
file generated by `find_rock_ports.py` is not sufficient by itself. Use those
launchers unless you are debugging the service bootstrap itself.

Verify everything is healthy:

```bash
alphadiana env
```

All four checks should pass:

```text
  ✓ admin
  ✓ proxy
  ✓ redis
  ✓ docker
```

`alphadiana env` also checks whether the configured admin/proxy ports belong to
this checkout. If a foreign worktree owns them, regenerate ports with
`find_rock_ports.py` and restart the local services. To reclaim ports owned by
the current user:

```bash
bash scripts/cleanup_rock_ports.sh
```

## Provider endpoints and agent gateways

Do not treat one base URL as interchangeable across every harness. There are
two different connections:

1. The **provider endpoint** is the OpenAI-compatible model API.
2. The **agent gateway** is a running OpenClaw gateway. For OpenClaw,
   `agent.config.api_base` means this gateway, not the upstream provider.

For `direct_llm`, `zeroclaw`, `opencode`, and the `terminal_bench2_*` agents,
blank provider fields are populated from these environment variables:

| Variable | Maps to agent field | Example |
|---|---|---|
| `OPENAI_BASE_URL` | `api_base` | `http://127.0.0.1:8000/v1` |
| `OPENAI_API_KEY` | `api_key` | `sk-EMPTY` (local vLLM) |
| `OPENAI_MODEL_NAME` | `model` (`model_name` for opencode / tb2 variants) | `Qwen/Qwen3-8B` |

Set them in your shell, or put them in a `.env` file that `scripts/activate.sh`
loads:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3-8B
```

:::warning OpenClaw auto-deploy boundary
The current config loader also fills blank OpenClaw `api_base` from
`OPENAI_BASE_URL`. The runner then interprets that value as an already-running
OpenClaw gateway and can skip ROCK/Podman gateway startup. Until those fields
are separated in code, do not export `OPENAI_BASE_URL` for an OpenClaw
auto-deploy run.

Instead, use a differently named shell variable and pass it to OpenClaw's
provider-specific config key:

```bash
export PROVIDER_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=<provider-key>
export OPENAI_MODEL_NAME=<provider-model>
unset OPENAI_BASE_URL

python -m alphadiana.cli run configs/examples/openclaw_aime2024.yaml \
  -o agent.config.OPENAI_BASE_URL="$PROVIDER_BASE_URL"
```

Set `agent.config.api_base` only when you intentionally want to connect to an
already deployed OpenClaw gateway; pair it with `gateway_token`, not the
provider API key.
:::

:::note Use `sk-EMPTY`, not literal `EMPTY`, for local vLLM
The config validator (`alphadiana/engine/config/validator.py`,
`ConfigValidator`) treats `None`, the empty string, the case-insensitive literal
`EMPTY`, and a string that is wholly an unresolved `${VAR}` placeholder as
**missing**. A local vLLM server ignores the key value, so use any non-`EMPTY`
string such as `sk-EMPTY` or `EMPTY-key`. Literal `EMPTY` fails validation.
:::

Config values may also reference these variables directly, for example
`${OPENAI_BASE_URL}` or `${SANDBOX_ID}`. They are expanded from the shell during
loading; an unresolved placeholder degrades to an empty string rather than
leaking the literal `${VAR}` text.

## Verify the install

Run a config validation and a quick evaluation:

```bash
alphadiana validate configs/examples/direct_llm.yaml
alphadiana run configs/examples/direct_llm.yaml
```

`validate` prints `Config is valid.` on success and exits non-zero with
`  - <error>` lines otherwise. A completed run writes scored records under
`output_dir` (default `./results`); results are persisted through the result
store at `alphadiana/analysis/io/result_store.py`. Regenerate a markdown report
from existing run files with:

```bash
alphadiana report results/
```

## Where things live

| Component | Path |
|---|---|
| CLI entry point | `alphadiana/cli.py` (console script `alphadiana`; module form `python -m alphadiana.cli`) |
| Config dataclass + loader | `alphadiana/engine/config/experiment_config.py` (`ExperimentConfig`) |
| Config validator | `alphadiana/engine/config/validator.py` (`ConfigValidator`) |
| Authoritative config schema | `configs/schema.yaml` |
| Harness implementations | `alphadiana/harness/` (`direct_llm.py`, `openclaw/`, `opencode/`, `zeroclaw/`) |
| Result store | `alphadiana/analysis/io/result_store.py` |

## Next steps

- [Quickstart](./quick-start) — run your first evaluation end to end.
- [direct_llm](../harnesses/direct-llm) — single-turn baseline, no sandbox.
- [openclaw](../harnesses/openclaw) — ROCK-sandboxed agent harness.
- [zeroclaw](../harnesses/zeroclaw) — sandbox/container execution and ROCK auto-deploy modes.
- [opencode](../harnesses/opencode) — host or Docker controller modes.
