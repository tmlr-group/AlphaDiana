# Troubleshooting

This page collects the most common failure modes when running AlphaDiana and the
exact fix for each. Most issues come from environment configuration (Hugging
Face dataset access, proxy variables, provider credentials) or from a backing
service (ROCK, vLLM) not being reachable.

A quick mental model: a run is defined by one YAML, parsed into
`ExperimentConfig` (`alphadiana/engine/config/experiment_config.py`), validated
by `ConfigValidator` (`alphadiana/engine/config/validator.py`), and executed by
the runner (`alphadiana/engine/runner.py`). Results are written through the
result store (`alphadiana/analysis/io/result_store.py`). When something breaks,
the symptom usually points to one of those layers.

If a run fails to even start, run `alphadiana validate <config.yaml>` first. It
prints `Config is valid.` on success, or one `  - <error>` line per problem and
exits non-zero.

## Hugging Face dataset loading

### Dataset fails to download

Benchmarks that pull from the Hugging Face Hub (AIME, HLE, GPQA, MMMU-Pro, and
others) raise a `RuntimeError` when the dataset cannot be fetched. The AIME
loader, for example, wraps the failure with an actionable message that echoes
the current mirror setting:

```
Failed to load AIME dataset from Hugging Face. If direct access is
unavailable, source `scripts/rock_env.sh` first or set
HF_ENDPOINT=https://hf-mirror.com and retry. Current HF_ENDPOINT=<unset>.
Original error: ...
```

On hosts where `huggingface.co` is unreachable, point the client at a mirror
before running:

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

`scripts/rock_env.sh` sets this for you. Sourcing it is the recommended fix
when direct Hub access times out.

### Gated datasets need a token

Some datasets are gated and require an access token. HLE
(`cais/hle`) is the canonical example: request access at
`https://huggingface.co/datasets/cais/hle`, then export your token before the
run.

```bash
export HF_TOKEN=your_token
```

Without `HF_TOKEN`, a gated dataset fails to load even though the mirror and
network are fine.

### Caching across runs

Repeated runs re-use the local Hub cache. If you want the cache on a specific
disk (or to share it between checkouts), set the standard Hugging Face cache
variables before running:

| Variable | Purpose |
| --- | --- |
| `HF_ENDPOINT` | Hub mirror (e.g. `https://hf-mirror.com`) |
| `HF_TOKEN` | Access token for gated datasets |
| `HF_HOME` | Root of the Hugging Face cache |
| `HF_DATASETS_CACHE` | Dataset-specific cache directory |
| `HUGGINGFACE_HUB_CACHE` | Hub download cache directory |

## Proxy environment variables

Proxy variables that are fine for normal shell use can break agent traffic,
because they leak into ROCK sandboxes and the provider connection. The `run`
command warns when any of `ALL_PROXY`, `HTTP_PROXY`, `HTTPS_PROXY`,
`all_proxy`, `http_proxy`, `https_proxy` are set.

The fix is to clear them in the current shell. `scripts/rock_env.sh` does this
explicitly:

```bash
source scripts/rock_env.sh   # unsets ALL_PROXY/HTTP_PROXY/HTTPS_PROXY (+ lowercase)
```

If you still need an outbound proxy for the dataset download but not for the
sandbox, fetch the dataset first, then clear the proxy variables before
launching the agent run.

## The literal `EMPTY` api_key pitfall

For a local vLLM endpoint the API key is irrelevant, but the validator treats
the literal string `"EMPTY"` as a *missing* value. `ConfigValidator`
considers a field unpopulated when it is `None`, an empty string, the literal
`EMPTY` (case-insensitive, after stripping), or a string that is wholly an
unresolved `${VAR}` placeholder.

So this fails validation:

```yaml
agent:
  config:
    api_key: "EMPTY"   # rejected: treated as missing
```

Use any other non-empty string instead:

```yaml
agent:
  config:
    api_key: "sk-EMPTY"   # passes; value is ignored by vLLM
```

The same rule applies on the command line and via environment defaults: prefer
`sk-EMPTY` over `EMPTY` for `OPENAI_API_KEY`.

### Where credentials come from

Many example configs leave `model`, `api_base`, and `api_key` blank and rely on
environment defaults. During `ExperimentConfig.from_yaml`, blank agent fields
are filled from the environment for the standard agents (`direct_llm`,
`zeroclaw`, `opencode`, and the `terminal_bench2_*` variants):

| Config field | Environment variable |
| --- | --- |
| `api_base` | `OPENAI_BASE_URL` |
| `api_key` | `OPENAI_API_KEY` |
| `model` / `model_name` | `OPENAI_MODEL_NAME` |

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=<model>
```

Note that defaults only apply when the YAML field is blank; a value present in
YAML wins. Also, env interpolation is two-phase: every string is run through
`os.path.expandvars` (so `${SANDBOX_ID}`, `${OPENAI_BASE_URL}`, etc. resolve
from the shell), and a string that is *entirely* an unresolved `${VAR}` is
blanked rather than left as a literal placeholder. A missing variable therefore
degrades to an empty field, which the validator will flag, rather than leaking
`${VAR}` into a request.

### OpenClaw sends a provider URL a gateway token

OpenClaw has two distinct endpoints. Uppercase `OPENAI_BASE_URL` (or lowercase
`openai_base_url`) in `agent.config` is the upstream model provider used while
starting a gateway. Lowercase `agent.config.api_base` is the URL of an
already-running OpenClaw gateway and is called with `gateway_token`.

The current config loader also copies the shell's `OPENAI_BASE_URL` into a blank
OpenClaw `api_base`. That can make an auto-deploy config skip gateway startup
and send its gateway credential directly to the provider. For an OpenClaw
ROCK/Podman auto-deploy run, use a separate shell variable:

```bash
export PROVIDER_BASE_URL=https://provider.example/v1
unset OPENAI_BASE_URL

python -m alphadiana.cli run <openclaw-config.yaml> \
  -o agent.config.OPENAI_BASE_URL="$PROVIDER_BASE_URL"
```

If you intentionally use a predeployed gateway, set `agent.config.api_base` to
that gateway and set the matching `gateway_token`.

## ROCK service not up

The `openclaw` harness and any run with `sandbox.name: rock` need the ROCK
admin, proxy, and Redis services running. Before such a run, the CLI performs a
pre-flight that checks reachability *and* port ownership of the admin, proxy,
and Redis ports. Non-ROCK runs (`direct_llm`, and the podman/opencode
controllers) skip this check entirely.

A healthy pre-flight prints:

```
Pre-flight: checking ROCK services (admin=..., proxy=..., redis=...)...
Pre-flight passed: admin ✓  proxy ✓  redis ✓
```

If it fails, the services are not up or the ports are owned by another process.
Check service health directly:

```bash
alphadiana env   # prints Admin/Proxy/Redis/Ray endpoints and health
```

To bring the stack up, follow the ROCK setup steps in the
[installation guide](./installation.md). Common causes of a container that exits
immediately:

- `ROCK_PROJECT_ROOT` is not set (Ray workers need it; restart Ray after
  setting it).
- The `.venv` symlink does not point at a valid Python environment. Repoint it
  at the active interpreter:

  ```bash
  ln -sfn "$(python -c 'import sys; print(sys.prefix)')" ref/ROCK/.venv
  ```

- The `gem-llm` dependency is not installed.

If the pre-flight reports a port-ownership mismatch, resolve the actual ports
and write them to the environment file the CLI reads:

```bash
python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env
```

See [../harnesses/openclaw](../harnesses/openclaw.md) for the OpenClaw reliability
contract and [../harnesses/zeroclaw](../harnesses/zeroclaw.md) for the
sandbox-only ZeroClaw path.

### Docker socket `permission denied`

Sandbox and ROCK containers talk to the Docker daemon over its socket. If the
current shell is not in the `docker` group, container operations fail with
`permission denied`. Pick up the group membership without logging out:

```bash
newgrp docker   # or re-login
```

After `newgrp docker` the shell is fresh, so re-run `conda activate`,
`source scripts/rock_env.sh`, and `source scripts/.rock_ports.env`.

### Stale Ray session metadata in the checkout Redis

If `ray start --head` aborts with `Session name ... does not match persisted
value ... Perhaps there was an error connecting to Redis`, this checkout's
isolated Redis still holds stale Ray session metadata. Recreate that checkout's
`ROCK_REDIS_CONTAINER` and clear its `RAY_TMPDIR` before retrying the Ray
startup. The start scripts (`scripts/start_zeroclaw.sh`,
`scripts/start_openclaw.sh`) also restart an unhealthy local Ray head instead of
reusing any listener on the GCS port, so re-running them is the simplest fix.

### `alphadiana env` loses `admin` while the GCS port is still open

A stale Ray head can keep listening on the GCS port (e.g. `6380`) while being
unhealthy, so `alphadiana env` reports the `admin` endpoint as down. Restart the
Ray head by re-running `bash scripts/start_zeroclaw.sh` or
`bash scripts/start_openclaw.sh`.

### `start_async` returns `404`

ROCK admin and proxy bind to different ports, and `/start_async` is served by
the admin. A `404` usually means the request hit the proxy port instead. The
admin and proxy ports default to `9000` and `9001`
(`alphadiana/utils/rock_ports.py`, overridable via `ROCK_ADMIN_PORT` /
`ROCK_PROXY_PORT`). Disambiguate which process owns which port:

```bash
ss -ltnp | grep ':9000\|:9001'   # or your configured ROCK_ADMIN_PORT / ROCK_PROXY_PORT
```

- admin port (default `9000`) -> `rock.admin.main --role admin`
- proxy port (default `9001`) -> `rock.admin.main --role proxy`

### `alphadiana run` fails with `ROCK proxy failed` after about 120s

Either an old ROCK proxy path still hardcodes a `120s` stream read timeout, or
this checkout is sharing stale ports/env with another instance. Fix it with a
dedicated env, dedicated ports, and the current `ref/ROCK` patch active (the
patched proxy uses a connect-only timeout from `ProxyServiceConfig.timeout`, so
long model calls are no longer cut off at two minutes).

### `OPENAI_BASE_URL` keeps the old provider silently

`source scripts/activate.sh` loads `.env` into the current shell. A wrapper that
sets defaults with shell-default syntax such as
`OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://localhost:8011/v1}"` lets any
existing `.env` or parent-shell `OPENAI_*` value win, so the wrapper silently
keeps the old provider. For local-model runs, either export `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, and `OPENAI_MODEL_NAME` explicitly after activation, or unset
them first before applying local defaults. When in doubt, verify the effective
provider from the final `alphadiana.cli run ...` argv or `/proc/<pid>/environ`.

### Long-lived services and stale env files

`scripts/quickstart.sh` is a bootstrap helper, not a daemon supervisor. If your
shell runner reaps background children after a command exits, keep
`ray start --head --block`, ROCK admin, and ROCK proxy in dedicated long-lived
terminal sessions for the duration of the run. If you start ROCK admin manually,
use a runtime `ROCK_CONFIG` whose `ray.address` points at this checkout's active
Ray GCS port; otherwise admin falls back to a local default Ray init and later
exits with `GCS unavailable` even though the intended Ray head is healthy.

`scripts/.alphadiana_env` is local, git-ignored state. If it points at a missing
checkout or the wrong ROCK root, regenerate it:

```bash
bash scripts/setup_alphadiana_rock.sh   # or: bash scripts/quickstart.sh
```

## vLLM transient crashes producing `None` records

A vLLM endpoint that hiccups mid-run can produce task records with a `null`
predicted answer and an empty trajectory. This is a transient infrastructure
failure, not a model failure, and the fix is to re-score the affected samples,
not to restart the model.

`alphadiana run` checkpoints automatically: re-invoking the same config loads
the existing `<run_id>.jsonl` (and per-sample files under
`results/<run_id>/`), skips completed task or sample ids, and resumes the rest.
So the normal recovery is:

1. Confirm vLLM is actually alive: `curl http://<host>:<port>/v1/models`. If
   that fails, the endpoint is down and must be restarted. If it returns the
   model list, the model is fine and only the records are bad.
2. Re-run the same config and run id. A provider/runtime error or other invalid
   record is not checkpoint-complete, so the runner evaluates that sample again
   and the latest JSONL record wins during load. You do not need to edit the
   result file.

Timeout-classified outcomes are different: current harnesses record them as
`score: 0`, `correct: false`, `finish_reason: timeout`, and
`score_status: valid_scored`, so they are checkpoint-complete. Use `--redo-all`
or a new run id when you intentionally want to rerun those samples.

Use `--redo-all` only when you intend to discard the checkpoint and recompute
every sample:

```bash
alphadiana run config.yaml --redo-all   # bypass checkpoint; recompute everything
```

For runs with `num_samples > 1`, checkpointing is per `(task_id, sample_index)`.
The flat `<run_id>.jsonl` stores every attempt, while
`results/<run_id>/tasks/<task_id>.json` is a JSON sample list. Lifecycle event
files use `{task_id}.jsonl` for sample 0 and
`{task_id}.sample_{N}.jsonl` for later samples.

## `max_tasks == 0` returns an empty run

The benchmark loaders treat `max_tasks: 0` as "load zero tasks" and return an
empty list, so the run completes with nothing scored. This is usually an
accident from over-aggressive slicing.

```yaml
benchmark:
  name: aime
  config:
    max_tasks: 0   # returns [] -> empty run
```

Fixes:

- To run the whole split, omit `max_tasks` entirely (do not set it to `0`).
- To run a fixed prefix, use a positive count (`max_tasks: 5`).
- To run a single specific task, use `dataset_index` instead. Do not combine a
  sliced split such as `train[16:17]` with a config that already sets
  `max_tasks`, and do not set both `dataset_index` and `max_tasks` at once.

`configs/examples/` is mixed: some files pin a small smoke, while others load a
full selected split. Inspect the selectors before launch and add a bounded
override for a smoke. `configs/full_runs/swe_verified_mini.yaml` is a rollout
campaign manifest, not a generic `alphadiana run` config.

## Quick checklist

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Dataset load `RuntimeError` | `huggingface.co` unreachable | `export HF_ENDPOINT=https://hf-mirror.com` |
| Dataset load fails on a gated set | No access token | `export HF_TOKEN=...` and request access |
| Agent requests fail oddly | Proxy vars leaking into sandbox | `source scripts/rock_env.sh` |
| `api_key` rejected at validation | Literal `EMPTY` value | use `sk-EMPTY` |
| OpenClaw provider returns gateway-auth `401` | Provider URL was loaded into gateway `api_base` | unset shell `OPENAI_BASE_URL`; override `agent.config.OPENAI_BASE_URL` from a differently named variable |
| Quickstart rejects an empty gateway token | Token generation happens after the current preflight | export a strong random `OPENCLAW_GATEWAY_TOKEN` before quickstart |
| Pre-flight fails for OpenClaw/ROCK | ROCK services down or wrong ports | `alphadiana env`, then start/repair ROCK |
| Docker socket `permission denied` | Shell lacks `docker` group | `newgrp docker` (then re-activate env) |
| Sandbox container exits immediately | `ref/ROCK/.venv` missing/invalid | `ln -sfn "$(python -c 'import sys; print(sys.prefix)')" ref/ROCK/.venv` |
| `ray start --head` aborts with `Session name ... does not match persisted value` | Stale Ray metadata in checkout Redis | recreate `ROCK_REDIS_CONTAINER`, clear `RAY_TMPDIR` |
| `start_async` returns `404` | Hit proxy port instead of admin | `ss -ltnp \| grep ':9000\|:9001'` (admin 9000, proxy 9001 by default) |
| `ROCK proxy failed` after ~120s | Old proxy stream timeout or shared stale ports/env | dedicated env + ports, current `ref/ROCK` patch active |
| Wrong provider despite exported vars | `OPENAI_BASE_URL:-` default kept stale `.env` value | export `OPENAI_*` explicitly or unset first; check argv |
| Stale `scripts/.alphadiana_env` | Points at missing checkout/wrong ROCK root | `bash scripts/setup_alphadiana_rock.sh` |
| Records have `predicted: null` | Provider/runtime failure | check `/v1/models`, then rerun the same run id |
| Run completes with 0 tasks | `max_tasks: 0` | omit `max_tasks` or use a positive count |
