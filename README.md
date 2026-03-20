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

#5. Run evaluation
alphadiana validate configs/examples/openclaw_aime2024.yaml
alphadiana run configs/examples/openclaw_aime2024.yaml

#6. View results
alphadiana report results/
```

> **Note:** Default configs are provided in `openclaw_deploy/`. At minimum, set `OPENAI_BASE_URL` and `OPENAI_MODEL_NAME` in `rock_agent_config.yaml` before deploying. For timeout tuning and other details, see [`configs/schema.yaml.md`](configs/schema.yaml.md).

The deploy flow uses `DEFAULT_SANDBOX_IMAGE` from `alphadiana/utils/rock_runtime.py`. The current default is:

```text
ghcr.io/tsrigo/openclaw-reasoning:20260320
```

This default can still be overridden with `OPENCLAW_SANDBOX_IMAGE` or `--image`.

The reasoning image contains the patched OpenClaw gateway, including:
- streaming `reasoning_content`
- cleaned reasoning text without underscore formatting
- `max_tokens` forwarding for `/v1/chat/completions`

To verify that the reasoning-enabled path is active after deploy, call the ROCK proxy endpoint with `"stream": true` and check that SSE chunks contain `delta.reasoning_content`:

```bash
curl -N --location "http://localhost:<ROCK_PORT>/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  --header "Authorization: bearer OPENCLAW" \
  --header "Content-Type: application/json" \
  --data '{
    "model": "openclaw",
    "stream": true,
    "max_tokens": 32768,
    "messages": [
      {
        "role": "user",
        "content": "Reply with ok."
      }
    ]
  }'
```

If you are testing long reasoning runs, also watch for infrastructure-side failures such as proxy timeout, `post proxy failed`, or sandbox actor disappearance before the request reaches `finish_reason=length`.

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

Then open `http://<host>:8000` in your browser.



## FAQs

For comprehensive troubleshooting (Docker permissions, Redis connection errors, Ray port conflicts, sandbox container issues, etc.), see the [`docs/setup_detail.md`](docs/setup_detail.md).
