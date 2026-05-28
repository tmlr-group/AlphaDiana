# Deploying OpenClaw with the Prebuilt Sandbox Image

`dev/start_openclaw.sh` is a one-command script that starts Ray, ROCK admin/proxy, and deploys a live OpenClaw sandbox using the default reasoning-enabled Docker image `tmlrgroup/alphadiana:v1`.

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Redis container | A Redis container must be running on the configured port. Start it once with:<br>`docker run -d --name ${ROCK_REDIS_CONTAINER:-redis-stack} -p ${ROCK_REDIS_PORT:-6379}:6379 redis/redis-stack-server:latest` |
| Docker access | The current user must be able to run `docker run` |
| ROCK checkout | `ref/ROCK/.venv` must symlink to the active conda environment |
| Model env vars | `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL_NAME` must be set |

---

## Quick Start

```bash
export OPENAI_BASE_URL=https://your-openai-compatible-api/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=deepseek-v3

bash dev/start_openclaw.sh
```

On success the script prints:

```
OpenClaw deployed successfully!
Sandbox ID: <id>
API base: http://127.0.0.1:9051/apis/envs/sandbox/v1/sandboxes/<id>/proxy/v1
Auto-clear: 7200s (120 minutes)
```

### Verify the gateway is alive

```bash
curl -s -X POST \
  "http://127.0.0.1:9051/apis/envs/sandbox/v1/sandboxes/<id>/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"hi"}],"max_tokens":32}'
```

---

## What the Script Does (Step by Step)

| Step | Action |
|------|--------|
| 1 | Sources `scripts/rock_env.sh` — loads all port/path configuration |
| 2 | Checks Redis is reachable on the port configured in the ROCK YAML |
| 3 | Kills any stale ROCK processes on ports 9050/9051; stops leftover sandbox containers |
| 4 | Starts a Ray head node on `ROCK_RAY_PORT` (default 6385), or reuses one already listening |
| 5 | Generates a runtime ROCK config with the explicit Ray GCS address; starts ROCK admin and proxy |
| 6 | Runs `python -m openclaw_deploy.deploy` with the default reasoning image |

The script is **idempotent** — safe to run multiple times.

---

## Port Configuration

All ports are read from `scripts/.rock_ports.env`:

| Variable | Default | Service |
|----------|---------|---------|
| `ROCK_ADMIN_PORT` | 9050 | ROCK admin (sandbox lifecycle API) |
| `ROCK_PROXY_PORT` | 9051 | ROCK proxy (gateway access) |
| `ROCK_RAY_PORT` | 6385 | Ray GCS head node |
| `ROCK_REDIS_PORT` | 6379 | Redis (used by ROCK, not the shared redis-stack) |

If another user's process is already occupying one of these ports, update `scripts/.rock_ports.env` and rerun the script.

---

## Performance Notes

**First run on a fresh Ray session (~2–3 minutes)**
Ray installs the sandbox actor runtime env (pip packages from `requirements_sandbox_actor.txt`) before the first sandbox actor can start. This is a one-time cost per Ray session — subsequent runs on the same session are instant.

**Sandbox agent install (~10 seconds with prebuilt image)**
The reasoning image ships OpenClaw pre-installed. The `custom_install_cmd` detects the existing installation and skips `npm install`, so the agent setup step takes only a few seconds.

If you use the default (non-prebuilt) image, `npm install` runs from scratch and takes **~7 minutes**.

---

## Troubleshooting

### Ray is in a broken state
Symptom: `Get timed out: some object(s) not ready` even after waiting.

```bash
pkill -u "$(whoami)" -f "gcs_server.*6385"
rm -rf /tmp/$(whoami)-ray/rock
bash dev/start_openclaw.sh
```

### ROCK admin fails to connect to Ray
Symptom: `Can't find a node_ip_address.json` in the admin log.

This usually means `address: "auto"` picked another user's Ray cluster. The script generates a runtime config with the explicit GCS address to prevent this. If it still happens, check `ROCK_RAY_PORT` and make sure no other user's Ray is listening on that port:

```bash
lsof -i :6385
```

### Runtime Env Agent crashes on startup
Symptom: `Raylet could not connect to Runtime Env Agent`.

Cause: the VPN client (`clash-lin`) may have occupied the ephemeral port that the Runtime Env Agent tried to bind to. The script avoids this by starting Ray with a dedicated session dir and limiting worker ports to 20000–29999. If it still occurs, restart Ray:

```bash
pkill -u "$(whoami)" -f "gcs_server.*6385"
rm -rf /tmp/$(whoami)-ray/rock
bash dev/start_openclaw.sh
```

### Container process terminated
Symptom: sandbox starts but `rocklet` exits immediately.

Cause: `rocklet` uses a conda shebang pointing to the Python interpreter from the active conda environment (e.g. `$(conda info --base)/envs/alphadiana/bin/python3.11`) that must be resolvable inside the container. The ROCK runtime env is patched (`ref/ROCK/rock/deployments/runtime_env.py`) to mount the conda env at its original host path. If the path changes, update that patch.

---

## Log Files

```
.cache/logs/rock-admin.log    # ROCK admin — Ray actor errors, sandbox lifecycle
.cache/logs/rock-proxy.log    # ROCK proxy — gateway routing errors
.cache/logs/ray-start.log     # Ray startup output
/tmp/$(whoami)-ray/rock/session_latest/logs/runtime_env_agent.log
```
