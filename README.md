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

####  Deploy OpenClaw Agent
Before deployment, make sure the following fields in `rock_agent_config.yaml` are set correctly. If you are using `vLLM`, `OPENAI_BASE_URL` must be set to the model service address that is accessible inside the Docker container.
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
# Deploy to ROCK sandbox
python openclaw_deploy/deploy.py \
  --agent-config openclaw_deploy/rock_agent_config.yaml
# Note the SANDBOX_ID from output
```

Verify the gateway before running benchmarks:

```bash
curl -X POST \
  "$ROCK_PROXY_URL/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3?"}],"temperature":0.0,"max_tokens":128}'
```

####  Run Evaluation
Before running the evaluation, make sure to fully complete the `configs/examples/openclaw_aime2024.yaml` file.

```bash
# Validate & run
alphadiana validate configs/examples/openclaw_aime2024.yaml
alphadiana run configs/examples/openclaw_aime2024.yaml
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

Then open `http://<host>:8000` in your browser.



## FAQs

For comprehensive troubleshooting (Docker permissions, Redis connection errors, Ray port conflicts, sandbox container issues, etc.), see the [`docs/setup_detail.md`](docs/setup_detail.md).

