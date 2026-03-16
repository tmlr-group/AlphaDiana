# Pre-built OpenClaw Sandbox Image

## Problem

Every sandbox deployment downloads Node.js and runs `npm install -g openclaw@2026.3.7`,
which takes **10–20 minutes**. Since the openclaw version is pinned, we can bake it
into the Docker image and reduce deploy time to **~1 minute**.

## Quick Start

```bash
# Pull the pre-built image (one-time)
docker pull ghcr.io/tsrigo/openclaw-sandbox:2026.3.7

# Set the environment variable (add to your .bashrc for persistence)
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
```

That's it. All deploy paths (CLI, dashboard, benchmark runner) will automatically
use the pre-built image and skip the slow npm install.

## Usage Scenarios

### 1. Deploy a sandbox + run AIME 2025 benchmark (end-to-end)

```bash
# Step 1: Set image and model config
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
export OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
export OPENAI_API_KEY=your-api-key
export OPENAI_MODEL_NAME=Kimi-K2.5

# Step 2: Deploy a sandbox (takes ~1 min with pre-built image)
python -m openclaw_deploy.deploy
# Output: Sandbox ID: <SANDBOX_ID>
# Output: API base: http://127.0.0.1:9051/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1

# Step 3: Run benchmark with the sandbox
alphadiana run configs/examples/openclaw_aime2025.yaml
```

The example config `configs/examples/openclaw_aime2025.yaml`:

```yaml
run_id: "openclaw-aime2025-001"

agent:
  name: openclaw
  config:
    api_base: "http://localhost:9051/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    system_prompt: |
      You are an expert problem solver. When given a problem, actively use your
      available tools and skills throughout your reasoning process. Do not attempt
      to solve problems purely in your head when tools can help.

      When you have reached your final answer, present it as: $$\boxed{your answer here}$$

benchmark:
  name: aime
  config:
    dataset: "MathArena/aime_2025"
    split: "train"

sandbox: null   # OpenClaw already runs inside ROCK; no extra sandbox needed

scorer:
  name: math_verify

max_concurrent: 1
output_dir: "./results"
```

> **Note**: Replace `<SANDBOX_ID>` with the actual sandbox ID from step 2.

### 2. Deploy a standalone sandbox (CLI)

Model config via CLI arguments:

```bash
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7

python -m openclaw_deploy.deploy \
  --model-base-url https://ark.cn-beijing.volces.com/api/coding/v3 \
  --model-api-key your-api-key \
  --model-name Kimi-K2.5
```

Or via environment variables (same effect):

```bash
export OPENCLAW_IMAGE=openclaw-sandbox:2026.3.7
export OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
export OPENAI_API_KEY=your-api-key
export OPENAI_MODEL_NAME=Kimi-K2.5

python -m openclaw_deploy.deploy
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
