# AlphaDiana: A modular evaluation framework for agent systems
## Outline
To run an OpenClaw evaluation, first execute `bash dev/quickstart.sh` from the project root to set up the environment and supporting services. Then, prepare the agent configuration and run `python openclaw_deploy/deploy.py` to deploy the agent and obtain the `sandbox_id`. Once the deployment is complete, insert the `sandbox_id` into `openclaw.yaml`. With the configuration in place, you can launch the evaluation by running `alphadiana run openclaw.yaml`.



## Overview
A modular evaluation framework for foundation models and agent systems on frontier reasoning benchmarks. AlphaDiana provides a pluggable pipeline to evaluate LLM-based reasoning systems with support for multi-turn agentic workflows, sandboxed code execution, and symbolic math verification. 
![Logo](./assets/fig1.jpg)

## Data Flow

```
    YAML Config ──→ Runner ──→ Benchmark (load tasks)
                      │              │
                      │              ▼
                      │         Agent (generate answers)
                      │           │   ↕ Sandbox (code execution)
                      │           ▼
                      │         Scorer (verify answers)
                      │              │
                      ▼              ▼
                 results/<run_id>.jsonl ──→ Report / Dashboard
```

## Installation

### Prerequisites

A Linux environment, Python >= 3.10, conda, Docker, Git, and an OpenRouter (or another provider) API key

### Direct LLM

Run the following command:
```bash
# run
alphadiana run direct_llm.yaml
```

### Quick Start

```bash
git clone <repo-url> 
cd AlphaDiana
bash dev/quickstart.sh
```
For manual setup and troubleshooting, see [`docs/setup_detail.md`](docs/setup_detail.md).


### Per-Terminal Environment Setup

Every new terminal session requires:

```bash
conda activate alphadiana
source dev/rock_env.sh
source dev/.rock_ports.env  
```

`dev/rock_env.sh` sets `PYTHONPATH`, `ROCK_CONFIG`, `ROCK_WORKER_ENV_TYPE`, proxy cleanup, and HuggingFace mirror configuration.

> **Convention**: All commands should be run from the repository root (`AlphaDiana/`) unless explicitly noted otherwise.

### 3. Deploy OpenClaw Agent
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
  --agent-config dev/generated/rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml
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

### 4. Run Evaluation
Before running the evaluation, make sure to fully complete the `openclaw.yaml` file.

```bash
# Validate & run
alphadiana validate openclaw.yaml
alphadiana run openclaw.yaml
```

Results are written to `results/<run_id>.jsonl`.

### 5. View Results

```bash
alphadiana report results/
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

