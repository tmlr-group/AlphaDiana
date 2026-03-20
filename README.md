# AlphaDiana: A modular evaluation framework for agent systems

## Outline
- [Overview](#overview)
- [Data Flow](#data-flow)
- [Quick Start](#quick-start)
  - [Prerequisites](#prerequisites)
  - [OpenClaw Agent](#openclaw-agent)
    - [Per-Terminal Environment Setup](#per-terminal-environment-setup)
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

#### Per-Terminal Environment Setup

Every new terminal session requires:

```bash
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env  
```

`dev/rock_env.sh` sets `PYTHONPATH`, `ROCK_CONFIG`, `ROCK_WORKER_ENV_TYPE`, proxy cleanup, and HuggingFace mirror configuration.

```bash
#1. Activate environment (required for every new terminal)
conda activate alphadiana && source dev/rock_env.sh && source dev/.rock_ports.env

#2. Edit config files - openclaw_deploy/rock_agent_config.yaml  — ROCK sandbox & model connection parameters

#3. Deploy OpenClaw agent to ROCK sandbox
python openclaw_deploy/deploy.py \
  --agent-config openclaw_deploy/rock_agent_config.yaml

#4. Edit config files  - configs/examples/openclaw_aime2024.yaml — evaluation parameters

#5. Run evaluation
alphadiana validate configs/examples/openclaw_aime2024.yaml
alphadiana run configs/examples/openclaw_aime2024.yaml

#6. View results
alphadiana report results/
```

> **Note:** Default configs are provided in `openclaw_deploy/`. At minimum, set `OPENAI_BASE_URL` and `OPENAI_MODEL_NAME` in `rock_agent_config.yaml` before deploying. For timeout tuning and other details, see [`configs/schema.yaml.md`](configs/schema.yaml.md).

The deploy script uses `DEFAULT_SANDBOX_IMAGE` in `openclaw_deploy/deploy.py`. The current default is:

```text
ghcr.io/tsrigo/openclaw-reasoning:20260320
```

This default can still be overridden with `OPENCLAW_SANDBOX_IMAGE` or `--image`.

The reasoning image contains the patched OpenClaw gateway, including:
- streaming `reasoning_content`
- cleaned reasoning text without underscore formatting
- `max_tokens` forwarding for `/v1/chat/completions`

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

#### Pre-built Image (Faster Deploy)

`bash dev/quickstart.sh` sets up infrastructure but does not deploy OpenClaw — use `bash dev/start_openclaw.sh` for that (~1 min with the pre-built image vs ~15 min with npm install).

```bash
docker pull ghcr.io/tsrigo/openclaw-sandbox:2026.3.7
```

Then start ROCK services and deploy:

```bash
bash dev/start_openclaw.sh \
  --model-base-url https://openrouter.ai/api/v1 \
  --model-api-key  sk-... \
  --model-name     moonshotai/kimi-k2.5
```

On success the script prints `Sandbox ID` and `API base` — copy those into your evaluation YAML.

> For full usage details, see [`dev/README_prebuilt.md`](dev/README_prebuilt.md).

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

Then open `http://<host>:8000` in your browser.



## FAQs

For comprehensive troubleshooting (Docker permissions, Redis connection errors, Ray port conflicts, sandbox container issues, etc.), see the [`docs/setup_detail.md`](docs/setup_detail.md).
