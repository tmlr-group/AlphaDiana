---
sidebar_position: 4
---

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
[installation guide](./installation). Common causes of a container that exits
immediately:

- `ROCK_PROJECT_ROOT` is not set (Ray workers need it; restart Ray after
  setting it).
- The `.venv` symlink does not point at a valid Python environment.
- The `gem-llm` dependency is not installed.

If the pre-flight reports a port-ownership mismatch, resolve the actual ports
and write them to the environment file the CLI reads:

```bash
python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env
```

See [../harnesses/openclaw](../harnesses/openclaw) for the OpenClaw reliability
contract and [../harnesses/zeroclaw](../harnesses/zeroclaw) for the
sandbox-only ZeroClaw path.

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
2. Delete the `None`/empty records for the affected tasks (or use a fresh
   `run_id`), then re-run the same config. Checkpointing fills only the missing
   samples.

Use `--redo-all` only when you intend to discard the checkpoint and recompute
every sample:

```bash
alphadiana run config.yaml --redo-all   # bypass checkpoint; recompute everything
```

For runs with `num_samples > 1`, checkpointing is per-sample: sample 0 writes
`{task_id}.jsonl` and later samples write `{task_id}.sample_{N}.jsonl`, so only
the failed samples are recomputed on resume.

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

The `configs/examples/` configs intentionally pin one task via `dataset_index`
or `max_tasks` for smoke testing. Do not use them for full benchmark runs;
the full entry points live under `configs/full_runs/`.

## Quick checklist

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Dataset load `RuntimeError` | `huggingface.co` unreachable | `export HF_ENDPOINT=https://hf-mirror.com` |
| Dataset load fails on a gated set | No access token | `export HF_TOKEN=...` and request access |
| Agent requests fail oddly | Proxy vars leaking into sandbox | `source scripts/rock_env.sh` |
| `api_key` rejected at validation | Literal `EMPTY` value | use `sk-EMPTY` |
| Pre-flight fails for OpenClaw/ROCK | ROCK services down or wrong ports | `alphadiana env`, then start/repair ROCK |
| Records have `predicted: null` | vLLM transient crash | check `/v1/models`, drop bad records, re-run |
| Run completes with 0 tasks | `max_tasks: 0` | omit `max_tasks` or use a positive count |
