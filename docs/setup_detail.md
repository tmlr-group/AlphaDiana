# AlphaDiana Setup Details

For detailed manual deployment, ROCK port detection, Redis/Ray/admin/proxy startup order, OpenClaw deployment, and FAQ, refer to the root `README.md`.

Quick entry:

```bash
bash scripts/quickstart.sh
```

By default, `quickstart.sh` now creates a checkout-derived conda env such as
`alphadiana-dev-9809e32f`. That default is intentional on shared hosts: it
keeps the current checkout's editable installs from drifting onto another
worktree's conda env.

Important caveat for agent-managed shells:

- `quickstart.sh` is a bootstrap helper, not a long-lived daemon supervisor.
  If your shell runner reaps background children after the command exits,
  keep `ray start --head --block`, ROCK admin, and ROCK proxy in dedicated
  long-lived terminal sessions while the evaluation is running.
- If you start ROCK admin manually, use a runtime `ROCK_CONFIG` whose
  `ray.address` points at the active checkout-owned Ray GCS port. Otherwise
  admin can fall back to a local default Ray init and later exit with
  `GCS unavailable` even though the intended Ray head is healthy.

If you need to run manually, pay attention to the following:

- Run `source scripts/rock_env.sh` from the repository root directory
- Run `unset TMPDIR` before running `python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env`
- After `newgrp docker`, you must re-run `conda activate`, `source scripts/rock_env.sh`, and `source scripts/.rock_ports.env`
- Run `ray stop` before starting Ray
- `RAY_TMPDIR` now defaults to a short repo-isolated path such as `/tmp/<user>-ray-<hash>` to avoid both cross-checkout reuse and Ray AF_UNIX path-length failures
- `scripts/.alphadiana_env` is local ignored state. If it points at a missing
  checkout or wrong ROCK root, re-run `bash scripts/setup_alphadiana_rock.sh`
  or `bash scripts/quickstart.sh`; `source scripts/activate.sh` now ignores a
  stale marker and prefers this checkout's local `ref/ROCK` when it exists.

Further manual steps from `README.md` can be migrated here over time.

## ROCK Isolation On Shared Hosts

If you have multiple AlphaDiana / ROCK checkouts on the same machine, isolate all of the following together:

- Use a dedicated conda env per checkout.
  Plain `bash scripts/quickstart.sh` now does this automatically by deriving a
  checkout-specific env name. If you need to pin it explicitly, pass
  `bash scripts/quickstart.sh <env_name>`.
- Persist that env choice via `scripts/.alphadiana_env` and enter it with:
  `source scripts/activate.sh`
- Allocate dedicated admin/proxy/Ray/Redis ports for the checkout:
  `python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env`
- Let the generated file carry a repo-specific
  `ALPHADIANA_ROCK_INSTANCE_NAME`,
  `ROCK_REDIS_CONTAINER`,
  and short `RAY_TMPDIR`, so Redis and Ray state do not collide across worktrees.
- Use `python -m alphadiana.cli env` before launching runs.
  It now checks both service health and whether the configured admin/proxy ports belong to this checkout. If a foreign checkout owns them, regenerate ports and restart the local services.

Current helper behavior:

- `scripts/activate.sh` now ignores stale `scripts/.alphadiana_env` markers
  whose recorded project root does not match the current checkout.
- `scripts/start_openclaw.sh` and `scripts/start_zeroclaw.sh` can reuse `ALPHADIANA_ROCK_ROOT` instead of requiring a local `ref/ROCK`.
- `scripts/start_openclaw.sh` and `scripts/start_zeroclaw.sh` now default their
  conda env fallback to the same checkout-derived env name that
  `quickstart.sh` writes.
- Both start scripts refresh ports when the configured admin/proxy belong to another checkout.
- `scripts/start_zeroclaw.sh` now restarts an unhealthy local Ray head instead of blindly reusing any listener on the GCS port.
- If `ray start --head` fails with `Session name ... does not match persisted value ... Perhaps there was an error connecting to Redis`, the checkout-isolated Redis still has stale Ray session metadata. Recreate that checkout's `ROCK_REDIS_CONTAINER` and clear that checkout's `RAY_TMPDIR` before retrying the Ray startup.

## ROCK Proxy Timeout Configuration

The local `ref/ROCK` proxy `http_proxy` path no longer hardcodes a `120s` read timeout for proxied model requests. It now uses a connect-only timeout derived from `ProxyServiceConfig.timeout`, so long ZeroClaw / OpenClaw upstream calls are not cut off just because the model takes more than two minutes to answer.

To raise the connect timeout itself, configure `proxy_service.timeout` in the ROCK YAML config:

```yaml
# rock-config.yml (specified via the ROCK_CONFIG environment variable)
proxy_service:
  timeout: 600
```

If you are using an older external ROCK checkout that still hardcodes `timeout=120`, patch `rock/sandbox/service/sandbox_proxy_service.py` the same way:

1. Open `rock/sandbox/service/sandbox_proxy_service.py` in the ROCK installation directory
2. Find the `http_proxy()` request builder with `timeout=120`
3. Remove the hardcoded per-request timeout and use a timeout object with `read=None` plus a bounded connect timeout

## FAQs

Common issues at a glance:

| Issue | Cause | Fix |
|---|---|---|
| `permission denied` on Docker socket | Current shell lacks `docker` group | Run `newgrp docker` or re-login |
| `deploy.py` sandbox timeout | Redis not running or `.venv` symlink broken | Check `docker ps` and `ls -la ref/ROCK/.venv` |
| Ray port `8265` already in use | Shared server port conflict | Use `find_rock_ports.py` to detect free ports |
| `rock.admin.main` Redis `ConnectionError` | Redis container not started | `docker start "$ROCK_REDIS_CONTAINER"` |
| `alphadiana env` shows healthy proxy but wrong checkout | Another worktree owns the configured admin/proxy ports | Regenerate `scripts/.rock_ports.env` and restart from `source scripts/activate.sh` |
| `alphadiana run` fails with `ROCK proxy failed` after about 120s | Old ROCK proxy path still has a hardcoded stream timeout, or the checkout is sharing stale ports/env with another instance | Use a dedicated env, dedicated ports, and ensure the current `ref/ROCK` patch is active |
| `alphadiana env` loses `admin` while `6380` is still open | A stale Ray head is still listening on the GCS port but is not actually healthy | Re-run `bash scripts/start_zeroclaw.sh` or `bash scripts/start_openclaw.sh` so the unhealthy Ray head is restarted |
| `ray start --head` aborts with `Session name ... does not match persisted value ...` | The checkout's isolated Redis still contains stale Ray session metadata | Recreate `"$ROCK_REDIS_CONTAINER"` and clear `"$RAY_TMPDIR"` before retrying Ray startup |
| Sandbox container exits immediately | `ref/ROCK/.venv` missing or invalid | `ln -sfn "$(python -c 'import sys; print(sys.prefix)')" ref/ROCK/.venv` |


## Port Cleanup

On shared servers, you may need to clean up previously allocated ports:

```bash
bash scripts/cleanup_rock_ports.sh
```

This only terminates processes owned by the current user on ROCK-related ports.

## CLI Reference

```bash
alphadiana run <config.yaml>                # Run evaluation
alphadiana validate <config.yaml>           # Validate config without running
alphadiana report <results_dir>             # Generate report from result files
alphadiana batch <c1.yaml> <c2.yaml> ...    # Run multiple configs (supports --parallel)
alphadiana list-benchmarks                  # List registered benchmarks
```

Override config values from CLI:

```bash
alphadiana run config.yaml \
  --override agent.config.temperature=0.5 \
  --override max_concurrent=4
```

Re-run failed tasks:

```bash
alphadiana run config.yaml --redo-all
```

## Configuration

Experiments are defined by a single YAML file. See [`configs/schema.yaml`](../configs/schema.yaml) for the full schema.

```yaml
run_id: "openclaw-qwen3-8b-aime2024-001"   # auto-generated if omitted

agent:
  name: openclaw                  # openclaw | direct_llm
  version: "2026.3.7"
  config:
    # Supports environment variables: ${SANDBOX_ID}, ${ROCK_PORT}
    api_base: "http://127.0.0.1:9001/apis/envs/sandbox/v1/sandboxes/${SANDBOX_ID}/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    max_tokens: 65536             # recommend 65536+ for thinking models
    max_attempts: 5               # retry attempts (openclaw only)
    request_timeout: 1800         # seconds (openclaw only)

benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

sandbox: null                     # null | rock | local | boxlite

scorer:
  name: math_verify
  config:
    tolerance: 1e-6

max_concurrent: 1                 # parallel task count
num_samples: 32                   # samples per task (for pass@k)
output_dir: "./results"
metadata:                         # free-form tags (optional)
  author: "team-xyz"
```

Example configs: [`configs/examples/`](configs/examples/)


### API Key Handling in Dashboard

The Dashboard API Key input supports two modes:

1. **Direct paste**: Paste the API key directly
2. **Environment variable reference**: Enter `$VAR_NAME` (e.g., `$OPENROUTER_API_KEY`) to read from `.env` or system environment

The Dashboard auto-matches API key variables based on the API base URL domain:

| API Base URL | Auto-matched Variable |
|---|---|
| `https://openrouter.ai/api/v1/` | `$OPENROUTER_API_KEY` |
| `https://api.openai.com/v1/` | `$OPENAI_API_KEY` |
| `https://api.siliconflow.cn/v1/` | `$SILICONFLOW_API_KEY` |
| `https://ark.cn-beijing.volces.com/api/...` | `$ARK_API_KEY` |
| `https://api.deepseek.com/v1/` | `$DEEPSEEK_API_KEY` |
