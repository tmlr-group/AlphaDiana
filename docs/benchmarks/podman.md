# Podman Experiment Runbook

This runbook covers the opt-in Podman experiment paths in the current checkout.
Podman is not a global default. Use the commands here when you want
container-backed OpenClaw, ZeroClaw, or OpenCode standard-reasoning experiments
without ROCK admin/proxy, Redis, Ray, Docker daemon, or Docker socket as the
main runtime path.

Current support boundary:

- Standard reasoning pilot path: validated for OpenClaw, ZeroClaw, and OpenCode
  across AIME, GPQA-Diamond, HLE, and IMO-AnswerBench with three tasks per
  cell. Evidence is in
  `context/podman-scale-readiness/README.md`.
- Task-container path: TerminalBench2 and SWE-bench Verified have focused
  opt-in Podman smoke evidence. SWE-bench Pro and external_benchmark remain deferred.
- Full-scale standard-reasoning Podman runs are recommended only after the
  pilot audit passes. No checked-in full-scale Podman matrix is promoted as a
  default.

For coding-agent handoff and a development file map, read
`context/add-podman-handoff/README.md`.

## Development File Map

- Runtime and agent code:
  `alphadiana/container_runtime/agent_runtime.py`,
  `alphadiana/agent/openclaw.py`, `alphadiana/agent/zeroclaw.py`,
  `alphadiana/agent/opencode.py`, `alphadiana/runner/runner.py`,
  `alphadiana/results/status.py`, `alphadiana/results/report.py`, and
  `alphadiana/utils/math_answer.py`.
- Configs and operators:
  `configs/smokes/podman_scale_readiness/`,
  `configs/smokes/podman_nightly_validation/`,
  `scripts/run_podman_scale_readiness.sh`,
  `scripts/audit_podman_scale_readiness.py`, and
  `scripts/run_podman_nightly_validation.sh`.
- Evidence and handoff:
  `context/add-podman-handoff/README.md`,
  `context/podman-scale-readiness/README.md`,
  `context/podman-nightly-validation/README.md`,
  `context/phase02-podman-agent-smokes/README.md`, and
  `context/phase03-podman-task-containers/README.md`.
- Focused tests:
  `tests/test_podman_agent_runtime.py`,
  `tests/test_podman_openclaw_runtime.py`,
  `tests/test_podman_zeroclaw_runtime.py`,
  `tests/test_podman_opencode_controller.py`,
  `tests/test_podman_scale_readiness_configs.py`,
  `tests/test_podman_scale_readiness_audit.py`, and
  `tests/test_standard_podman_error_metadata.py`.

## Prerequisites

From the repository root:

```bash
source scripts/activate.sh

podman --version
podman info >/dev/null
```

Set the provider variables explicitly after `source scripts/activate.sh` so a
local `.env` does not point the run at the wrong backend:

```bash
export OPENAI_BASE_URL=http://localhost:8011/v1
export OPENAI_API_KEY=EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-4B
```

For HLE rows, provide operator-owned Hugging Face credentials/cache if the
dataset is gated or the shared cache is read-only:

```bash
export HF_TOKEN=<token>
export HF_HOME=<writable-cache>
export HF_DATASETS_CACHE=<writable-cache>
```

Build the local Podman images used by the standard-reasoning matrix:

```bash
podman build -f openclaw_deploy/Dockerfile \
  -t localhost/alphadiana-openclaw:latest .
podman tag localhost/alphadiana-openclaw:latest alphadiana-openclaw:latest

podman build -f zeroclaw_deploy/Dockerfile \
  -t localhost/zeroclaw-reasoning:0.6.9 .

podman build -f opencode_deploy/Containerfile.podman-controller \
  -t localhost/alphadiana-opencode-podman:latest .
podman tag localhost/alphadiana-opencode-podman:latest \
  alphadiana-opencode-podman:latest
```

For SWE-bench Verified paths that still use a Docker-compatible client
boundary, start the user Podman socket and point compatibility clients at it:

```bash
systemctl --user start podman.socket
export ALPHADIANA_PODMAN_SOCKET="${XDG_RUNTIME_DIR}/podman/podman.sock"
export DOCKER_HOST="unix://${ALPHADIANA_PODMAN_SOCKET}"
```

## Standard Reasoning Pilot

Use the Phase 5 script for the audited 12-cell pilot:

```bash
export PODMAN_SCALE_RUN_PREFIX=podman_scale_$(date +%Y%m%d_%H%M%S)
export PODMAN_SCALE_COMMAND_TIMEOUT_SECONDS=7200

bash scripts/run_podman_scale_readiness.sh validate
bash scripts/run_podman_scale_readiness.sh pilot
bash scripts/run_podman_scale_readiness.sh audit
```

The script runs:

- OpenClaw, ZeroClaw, and OpenCode.
- AIME, GPQA-Diamond, HLE, and IMO-AnswerBench.
- Three tasks per agent x benchmark cell.

This host's validated local-vLLM path uses Podman host networking. The script
preflights `/v1/models` from a small Podman container before launching the
pilot. If you are not using a loopback provider, keep the same script flow but
set `OPENAI_BASE_URL` to a container-reachable endpoint.

Primary outputs:

- Raw logs: `logs/<run_id>.log`
- Results: `results/<run_id>/.../tasks/*.json`
- Status TSV: `context/podman-scale-readiness/run-status-<prefix>.tsv`
- Audit JSON/table:
  `context/podman-scale-readiness/audit-<prefix>.json` and
  `context/podman-scale-readiness/audit-<prefix>.md`

Inspect task JSON files as sample lists:

```bash
python - <<'PY'
import json
from pathlib import Path

for path in sorted(Path("results").glob("podman_scale_*/*/tasks/*.json")):
    data = json.loads(path.read_text())
    row = data[0]
    print(path, row.get("score_status"), row.get("score"),
          row.get("metadata", {}).get("container_engine"))
PY
```

Each Podman-backed row should preserve `metadata.container_engine=podman`.

## Nightly Validation Matrix

Use this when you want broader validation-only coverage, including the current
TerminalBench2 and SWE-bench Verified opt-in cells:

```bash
bash scripts/run_podman_nightly_validation.sh validate
bash scripts/run_podman_nightly_validation.sh standard
bash scripts/run_podman_nightly_validation.sh task
```

`all` runs both standard and task scopes:

```bash
bash scripts/run_podman_nightly_validation.sh all
```

This matrix is evidence-gathering only. It does not promote Podman defaults.
Read `context/podman-nightly-validation/README.md` before describing support
status from those rows.

## Focused Task-Container Smokes

TerminalBench2 OpenCode Podman smoke:

```bash
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench-2

python -m alphadiana.cli run \
  configs/examples/terminal_bench2_opencode_podman_smoke.yaml \
  --redo-all \
  -o run_id=podman_tb2_opencode_smoke_$(date +%Y%m%d_%H%M%S) \
  -o output_dir=./results/podman_tb2_opencode_smoke
```

SWE-bench Verified OpenClaw Podman smoke:

```bash
python -m alphadiana.cli run \
  configs/examples/openclaw_swe_bench_podman_smoke.yaml \
  --redo-all \
  -o run_id=podman_swe_verified_openclaw_smoke_$(date +%Y%m%d_%H%M%S) \
  -o output_dir=./results/podman_swe_verified_openclaw_smoke
```

For task-container results, inspect both the task row and benchmark artifacts.
For SWE-bench Verified, Podman provenance may appear in
`artifacts/<task>/sandbox/sandbox_meta.json` in addition to the top-level task
metadata.

## Scaling Past The Pilot

After a passing pilot audit, create a new config directory from
`configs/smokes/podman_scale_readiness/`, increase the benchmark task selectors
or `max_tasks`, and use unique run IDs. Keep the same operator contract:

- run `python -m alphadiana.cli validate <config>` first;
- write raw shell logs under `logs/<run_id>.log`;
- use `--redo-all` only when you intend to replace existing task rows;
- inspect `results/<run_id>/.../tasks/*.json` via `data[0]`;
- preserve the status TSV and audit output under `context/` if the run changes
  support evidence or recommended commands.

For long overnight campaigns, run the supervisor from a named `tmux` session so
the process is not tied to an agent-operated shell.

## Known Caveats

- Podman remains opt-in. Do not describe these paths as global defaults.
- Loopback provider URLs such as `http://localhost:8011/v1` work from the
  Phase 5 matrix because those configs use Podman host networking. Other
  Podman configs may need a non-loopback host gateway URL.
- SWE-bench Pro Podman is not supported from current evidence.
- external_benchmark Podman is deferred pending GPU/CDI validation.
- Do not expose a public TCP Podman API. Use the Unix user socket for
  Docker-compatible clients.
