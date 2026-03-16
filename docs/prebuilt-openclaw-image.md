# Pre-built OpenClaw Sandbox Image

## Problem

Every sandbox deployment downloads Node.js and runs `npm install -g openclaw@2026.3.7`,
which takes **10–20 minutes**. Since the openclaw version is pinned, we can bake it
into the Docker image and reduce deploy time to **~1 minute**.

## Prerequisites

- **ROCK sandbox environment** must be running (Ray, ROCK admin, ROCK proxy).
  See [docs/getting_started.md § 4. 搭建 ROCK 沙箱环境](getting_started.md#4-搭建-rock-沙箱环境)
  for setup instructions.
- Note down your ROCK proxy URL (e.g., `http://127.0.0.1:<PROXY_PORT>/apis/envs/sandbox/v1`).
  The port depends on your `dev/.rock_ports.env` configuration.

## Quick Start

```bash
# Pull the pre-built image (one-time)
docker pull ghcr.io/tsrigo/openclaw-sandbox:2026.3.7

# Set the environment variable (add to your .bashrc for persistence)
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
```

That's it. All deploy paths (CLI, dashboard, benchmark runner) will automatically
use the pre-built image and skip the slow npm install.

## Usage: Deploy sandbox + run AIME 2025 benchmark

```bash
# Step 1: Set image and model config
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
export OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
export OPENAI_API_KEY=your-api-key
export OPENAI_MODEL_NAME=Kimi-K2.5

# Step 2: Deploy a sandbox (~1 min with pre-built image)
python -m openclaw_deploy.deploy
# Output example:
#   Sandbox ID: fa6db7ce565a44c79ced5c9ee49f2b02
#   API base: http://127.0.0.1:9051/apis/envs/sandbox/v1/sandboxes/fa6db7ce.../proxy/v1

# Step 3: Edit the config — replace <SANDBOX_ID> and <PROXY_PORT> with your values
#   See configs/examples/openclaw_aime2025.yaml
alphadiana run configs/examples/openclaw_aime2025.yaml
```

> **Important**: `alphadiana run` does NOT deploy sandboxes automatically.
> You must run `python -m openclaw_deploy.deploy` first and fill in the
> sandbox ID in your config YAML before running the benchmark.

### Model config via CLI arguments

```bash
python -m openclaw_deploy.deploy \
  --model-base-url https://ark.cn-beijing.volces.com/api/coding/v3 \
  --model-api-key your-api-key \
  --model-name Kimi-K2.5
```

### Dashboard

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
