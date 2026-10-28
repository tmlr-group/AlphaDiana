# Installation

This page covers installing AlphaDiana, optionally bringing up the ROCK
services that the sandboxed harnesses depend on, and setting the model-provider
environment variables that every run reads.

For the first end-to-end evaluation, continue to [Quickstart](./quick-start.md).
For per-harness configuration, see the harness pages such as
[direct_llm](../harnesses/direct-llm.md), [openclaw](../harnesses/openclaw.md),
[opencode](../harnesses/opencode.md), and [zeroclaw](../harnesses/zeroclaw.md).

## Install the package

Use Python 3.10 or later and an existing OpenAI-compatible model endpoint.
Run from a checkout of this repository:

```bash
git clone https://github.com/AndrewZhou924/AlphaDiana-dev.git
cd AlphaDiana-dev
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[analysis,benchmarks,agents]'
```

The DirectLLM quick start needs neither a GPU nor container services on the
client host. A GPU is needed when you serve the model yourself. OpenCode's
README example additionally needs Podman and its locally built controller image;
see [OpenCode](../harnesses/opencode.md#build-the-podman-controller-image).
Other native runtimes have their own [harness prerequisites](../harnesses/).

Set the provider variables as shown in [Quick Start](quick-start.md).
The checked-in Qwen preset requires streaming, logprobs, and thinking-template
support. Select or adapt a config for another endpoint through
[Configuration](../configuration/).

### Optional ROCK service installation

ROCK-backed runs require Linux, Docker, Conda, and checkout-owned ROCK services.
Use Python 3.10–3.12 for this stack; its dependencies do not support Python 3.13.
The service bootstrap creates a checkout-derived Conda environment so multiple
checkouts can coexist:

```bash
# Required by quickstart's security preflight before token generation.
export OPENCLAW_GATEWAY_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bash scripts/setup/quickstart.sh
source scripts/activate.sh
```

The preflight rejects public Redis/ROCK bindings and HIGH/CRITICAL findings.
Fix reported issues before rerunning. The activation script selects the
checkout-derived environment, loads ROCK ports, and sources `.env`.

The alternative `bash installation.sh` creates a Conda environment named by
`ALPHADIANA_ENV` (default `alphadiana`) with Python selected by
`ALPHADIANA_PYTHON` (default `3.12`), without starting ROCK services. Activate
that environment directly:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${ALPHADIANA_ENV:-alphadiana}"
```

This standalone route does not write quickstart's `scripts/.alphadiana_env`.
For the plain virtual environment above, use `source .venv/bin/activate`.

### Serve a model with vLLM (optional)

To run against a local model, install and start vLLM. Any OpenAI-compatible
server works.

```bash
pip install vllm

CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
    --model /path/to/Qwen3.5-27B \
    --host 127.0.0.1 \
    --port 8000 \
    --trust-remote-code
```

Harnesses that use tool calling (`openclaw`, and the SWE / terminal-bench
agents) need the server started with `--enable-auto-tool-choice
--tool-call-parser <parser>`, where `<parser>` matches the model (for example
`qwen3_coder` for Qwen3.5, `hermes` for Qwen3). `direct_llm` does not require it.
See [openclaw](../harnesses/openclaw.md) for details.

## ROCK bring-up (for ROCK-backed configs)

`direct_llm` and host/Docker-controller OpenCode paths do **not** need ROCK.
Generic ZeroClaw still requires a live sandbox/container session. The ROCK preflight only fires
for ROCK-backed runs (`sandbox.name == 'rock'` or a gateway-autodeploy agent),
checking admin/proxy/Redis reachability and port ownership in
`alphadiana/cli/commands.py`; non-ROCK runs skip it.

`scripts/setup/quickstart.sh` brings these services up for you. For an already
installed checkout, the supported harness launchers are safer than assembling
the stack by hand: they allocate checkout-specific ports, bind Redis and ROCK
to loopback, start or reuse the intended Ray cluster, and inject that cluster's
GCS address into a runtime ROCK config.

```bash
source scripts/activate.sh
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export OPENAI_BASE_URL=https://provider.example/v1
export OPENAI_API_KEY=<provider-key>
export OPENAI_MODEL_NAME=<provider-model>

# OpenClaw ROCK control plane. The runner creates gateway sandboxes.
bash scripts/setup/start_openclaw.sh

# Or, for a ZeroClaw ROCK stack:
# bash scripts/setup/start_zeroclaw.sh
```

Both launchers read provider settings from the environment and accept no model
flags. Passing deployment options to `start_openclaw.sh` exits with status 2.

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
python scripts/setup/find_rock_ports.py --write-env scripts/.rock_ports.env
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
that runtime config before invoking `scripts/setup/run_rock_admin_local.py`; the base
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
bash scripts/setup/cleanup_rock_ports.sh
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
| `OPENAI_MODEL_NAME` | `model` (`model_name` for opencode / tb2 variants) | `Qwen/Qwen3.5-27B` |

Set them in your shell, or put them in a `.env` file that `scripts/activate.sh`
loads:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
```

> [!NOTE] **OpenClaw auto-deploy boundary**
> For a runtime-backed OpenClaw config (`runtime`, `runtime_backend: podman`, or
> a `rock_agent_config_path` / `openclaw_config_path` pair), the loader treats
> `OPENAI_BASE_URL` as the upstream provider endpoint and deliberately leaves
> lower-case `agent.config.api_base` empty so the runner creates the gateway.
> You can therefore use the normal provider variables:
>
> ```bash
> export OPENAI_BASE_URL=https://openrouter.ai/api/v1
> export OPENAI_API_KEY=<provider-key>
> export OPENAI_MODEL_NAME=<provider-model>
>
> python -m alphadiana.cli run configs/macro_runs/aime2026_openclaw_qwen35_27b.yaml \
>   -o run_id=endpoint_demo_openclaw_aime_t1_k1 \
>   -o benchmark.config.max_tasks=1 -o num_samples=1
> ```
>
> Set `agent.config.api_base` only when you intentionally want to connect to an
> already deployed OpenClaw gateway; pair it with `gateway_token`, not the
> provider API key.


> [!NOTE] **Use `sk-EMPTY`, not literal `EMPTY`, for local vLLM**
> The config validator (`alphadiana/engine/config/validator.py`,
> `ConfigValidator`) treats `None`, the empty string, the case-insensitive literal
> `EMPTY`, and a string that is wholly an unresolved `${VAR}` placeholder as
> **missing**. A local vLLM server ignores the key value, so use any non-`EMPTY`
> string such as `sk-EMPTY` or `EMPTY-key`. Literal `EMPTY` fails validation.


Config values may also reference these variables directly, for example
`${OPENAI_BASE_URL}` or `${SANDBOX_ID}`. They are expanded from the shell during
loading; an unresolved placeholder degrades to an empty string rather than
leaking the literal `${VAR}` text.

## Verify the install

Run a config validation and a quick evaluation:

```bash
alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=install_check_aime_directllm_t1_k1 \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1
```

`validate` prints `Config is valid.` on success and exits non-zero with
`  - <error>` lines otherwise. A completed run writes scored records under
`output_dir` (default `./results`); results are persisted through the result
store at `alphadiana/results/store.py`. Regenerate a markdown report
from existing run files with:

```bash
alphadiana report results/
```

## Where things live

| Component | Path |
|---|---|
| CLI entry point | `alphadiana/cli/commands.py` (console script `alphadiana`; module form `python -m alphadiana.cli`) |
| Config dataclass + loader | `alphadiana/engine/config/experiment_config.py` (`ExperimentConfig`) |
| Config validator | `alphadiana/engine/config/validator.py` (`ConfigValidator`) |
| Annotated core config shape | `configs/schema.yaml` |
| Harness implementations | `alphadiana/harness/` (`direct_llm/agent.py`, `openclaw/`, `opencode/`, `zeroclaw/`) |
| Result store | `alphadiana/results/store.py` |

## Next steps

- [Quickstart](./quick-start.md) — run your first evaluation end to end.
- [direct_llm](../harnesses/direct-llm.md) — single-turn baseline, no sandbox.
- [openclaw](../harnesses/openclaw.md) — gateway, ROCK, and Podman runtime paths.
- [zeroclaw](../harnesses/zeroclaw.md) — sandbox/container execution and ROCK auto-deploy modes.
- [opencode](../harnesses/opencode.md) — host, Docker, Podman, and SWE task-container modes.
