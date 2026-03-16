# Pre-built OpenClaw Sandbox Image

## Problem

Every sandbox deployment downloads Node.js and runs `npm install -g openclaw@2026.3.7`,
which takes **10–20 minutes**. Since the openclaw version is pinned, we can bake it
into the Docker image and reduce deploy time to **~1 minute**.

## Quick Start

```bash
# Pull the pre-built image (one-time)
docker pull ghcr.io/tsrigo/openclaw-sandbox:2026.3.7

# Set the environment variable
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
```

That's it. All deploy paths (CLI, dashboard, benchmark runner) will automatically
use the pre-built image and skip the slow npm install.

## Usage Scenarios

### 1. Deploy a standalone sandbox (CLI)

```bash
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7

python -m openclaw_deploy.deploy \
  --model-base-url https://api.openai.com/v1 \
  --model-api-key sk-xxx \
  --model-name gpt-4o
```

Model config can also be passed via environment variables:

```bash
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_API_KEY=sk-xxx
export OPENAI_MODEL_NAME=gpt-4o

python -m openclaw_deploy.deploy
```

### 2. Run a benchmark with OpenClaw agent

The benchmark runner reads the same `OPENCLAW_IMAGE` variable. No code changes needed:

```bash
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_API_KEY=sk-xxx
export OPENAI_MODEL_NAME=gpt-4o

python -m alphadiana.run \
  --agent openclaw \
  --benchmark aime \
  --benchmark-config '{"dataset": "HuggingFaceTB/aime_2024", "split": "train"}' \
  --sandbox rock \
  --sandbox-config '{"admin_base_url": "http://127.0.0.1:9050", "proxy_base_url": "http://127.0.0.1:9051"}'
```

### 3. Dashboard

Set `OPENCLAW_IMAGE` before starting the dashboard server. All "Deploy & Run"
jobs will use the pre-built image automatically.

```bash
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
python -m alphadiana.dashboard.backend.main
```

## Build Your Own Image

If you need a different openclaw version or customizations:

```bash
cd openclaw_deploy
# Edit the Dockerfile if needed (e.g., change openclaw version)
docker build -t openclaw-sandbox:2026.3.7 .
```

## How It Works

When `OPENCLAW_IMAGE` is set to a non-`python:3.11` value:

1. **Image**: Sandboxes are created with the pre-built image instead of bare `python:3.11`
2. **Node.js**: Already at `/opt/node` in the image; ROCK creates a symlink instead of downloading
3. **openclaw**: Already installed globally; `openclaw --version` verifies it in <1s
4. **Agent config**: Uses `rock_agent_config.prebuilt.yaml` (optimized for pre-built image)

When `OPENCLAW_IMAGE` is **not set**, everything falls back to `python:3.11` with
the original `rock_agent_config.yaml` and full npm install. Zero behavior change.

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENCLAW_IMAGE` | No | Docker image name. Defaults to `python:3.11` |
| `OPENAI_BASE_URL` | Yes* | Model provider API base URL |
| `OPENAI_API_KEY` | Yes* | Model provider API key |
| `OPENAI_MODEL_NAME` | Yes* | Model name to use |

\* Required when using `deploy.py` with `rock_agent_config.prebuilt.yaml`.
  The dashboard and benchmark runner resolve these from their own config.
