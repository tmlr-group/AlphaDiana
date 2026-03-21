# OpenClaw Reasoning Image Repro Guide

This guide is for validating the current `AlphaDiana` OpenClaw + ROCK flow on a **new machine**.

Goal:

- start a local ROCK environment
- deploy an OpenClaw sandbox from the patched reasoning image
- verify that `ROCK proxy -> /v1/chat/completions` streams `reasoning_content`
- keep the model configurable through environment variables instead of hardcoding it in the repo

## What This Uses

- AlphaDiana branch: the current checked-out branch in this repository
- Default image: `ghcr.io/tsrigo/openclaw-reasoning:20260320`
- Default deploy entry:
  - `openclaw_deploy/deploy.py`
  - `openclaw_deploy/rock_agent_config.yaml`
  - `openclaw_deploy/openclaw.json`

The image is expected to contain:

- patched OpenClaw with streamed `reasoning_content`
- plain-text reasoning chunks without `_This_ is_ ...` formatting

## Recommended Image Distribution

Do **not** put the image into the Git repository itself.

Recommended:

- publish it to **GitHub Container Registry (GHCR)**
- for example:
  - `ghcr.io/tsrigo/openclaw-reasoning:20260320`

Why:

- Docker images are binary artifacts and are not suitable for Git history
- GHCR keeps the repo clean and makes fresh-machine validation much simpler
- the repro script can `docker pull` the image directly

Until the image is published, you can still use the script with:

- access to `ghcr.io/tsrigo/openclaw-reasoning:20260320`
- or override `OPENCLAW_IMAGE` to another available tag

## Prerequisites

- Ubuntu/Linux host with Docker working for the current user
- Conda installed
- Git installed
- This repository checked out locally

Optional but recommended:

- enough free RAM for local ROCK + sandbox startup

## Required Runtime Variables

The deploy flow keeps the OpenClaw model configurable through environment variables:

```bash
export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export OPENAI_API_KEY="sk-or-..."
export OPENAI_MODEL_NAME="moonshotai/kimi-k2.5"
```

You can replace them with:

- a vLLM endpoint accessible from inside the sandbox
- another OpenAI-compatible provider
- another model name

## One-Command Repro

After cloning this repository:

```bash
bash repro/run_openclaw_reasoning_image.sh
```

If you want to override the image:

```bash
OPENCLAW_IMAGE="ghcr.io/tsrigo/openclaw-reasoning:20260320" \
OPENAI_BASE_URL="https://openrouter.ai/api/v1" \
OPENAI_API_KEY="sk-or-..." \
OPENAI_MODEL_NAME="moonshotai/kimi-k2.5" \
bash repro/run_openclaw_reasoning_image.sh
```

## What the Script Does

1. Runs `dev/quickstart.sh alphadiana`
2. Sources:
   - `dev/rock_env.sh`
   - `dev/.rock_ports.env`
3. Verifies that the configured image exists locally or can be pulled
4. Creates a temporary deploy working directory
5. Copies:
   - `openclaw_deploy/openclaw.json`
   - `openclaw_deploy/rock_agent_config.yaml`
6. Rewrites only the runtime model fields in the temporary copy:
   - `OPENAI_BASE_URL`
   - `OPENAI_API_KEY`
   - `OPENAI_MODEL_NAME`
7. Deploys the sandbox through:
   - `openclaw_deploy/deploy.py`
8. Prints:
   - `SANDBOX_ID`
   - the final ROCK proxy base URL
   - a ready-to-run `curl` command
9. Runs a short streamed smoke test automatically

## Expected Success Signal

Successful smoke output should look like:

```text
status 200
data: ... "delta":{"role":"assistant"}
data: ... "delta":{"reasoning_content":"Reasoning:\nThis"}
data: ... "delta":{"reasoning_content":" is"}
```

Important:

- you should see `reasoning_content`
- the reasoning text should be plain text, not `_This_ is_ ...`

## Manual Validation After Deploy

The script prints a ready-to-run command, but the shape is:

```bash
curl -N \
  "http://127.0.0.1:<ROCK_PROXY_PORT>/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1/chat/completions" \
  -H "Authorization: Bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw",
    "stream": true,
    "max_tokens": 32768,
    "messages": [
      {
        "role": "user",
        "content": "Let $\\triangle ABC$ be a right triangle with $\\angle A = 90^\\circ$ and $BC = 38$. There exist points $K$ and $L$ inside the triangle such that $$AK = AL = BK = CL = KL = 14.$$ The area of the quadrilateral $BKLC$ can be expressed as $n\\sqrt{3}$ for some positive integer $n$. Find $n$. Reasoning step by step."
      }
    ]
  }'
```

## Why the Gateway Defaults Changed

These settings are not model-specific. They are needed so that ROCK proxy can actually reach OpenClaw inside the sandbox:

- `gateway.port = 8080`
- `gateway.bind = custom`
- `gateway.customBindHost = 0.0.0.0`
- `gateway.controlUi.enabled = false`
- `gateway.http.endpoints.chatCompletions.enabled = true`

Without them:

- OpenClaw may only listen on container loopback
- Docker port mapping cannot reach it
- ROCK proxy returns `502`

## Known Practical Notes

- `deploy.py` may fail on a dirty local ROCK state if an old sandbox actor is still holding resources
- if that happens, stop old sandboxes before retrying
- on first use, sandbox startup can still take a while

## Files Involved

- `openclaw_deploy/deploy.py`
- `openclaw_deploy/rock_agent_config.yaml`
- `openclaw_deploy/openclaw.json`
- `alphadiana/agent/openclaw.py`
- `tests/test_fix_empty_response.py`
