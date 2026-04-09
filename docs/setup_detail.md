# AlphaDiana Setup Details

For detailed manual deployment, ROCK port detection, Redis/Ray/admin/proxy startup order, OpenClaw deployment, and FAQ, refer to the root `README.md`.

Quick entry:

```bash
bash scripts/quickstart.sh
```

If you need to run manually, pay attention to the following:

- Run `source scripts/rock_env.sh` from the repository root directory
- Run `unset TMPDIR` before running `python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env`
- After `newgrp docker`, you must re-run `conda activate`, `source scripts/rock_env.sh`, and `source scripts/.rock_ports.env`
- Run `ray stop` before starting Ray
- `RAY_TMPDIR` defaults to `/tmp/ray`

Further manual steps from `README.md` can be migrated here over time.

## ROCK Proxy Timeout Configuration

The ROCK proxy `post_proxy` timeout has been changed from a hardcoded 120s to reading from `ProxyServiceConfig.timeout` (default 180s). To set a larger value (600s recommended), configure it in the ROCK YAML config:

```yaml
# rock-config.yml (specified via the ROCK_CONFIG environment variable)
proxy_service:
  timeout: 600
```

If your ROCK version still has `timeout=120` hardcoded, you need to modify it manually:

1. Open `rock/sandbox/service/sandbox_proxy_service.py` in the ROCK installation directory
2. Find `timeout=120` (around line 454)
3. Change it to `timeout=self.proxy_config.timeout`

## FAQs

Common issues at a glance:

| Issue | Cause | Fix |
|---|---|---|
| `permission denied` on Docker socket | Current shell lacks `docker` group | Run `newgrp docker` or re-login |
| `deploy.py` sandbox timeout | Redis not running or `.venv` symlink broken | Check `docker ps` and `ls -la ref/ROCK/.venv` |
| Ray port `8265` already in use | Shared server port conflict | Use `find_rock_ports.py` to detect free ports |
| `rock.admin.main` Redis `ConnectionError` | Redis container not started | `docker start "$ROCK_REDIS_CONTAINER"` |
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
