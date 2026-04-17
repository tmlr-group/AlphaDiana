# AlphaDiana Quickstart Commands

Tested on 2026-03-23. All commands assume you are in the project root (`/path/to/xxx/AlphaDiana-dev`).

## Prerequisites

- Conda environment `alphadiana` with all dependencies installed
- ROCK repository cloned at `ref/ROCK` with `rl-rock` installed
- Docker accessible (for Redis container)
- Ray head node running

## 1. Environment Setup

```bash
# Activate conda and clear proxy variables
eval "$(conda shell.bash hook)"
conda activate alphadiana
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy

# Load ROCK port configuration
source scripts/.rock_ports.env

# Load API keys
export $(grep -v '^#' .env | xargs)
```

### `.env` file contents

```
OPENROUTER_API_KEY=<your-openrouter-key>
OPENAI_BASE_URL=https://openrouter.ai/api/v1
OPENAI_API_KEY=<your-openrouter-key>
OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507
```

### Port configuration (`scripts/.rock_ports.env`)

| Service | Port |
|---------|------|
| Admin   | 9016 |
| Proxy   | 9027 |
| Redis   | 20201 |
| Ray     | 6388 |

## 2. Start ROCK Services

### Redis (Docker container)

```bash
docker start redis-stack 2>/dev/null \
  || docker run -d --restart unless-stopped \
       --name redis-stack \
       -p 20201:6379 \
       redis/redis-stack-server:latest
```

### ROCK Admin

```bash
cd ref/ROCK
nohup python -m rock.admin.main \
  --env local-proxy --role admin --port 9016 \
  > ../../dev/generated/admin.log 2>&1 &
cd -
```

### ROCK Proxy

```bash
cd ref/ROCK
nohup python -m rock.admin.main \
  --env local-proxy --role proxy --port 9027 \
  > ../../dev/generated/proxy.log 2>&1 &
cd -
```

Wait ~3 seconds for services to start, then verify.

## 3. Verify Services

```bash
alphadiana env
```

Expected output:

```
ROCK Environment Status
==================================================
  Ports file:  /path/to/xxx/AlphaDiana-dev/scripts/.rock_ports.env
  Admin:       http://127.0.0.1:9016
  Proxy:       http://127.0.0.1:9027
  Redis:       127.0.0.1:20201
  Ray:         127.0.0.1:6388

Service Health Checks
--------------------------------------------------
  ✓ admin
  ✓ proxy
  ✓ redis
  ✓ docker

All services healthy. Ready for OpenClaw evaluation.
```

## 4. Validate Config

```bash
alphadiana validate configs/test_openclaw_quick.yaml
```

Expected: `Config is valid.`

## 5. Run Evaluation

```bash
alphadiana run configs/test_openclaw_quick.yaml
```

To force re-run (ignore checkpoint):

```bash
alphadiana run configs/test_openclaw_quick.yaml --redo-all
```

### What happens during a run

1. **Pre-flight check** -- verifies admin, proxy, redis are reachable
2. **Sandbox startup** -- creates ROCK sandbox (~12s on first attempt)
3. **Agent init** -- deploys working dir, installs runtime, starts OpenClaw gateway (~25s)
4. **Gateway warmup** -- probes `/v1/models`, then sends a warmup chat completion (~10s)
5. **Task execution** -- sends problem to LLM via OpenClaw, extracts boxed answer
6. **Scoring** -- compares predicted vs ground truth with numeric scorer
7. **Teardown** -- stops sandbox

### Expected output (quick test)

```
Run completed: test-openclaw-quick
  Accuracy:   1.0000
  Mean Score: 1.0000
  Pass@1:    1.0000
  Avg@1:     1.0000
  Tasks:      1/1 completed
```

## 6. Stop Services

Kill admin and proxy by port:

```bash
fuser -k 9016/tcp   # admin
fuser -k 9027/tcp   # proxy
```

Or use the cleanup script (requires `scripts/` to be under a `dev/` path):

```bash
bash scripts/cleanup_rock_ports.sh
```

## Full One-Liner (Copy-Paste)

```bash
eval "$(conda shell.bash hook)" && \
conda activate alphadiana && \
unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy && \
source scripts/.rock_ports.env && \
export $(grep -v '^#' .env | xargs) && \
alphadiana env && \
alphadiana run configs/test_openclaw_quick.yaml --redo-all
```
