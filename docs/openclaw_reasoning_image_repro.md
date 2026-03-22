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


If you prefer the manual deploy flow, use `openclaw_deploy/rock_agent_config.prebuilt.yaml` with the reasoning image. The static examples (`openclaw_aime2024.yaml`, `openclaw_aime2025.yaml`) assume you already deployed a sandbox and only need `SANDBOX_ID`.

> **Important:** `openclaw_deploy/rock_agent_config.prebuilt.yaml` is the right config for the pre-built reasoning image. `openclaw_deploy/rock_agent_config.yaml` is the fallback path for a bare image that installs `openclaw@2026.3.7` at runtime, which is slower and not the recommended flow.

> **Note:** Default configs are provided in `openclaw_deploy/`. At minimum, set `OPENAI_BASE_URL`, `OPENAI_API_KEY`, and `OPENAI_MODEL_NAME` before running OpenClaw. For timeout tuning and other details, see [`configs/schema.yaml.md`](configs/schema.yaml.md).

The deploy flow uses `DEFAULT_SANDBOX_IMAGE` from `alphadiana/utils/rock_runtime.py`. The current default is:

```text
ghcr.io/tsrigo/openclaw-reasoning:20260320
```

This default can still be overridden with `OPENCLAW_SANDBOX_IMAGE` or `--image`.

For a full reproduction guide on validating the reasoning image on a new machine, see [`docs/openclaw_reasoning_image_repro.md`](docs/openclaw_reasoning_image_repro.md).

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
- `Failed to start sandbox within 60.0s` usually means Docker is still pulling `ghcr.io/tsrigo/openclaw-reasoning:20260320`, or the local image cache is stale. Re-pull the reasoning image and retry.
- `async start sandbox failed` with `Get timed out: some object(s) not ready.` usually points to ROCK/Ray startup issues or stale ports. Re-run `source dev/rock_env.sh && source dev/.rock_ports.env`, confirm `bash dev/quickstart.sh` completed cleanly, and make sure Docker, Ray, ROCK admin/proxy, and Redis are all alive.

> For full usage details, see [`dev/README_prebuilt.md`](dev/README_prebuilt.md).

