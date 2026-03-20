# AlphaDiana: A modular evaluation framework for agent systems


## Overview
AlphaDiana is a modular evaluation framework for foundation models and agent systems on frontier reasoning benchmarks. It offers a pluggable evaluation pipeline to evaluate LLM-based reasoning systems with support for multi-turn agentic workflows, sandboxed code execution, and symbolic math verification.
![Logo](./assets/fig1.jpg)

## Key Features

- 🤖 **OpenClaw Agent Support** — First-class integration with OpenClaw, enabling multi-turn agentic reasoning workflows out of the box
- 🧪 **Sandboxed Code Execution** — Run agent-generated code safely inside Docker-based ROCK sandboxes with full isolation
- 📊 **Multiple Benchmarks** — Built-in support for frontier reasoning benchmarks including AIME, MATH, and more
- 📋 **Web Dashboard** — Visual UI for managing evaluation tasks and viewing results
- 🔗 **Direct LLM Mode** — Evaluate any OpenAI-compatible model directly without agent orchestration

## Quick Start

```bash
git clone <repo-url>
cd AlphaDiana
```
For manual setup and troubleshooting, see [`docs/setup_detail.md`](docs/setup_detail.md).

### Prerequisites

A Linux environment, Python >= 3.10, conda, Docker, Git, and an OpenRouter (or another provider) API key

```bash
# Install dependencies & set up environment
bash dev/quickstart.sh
```


### OpenClaw Agent


```bash
#1. Activate environment (required for every new terminal)

conda activate alphadiana && source dev/rock_env.sh && source dev/.rock_ports.env

#2. Edit config files - openclaw_deploy/rock_agent_config.yaml  — ROCK sandbox & model connection parameters

#3. Deploy OpenClaw agent to ROCK sandbox
python openclaw_deploy/deploy.py \
  --agent-config openclaw_deploy/rock_agent_config.yaml

#4. Edit config files  - configs/examples/openclaw_aime2024.yaml — evaluation parameters

# 5. Run evaluation
alphadiana validate configs/examples/openclaw_aime2024.yaml
alphadiana run configs/examples/openclaw_aime2024.yaml

# 6. View results
alphadiana report results/
```

> **Note:** Default configs are provided in `openclaw_deploy/`. At minimum, set `OPENAI_BASE_URL` and `OPENAI_MODEL_NAME` in `rock_agent_config.yaml` before deploying. For timeout tuning and other details, see [`configs/schema.yaml.md`](configs/schema.yaml.md).

#### Pre-built Image (Faster Deploy)

`bash dev/quickstart.sh` sets up infrastructure but does not deploy OpenClaw — use `bash dev/start_openclaw.sh` for that (~1 min with the pre-built image vs ~15 min with npm install).

Pull the image once (~2.6 GB):

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

Directly evaluate any model without agent - useful for quick baseline
  benchmarking
```bash
# run
alphadiana run configs/examples/direct_llm.yaml
```

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
