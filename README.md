# AlphaDiana: A System for Evaluating Agentic Reasoning

**AlphaDiana** is a modular evaluation system for agentic reasoning with foundation models. It makes it easy to benchmark **reasoning agents** (e.g., OpenClaw) with **advanced models** (e.g., Qwen 3.5) on **frontier tasks** (e.g., AIME 2026) using pluggable benchmarks, sandbox backends, and scorers.

<p align="center">
  <img src="./assets/fig1.jpg" width="100%">
</p>
<p align="center">
  <em><strong>Figure 1.</strong> Why agentic reasoning requires system-level evaluation: foundation models are evaluated like engines, agents behave like cars shaped by tools and state, and AlphaDiana serves as the tournament organizer that standardizes evaluation and records canonical traces.</em>
</p>

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

Recommended minimal path: use the single-sandbox dynamic example first. It is the quickest way to verify that the full system can run end-to-end on a fresh machine.


```bash
#1. Activate environment (required for every new terminal)

conda activate alphadiana && source dev/rock_env.sh && source dev/.rock_ports.env
#2. Pull the recommended reasoning image once
docker pull ghcr.io/tsrigo/openclaw-reasoning:20260320

#3. Export your model provider settings
export OPENAI_BASE_URL=...
export OPENAI_API_KEY=...
export OPENAI_MODEL_NAME=...

#4. Run the single-sandbox dynamic example
alphadiana validate configs/examples/openclaw_aime2024_dynamic.yaml
alphadiana run configs/examples/openclaw_aime2024_dynamic.yaml

#5. View results
alphadiana report results/
```

This example now defaults to a single sandbox, `4g` memory, `1` CPU, and `max_concurrent: 1` to maximize first-run success.

For higher concurrency, you can still use the dashboard deploy-and-run flow or pre-deploy multiple sandboxes; we will keep improving the CLI high-concurrency path separately.

If you prefer the manual deploy flow, use `openclaw_deploy/rock_agent_config.prebuilt.yaml` with the reasoning image. The static examples (`openclaw_aime2024.yaml`, `openclaw_aime2025.yaml`) assume you already deployed a sandbox and only need `SANDBOX_ID`.

> **Important:** `openclaw_deploy/rock_agent_config.prebuilt.yaml` is the right config for the pre-built reasoning image. `openclaw_deploy/rock_agent_config.yaml` is the fallback path for a bare image that installs `openclaw@2026.3.7` at runtime, which is slower and not the recommended flow.

> **Note:** Default configs are provided in `openclaw_deploy/`. At minimum, set `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL_NAME` before running OpenClaw. For timeout tuning and other details, see [`configs/schema.yaml.md`](configs/schema.yaml.md).

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

`bash dev/quickstart.sh` sets up Python, Ray, ROCK admin/proxy, and Redis, but it does not deploy OpenClaw. To get a working sandbox, run `bash dev/start_openclaw.sh` or `python openclaw_deploy/deploy.py ...` afterward.

Pull the image once (~2.6 GB):

```bash
docker pull ghcr.io/tsrigo/openclaw-reasoning:20260320
```

Then start ROCK services and deploy:

```bash
bash dev/start_openclaw.sh \
  --model-base-url https://openrouter.ai/api/v1 \
  --model-api-key  sk-... \
  --model-name     moonshotai/kimi-k2.5
```

On success the script prints `Sandbox ID` and `API base` — copy those into your evaluation YAML.

Common failure modes:
- `Failed to start sandbox within 60.0s` usually means the image is still the old `ghcr.io/tsrigo/openclaw-sandbox:2026.3.7`, or Docker/image pull is slow. Use `ghcr.io/tsrigo/openclaw-reasoning:20260320` and retry.
- `async start sandbox failed` with `Get timed out: some object(s) not ready.` usually points to ROCK/Ray startup issues or stale ports. Re-run `source dev/rock_env.sh && source dev/.rock_ports.env`, confirm `bash dev/quickstart.sh` completed cleanly, and make sure Docker, Ray, ROCK admin/proxy, and Redis are all alive.

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
