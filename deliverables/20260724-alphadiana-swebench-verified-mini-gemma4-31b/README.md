# AlphaDiana SWE-Bench Verified-Mini × Gemma 4 31B

This bundle formalizes four runs:

| Run | Execution path | Config |
| --- | --- | --- |
| DirectLLM | Official standalone SWE-agent | Runbook only; no AlphaDiana experiment YAML |
| OpenClaw | AlphaDiana `swebench_container` | `configs/openclaw.yaml` |
| OpenCode | AlphaDiana `swebench_container` | `configs/opencode.yaml` |
| ZeroClaw | AlphaDiana `swebench_container` | `configs/zeroclaw.yaml` |

The shared contract is recorded in `experiment-matrix.yaml`. Follow
`RUNBOOK.md` from an AlphaDiana checkout root.

The bundle contains no credentials. Runtime URLs, the API key, the OpenClaw
gateway token, and local checkout locations are supplied through environment
variables.

## Files

- `RUNBOOK.md` — end-to-end setup, preflight, launch, monitoring, validation,
  and upload instructions.
- `experiment-matrix.yaml` — machine-readable experiment contract and parameter
  mapping.
- `configs/` — the three AlphaDiana `swebench_container` configs.
- `scripts/preflight.sh` — checks the endpoint, Docker, config validity, and
  required checkouts.
- `scripts/run.sh` — launches one of the four runs, with an optional native
  one-task smoke mode.
- `scripts/verify_outputs.py` — checks that a finished run has 50 unique tasks
  and one sample per task.
- `scripts/upload.sh` — stages and uploads one result folder to the private HF
  dataset repository without silently reusing an existing destination folder.

## Quick start

```bash
export ALPHADIANA_ROOT=/path/to/AlphaDiana
export DIRECTLLM_SWE_VERIFIED_ROOT=/path/to/swe-bench-root
export OPENAI_BASE_URL=http://127.0.0.1:8011/v1
export SWE_CONTAINER_OPENAI_BASE_URL=http://host.docker.internal:8011/v1
export OPENAI_API_KEY=local-key
export OPENCLAW_GATEWAY_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"

bash scripts/preflight.sh
bash scripts/run.sh opencode --smoke
bash scripts/run.sh opencode
```

Run the four full evaluations sequentially unless the model server has been
capacity-tested for more than four simultaneous benchmark workers.
