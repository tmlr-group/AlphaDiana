# AlphaDiana: A modular evaluation framework for agent systems

## Outline
- [Overview](#overview)
- [Data Flow](#data-flow)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [OpenClaw Agent](#openclaw-agent)
    - [Per-Terminal Environment Setup](#per-terminal-environment-setup)
    - [Pre-built Image (Faster Deploy)](#pre-built-image-faster-deploy)
    - [Deploy OpenClaw Agent](#deploy-openclaw-agent)
    - [Run Evaluation](#run-evaluation)
    - [View Results](#view-results)
  - [Direct LLM](#direct-llm)
- [Dashboard](#dashboard)
  - [Start the Dashboard](#start-the-dashboard)
- [FAQs](#faqs)

## Overview
AlphaDiana is a modular evaluation framework for foundation models and agent systems on frontier reasoning benchmarks. It offers a pluggable evaluation pipeline to evaluate LLM-based reasoning systems with support for multi-turn agentic workflows, sandboxed code execution, and symbolic math verification.
![Logo](./assets/fig1.jpg)

## Data Flow


```
YAML Config ----> Runner -----------> Benchmark (load tasks)
                     |                 |
                     |                 v
                     |                 Agent (generate answers)
                     |                 |   <-> Sandbox (code execution)
                     |                 v
                     |               Scorer (verify answers)
                     |                 |
                     v                 v
                   results/<run_id>.jsonl ----> Report / Dashboard
```


## Quick Start

```bash
git clone <repo-url>
cd AlphaDiana
```
For manual setup and troubleshooting, see [`docs/setup_detail.md`](docs/setup_detail.md).

### Prerequisites

A Linux environment, Python >= 3.10, conda, Docker, Git, and an OpenRouter (or another provider) API key


### OpenClaw Agent

``` bash
bash dev/quickstart.sh
```

> **Convention**: All commands should be run from the repository root (`AlphaDiana/`) unless explicitly noted otherwise.

#### Per-Terminal Environment Setup

Every new terminal session requires:

```bash
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env
```

`dev/rock_env.sh` sets `PYTHONPATH`, `ROCK_CONFIG`, `ROCK_WORKER_ENV_TYPE`, proxy cleanup, and HuggingFace mirror configuration.

#### Pre-built Image (Faster Deploy)

The pre-built sandbox image (`ghcr.io/tsrigo/openclaw-sandbox:2026.3.7`) ships with Node.js and openclaw pre-installed, reducing deploy time from ~15 minutes (npm install) to ~1 minute.

**Step 1 — Pull the image once**

```bash
docker pull ghcr.io/tsrigo/openclaw-sandbox:2026.3.7
```

You only need to do this once per machine. The image is ~2.6 GB.

**Step 2 — Start services and deploy with one command**

`dev/start_openclaw.sh` starts all ROCK services and deploys the pre-built sandbox:

```bash
bash dev/start_openclaw.sh \
  --model-base-url https://openrouter.ai/api/v1 \
  --model-api-key  sk-... \
  --model-name     moonshotai/kimi-k2.5
```

Or equivalently via environment variables:

```bash
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL_NAME="moonshotai/kimi-k2.5"
bash dev/start_openclaw.sh
```

The script prints the `Sandbox ID` and `API base` on success — copy those into your evaluation YAML.

The script is idempotent: it cleans up stale ROCK processes and sandbox containers before starting fresh. Additional options are forwarded to `openclaw_deploy.deploy`:

```bash
bash dev/start_openclaw.sh --memory 8g --cpus 2 --auto-clear-seconds 28800
```

**Using a custom or patched image**

```bash
bash dev/start_openclaw.sh \
  --image my-custom-openclaw:latest \
  --model-base-url ... --model-api-key ... --model-name ...
```

The Dashboard also uses the pre-built image automatically when `OPENCLAW_IMAGE` is set (see [Dashboard](#dashboard)).

#### Deploy OpenClaw Agent

If you prefer to deploy manually after `bash dev/quickstart.sh`, make sure the model configuration is available either through environment variables:

```bash
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="sk-..."
export OPENAI_MODEL_NAME="moonshotai/kimi-k2.5"
```

or via CLI flags:

```bash
python -m openclaw_deploy.deploy \
  --model-base-url https://openrouter.ai/api/v1 \
  --model-api-key sk-... \
  --model-name moonshotai/kimi-k2.5
```

Before running `deploy`, make sure the local ROCK stack is already healthy.
If you used `bash dev/quickstart.sh`, this should already be done. For manual setup,
confirm the following from the repo root:

```bash
docker start "$ROCK_REDIS_CONTAINER" || docker run -d --name "$ROCK_REDIS_CONTAINER" -p 6379:6379 redis/redis-stack-server:latest
curl http://127.0.0.1:$ROCK_ADMIN_PORT/
curl http://127.0.0.1:$ROCK_PROXY_PORT/
ray status
```

Expected result:

- both `curl` commands return `{"message":"hello, ROCK!"}`
- `ray status` shows one active node and no recent failures

If `ray start` fails with `Address already in use`, your current `dev/.rock_ports.env`
is stale for this machine. Regenerate ports via the setup helper before continuing.

When using vLLM, `OPENAI_BASE_URL` must be set to the model service address that is accessible inside the Docker container.

```yaml
working_dir: "."

run_cmd: OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json nohup openclaw gateway >> /root/gateway.log 2>&1 &

runtime_env_config:
  type: node
  npm_registry: https://registry.npmmirror.com
  custom_install_cmd: npm install -g openclaw@2026.3.7
  install_timeout: 1200

env:
  OPENAI_BASE_URL: ""
  OPENAI_API_KEY: "EMPTY"
  OPENAI_MODEL_NAME: ""
  OPENCLAW_GATEWAY_TOKEN: "OPENCLAW"
```

```bash
# Deploy to a new ROCK sandbox
python -m openclaw_deploy.deploy

# Or deploy with a custom ROCK agent config YAML
python -m openclaw_deploy.deploy \
  --agent-config dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml
```

On shared or lower-memory machines, the default sandbox spec may be too large.
If the sandbox stays in `PENDING_CREATION`, try:

```bash
python -m openclaw_deploy.deploy --memory 3g
```

The deploy command prints both a `Sandbox ID` and an `API base`, for example:

```text
Sandbox ID: fa6db7ce565a44c79ced5c9ee49f2b02
API base: http://127.0.0.1:9051/apis/envs/sandbox/v1/sandboxes/fa6db7ce565a44c79ced5c9ee49f2b02/proxy/v1
```

> `alphadiana run` does not deploy sandboxes automatically. Deploy first, then
> copy the printed `api_base` into your evaluation config.

> The generated config file may contain your API key. Do not commit or share it.

#### ROCK Proxy Timeout Configuration (Important)

The default ROCK proxy timeout is too short. During complex evaluations (e.g. AIME), the agent may run much longer than the default, causing requests to be cut off mid-stream and returning empty responses. Please verify the following settings:

1. **ROCK proxy timeout**: In your ROCK installation, open `rock/sandbox/service/sandbox_proxy_service.py` and ensure the `post_proxy` method uses `timeout=self.proxy_config.timeout` instead of a hardcoded value. To increase the timeout, set it in the ROCK YAML config:
   ```yaml
   proxy_service:
     timeout: 600
   ```
2. **Gateway agent timeout**: Already configured in `openclaw_deploy/openclaw.json` as `agents.defaults.timeoutSeconds: 3600`
3. **Client request timeout**: Set via `agent.config.request_timeout` in the experiment YAML (default: 1800s)

Verify the gateway before running benchmarks:

```bash
curl -X POST \
  "$ROCK_PROXY_URL/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3?"}],"temperature":0.0,"max_tokens":128}'
```

If deploy succeeds but this proxy request fails, the issue is in the ROCK proxy path,
not necessarily in sandbox creation or the OpenClaw gateway itself.

#### Run Evaluation

Before running the evaluation, fill in the `api_base` field in your evaluation config
with the `API base` printed by `python -m openclaw_deploy.deploy` (or by `bash dev/start_openclaw.sh`).

```yaml
run_id:
agent:
  name: openclaw
  version: 2026.3.7
  config:
    api_base:
    model: openclaw
    gateway_token: OPENCLAW
    temperature:
    max_tokens: 32768
    system_prompt:

benchmark:
  name: aime
  config:
    dataset: MathArena/aime_2026
    split: train
sandbox: null
scorer:
  name: math_verify
  config:
    tolerance: 1e-6
num_samples: 32
max_concurrent: 16
output_dir: ./results
metadata:
  author:
  gpu:
  notes:
```

You can also start from [`configs/examples/openclaw_aime2025.yaml`](configs/examples/openclaw_aime2025.yaml)
and replace `<PROXY_PORT>` and `<SANDBOX_ID>` with your values.

To avoid editing the example file, you can override `api_base` directly from CLI:

```bash
alphadiana run configs/examples/openclaw_aime2025.yaml \
  -o agent.config.api_base=http://127.0.0.1:<PROXY_PORT>/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1
```

```bash
# Validate & run
alphadiana validate configs/examples/openclaw_aime2025.yaml
alphadiana run configs/examples/openclaw_aime2025.yaml
```

Results are written to `results/<run_id>.jsonl`.

####  View Results

```bash
alphadiana report results/
```

### Direct LLM

Run the following command:
```bash
# run
alphadiana run configs/examples/direct_llm.yaml
```


## Dashboard

A web UI for managing and viewing evaluation tasks without manually editing YAML or reading JSONL files.

### Start the Dashboard

```bash
uvicorn alphadiana.dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

For faster sandbox deploys from the Dashboard, set `OPENCLAW_IMAGE` before starting
the backend server:

```bash
export OPENCLAW_IMAGE=ghcr.io/tsrigo/openclaw-sandbox:2026.3.7
uvicorn alphadiana.dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open `http://<host>:8000` in your browser.



## FAQs

For comprehensive troubleshooting (Docker permissions, Redis connection errors, Ray port conflicts, sandbox container issues, etc.), see the [`docs/setup_detail.md`](docs/setup_detail.md).

Common issues at a glance:

| Issue | Cause | Fix |
|---|---|---|
| `permission denied` on Docker socket | Current shell lacks `docker` group | Run `newgrp docker` or re-login |
| `deploy.py` sandbox timeout | Redis not running, ROCK/Ray not healthy, sandbox memory too high, or Ray `runtime_env` dependency install failed | Start Redis, check `curl http://127.0.0.1:$ROCK_ADMIN_PORT/`, `curl http://127.0.0.1:$ROCK_PROXY_PORT/`, `ray status`, retry with `--memory 3g`, then inspect `/tmp/kai-ray/session_latest/logs/runtime_env_setup-*.log` |
| Ray port `8265` already in use | Shared server port conflict | Use `find_rock_ports.py` to detect free ports |
| `ray start` fails with `Address already in use` on configured ports | `dev/.rock_ports.env` is stale or conflicts with existing services | Regenerate ports via the setup helper, then restart Redis / Ray / ROCK |
| `rock.admin.main` Redis `ConnectionError` | Redis container not started | `docker start "$ROCK_REDIS_CONTAINER"` |
| Sandbox container exits immediately (`Container process terminated`) | `rock` editable install path mismatch — switched project directories without re-running `pip install -e .` | `pip install -e ref/ROCK -q` then retry |
| Proxy `/chat/completions` fails after successful deploy | ROCK proxy forwarding issue | Check ROCK proxy logs and verify whether the sandbox-exposed gateway port works directly |
