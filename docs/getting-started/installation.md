---
sidebar_position: 2
---

# Installation

This page covers installing AlphaDiana, optionally bringing up the ROCK
services that the sandboxed harnesses depend on, and setting the model-provider
environment variables that every run reads.

For the first end-to-end evaluation, continue to [Quickstart](./quickstart).
For per-harness configuration, see the harness pages such as
[direct_llm](../harnesses/direct-llm), [openclaw](../harnesses/openclaw),
[opencode](../harnesses/opencode), and [zeroclaw](../harnesses/zeroclaw).

## Prerequisites

| Requirement | Notes |
|---|---|
| OS | Linux |
| Python | >= 3.10, 3.11/3.12 recommended. Do **not** use 3.13 — ROCK depends on a library that supports only <= 3.12. |
| Conda | Used to create an isolated, checkout-local environment. |
| NVIDIA GPU | One card with 40GB+ VRAM (A100/A800) to serve a local model via vLLM. Not required when targeting a hosted API. |
| Docker | Required for the ROCK-backed harnesses (`openclaw`, `zeroclaw`). The current user must be in the `docker` group. |
| Model access | Either a local vLLM endpoint or an API key for a provider such as OpenRouter. |

A GPU is only needed if you serve the model yourself. `direct_llm` and the
agent harnesses can all point at a remote OpenAI-compatible endpoint instead.

## Install

Clone the repository and create the environment. The one-click path creates a
checkout-derived conda env (for example `alphadiana-dev-9809e32f`) so that
multiple checkouts on the same host do not collide:

```bash
git clone https://github.com/tmlr-group/AlphaDiana
cd AlphaDiana

# One-click setup: checkout-local conda env + dependencies + services
bash scripts/quickstart.sh
```

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
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code
```

## ROCK bring-up (optional, for openclaw / zeroclaw)

`direct_llm` and the host/Docker-controller harnesses (`opencode`, plus
`zeroclaw` in local mode) do **not** need ROCK. The ROCK preflight only fires
for ROCK-backed runs (`sandbox.name == 'rock'` or a gateway-autodeploy agent),
checking admin/proxy/Redis reachability and port ownership in
`alphadiana/cli.py`; non-ROCK runs skip it.

`scripts/quickstart.sh` brings these services up for you. The manual sequence,
using one checkout's allocated ports, is below. Generate dedicated ports first
so worktrees on a shared host do not collide:

```bash
python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env
source scripts/activate.sh
```

A typical allocation looks like:

| Service | Example port |
|---|---|
| Admin | 9016 |
| Proxy | 9027 |
| Redis | 20201 |
| Ray | 6388 |

Start Redis (as a `redis-stack` container), then the ROCK admin and proxy
processes:

```bash
# Redis (redis-stack container)
docker start redis-stack 2>/dev/null \
  || docker run -d --restart unless-stopped \
       --name redis-stack \
       -p 20201:6379 \
       redis/redis-stack-server:latest

# ROCK admin
cd ref/ROCK
nohup python -m rock.admin.main \
  --env local-proxy --role admin --port 9016 \
  > ../../dev/generated/admin.log 2>&1 &

# ROCK proxy
nohup python -m rock.admin.main \
  --env local-proxy --role proxy --port 9027 \
  > ../../dev/generated/proxy.log 2>&1 &
cd -
```

Ray is started as a head node by the bring-up scripts; run `ray stop` before
re-starting it if a stale head is already listening on the GCS port. For the
turnkey paths use `scripts/start_openclaw.sh` or `scripts/start_zeroclaw.sh`,
then `source scripts/rock_env.sh` so `ROCK_BASE_URL` / `ROCK_PROXY_URL` are
exported into your current shell before `alphadiana run`.

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

## Model-provider environment variables

Most harnesses leave `model`, `api_base`, and `api_key` blank in their YAML and
inherit them from the environment. During config loading,
`ExperimentConfig._apply_agent_env_defaults`
(`alphadiana/engine/config/experiment_config.py`) fills any blank agent field
from these variables for `direct_llm`, `zeroclaw`, `opencode`, and the
`terminal_bench2_*` agents:

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

- [Quickstart](./quickstart) — run your first evaluation end to end.
- [direct_llm](../harnesses/direct-llm) — single-turn baseline, no sandbox.
- [openclaw](../harnesses/openclaw) — ROCK-sandboxed agent harness.
- [zeroclaw](../harnesses/zeroclaw) — local CLI or ROCK auto-deploy modes.
- [opencode](../harnesses/opencode) — host or Docker controller modes.
