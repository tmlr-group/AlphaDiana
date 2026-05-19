# AlphaDiana Evaluation Tutorial: ZeroClaw + AIME 2026

This tutorial covers two execution modes for **ZeroClaw on AIME 2026**.

If you only want the shortest working command sequence, start with
[`zeroclaw_aime2026_runbook.md`](zeroclaw_aime2026_runbook.md) and come back to
this tutorial for the longer explanation.

| Mode | When to use | Requires |
|------|-------------|----------|
| **Local** (quick start) | Smoke testing, no infra | `zeroclaw` binary in PATH |
| **ROCK auto-deploy** | Production / paper runs | ROCK services + Docker image |

Both modes write results to `results/<run_id>/tasks/<task_id>.json`.

---

## What Is Different From The Root README

The root [`README.md`](../README.md) describes the general AlphaDiana quick
start with OpenClaw. The ZeroClaw path differs in three ways:

- **Local mode** bypasses ROCK entirely: no `start_rock.sh`, no Docker image,
  no `sandbox:` entry in the YAML. The `zeroclaw` binary runs directly on the
  host in a temporary workspace.
- **ROCK auto-deploy mode** follows the same control plane as OpenClaw (Redis +
  Ray + ROCK admin/proxy) but uses a ZeroClaw-specific sandbox image and bridge
  instead of the OpenClaw gateway.
- Config uses `agent.name: zeroclaw` and `agent.config.api_base / api_key /
  model` instead of OpenClaw keys.

---

## Prerequisites

- Linux host.
- Conda environment activated (`source scripts/activate.sh`).
- `zeroclaw` binary installed and in PATH (`zeroclaw --version` prints a version).
- A reachable OpenAI-compatible provider:

  ```bash
  export OPENAI_BASE_URL=https://api.example.com/v1/
  export OPENAI_API_KEY=sk-...
  export OPENAI_MODEL_NAME=minimax-m2.5
  ```

  Any OpenAI-compatible endpoint works. The provider is auto-detected as
  `custom:<URL>` when `OPENAI_BASE_URL` is not the standard OpenAI address.

- **(ROCK mode only)** Docker access + ROCK services running — see
  [Step A](#step-a-rock-auto-deploy-mode).

---

## Mode 1: Local Mode (Quick Start, No ROCK)

This is the **validated smoke path** for this PR. No Docker image build and no
ROCK services are required.

### Config

[`configs/examples/zeroclaw_aime2026_local_smoke.yaml`](../configs/examples/zeroclaw_aime2026_local_smoke.yaml)

Key fields:

```yaml
agent:
  name: zeroclaw
  config:
    model: "${OPENAI_MODEL_NAME}"
    api_base: "${OPENAI_BASE_URL}"
    api_key: "${OPENAI_API_KEY}"
    request_timeout: 300
    max_tool_iterations: 50
    max_actions_per_hour: 100

benchmark:
  name: aime
  config:
    dataset: "MathArena/aime_2026"
    split: "train"
    max_tasks: 1   # smoke default — remove for a full run

sandbox: null      # no ROCK, runs in a local temp dir
```

### Run

```bash
source scripts/activate.sh

export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5

alphadiana validate configs/examples/zeroclaw_aime2026_local_smoke.yaml
alphadiana run    configs/examples/zeroclaw_aime2026_local_smoke.yaml
```

### Verified smoke result — 2026-04-16

Smoke run on this machine with **MiniMax M2.5** via the OpenAI-compatible endpoint:

```
2026-04-16 14:26:16 | Loaded 1 tasks from benchmark 'aime'
2026-04-16 14:30:34 | Task aime_0 done: predicted='277' vs ground_truth='277' correct=True

Run completed: zeroclaw_local_smoke_minimax_aime2026
  Accuracy:   1.0000
  Mean Score: 1.0000
  Pass@1:    1.0000
  Tasks:      1/1 completed
```

Playbook pass criteria met:

| Criterion | Result |
|-----------|--------|
| `results/.../tasks/aime_0.json` exists | ✓ |
| No `error` dict in task record | ✓ |
| Dashboard shows `O` (correct) | ✓ |

Task record excerpt:

```json
{
  "task_id": "aime_0",
  "agent_name": "zeroclaw",
  "agent_version": "0.1.7",
  "problem": "Patrick started walking at a constant rate...",
  "ground_truth": "277",
  "predicted": "277",
  "correct": true,
  "score": 1.0,
  "rationale": "Exact match after normalization (math-verify fallback)."
}
```

Run time: ~4 min 20 s for 1 AIME task.

Dashboard file:

```
results/zeroclaw_local_smoke_minimax_aime2026/status/dashboard.txt
Problem aime_0: O (0 left)
```

### Verification commands

```bash
# Confirm task JSON exists and is correct
cat results/zeroclaw_local_smoke_minimax_aime2026/tasks/aime_0.json \
  | python3 -c "import sys,json; r=json.load(sys.stdin); print('predicted:', r['predicted'], 'correct:', r['correct'])"

# Confirm dashboard shows O (correct execution)
cat results/zeroclaw_local_smoke_minimax_aime2026/status/dashboard.txt
```

---

## Mode 2: ROCK Auto-Deploy Mode (Production)

This mode follows the same `ROCK → sandbox → bridge → ZeroClaw CLI` flow as
OpenClaw. Use it for multi-sandbox concurrent runs or paper-quality evaluations.

### Step A: ROCK auto-deploy mode

#### A1. Start host ROCK services

```bash
bash scripts/start_zeroclaw.sh
source scripts/rock_env.sh
```

The script starts Redis, Ray, ROCK admin (`9002`), and ROCK proxy (`9017`),
verifies the routes, checks that the ZeroClaw image exists, and validates the
example config.

Because `bash scripts/start_zeroclaw.sh` runs in a subprocess, it does not
export `ROCK_BASE_URL` / `ROCK_PROXY_URL` back to your current shell. Source
`scripts/rock_env.sh` before launching the benchmark so
`configs/examples/zeroclaw_aime2026.yaml` picks up the active local ROCK URLs.

For a stronger host-isolation posture, use both of these knobs together:

- export `ROCK_WORKER_ENV_TYPE=pip` (or `uv`) before starting ROCK, so the worker
  does not mount the host project tree and `.venv` into the sandbox.
- set `agent.config.rock_use_kata_runtime: true` in the run config, so ROCK uses
  Kata instead of the default privileged Docker mode.

#### A2. Build the ZeroClaw reasoning image

```bash
docker build -f zeroclaw_deploy/Dockerfile -t zeroclaw-reasoning:0.6.9 .
```

Quick sanity check:

```bash
docker run --rm --entrypoint zeroclaw zeroclaw-reasoning:0.6.9 --version
```

#### A3. Set provider environment variables

```bash
export OPENAI_BASE_URL=https://api.example.com/v1/
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
```

#### A4. Validate and run

```bash
alphadiana validate configs/examples/zeroclaw_aime2026.yaml
alphadiana run    configs/examples/zeroclaw_aime2026.yaml
```

### Config

[`configs/examples/zeroclaw_aime2026.yaml`](../configs/examples/zeroclaw_aime2026.yaml)

Additional fields vs local mode:

```yaml
agent:
  config:
    rock_image: "zeroclaw-reasoning:0.6.9"   # Docker image with zeroclaw preinstalled
    rock_memory: "4g"
    rock_cpus: 1
    rock_startup_timeout: 600
    admin_base_url: "${ROCK_BASE_URL}"       # e.g. http://127.0.0.1:9002
    proxy_base_url: "${ROCK_PROXY_URL}"      # e.g. http://127.0.0.1:9017/apis/envs/sandbox/v1
    request_timeout: 1200

sandbox: null   # auto-created by runner when rock_image is set
```

### What happens internally

1. AlphaDiana calls `POST /start_async` on ROCK admin to create a sandbox.
2. The bridge script (`zeroclaw_deploy/zeroclaw_bridge.py`) is uploaded into
   the sandbox and started as a background HTTP server on port 8080.
3. AlphaDiana routes `POST /chat/completions` through the ROCK proxy to the
   bridge. The bridge calls `zeroclaw agent -m` inside the sandbox and returns
   the output in OpenAI format.
4. If the ROCK proxy times out before the full response is ready, the runner
   falls back to direct sandbox CLI execution (same sandbox, no restart).

### Expected console output

```text
Auto-created ROCK sandbox for zeroclaw concurrent isolation ...
Task aime_0 done: predicted='277' vs ground_truth='277' correct=True
Run completed: zeroclaw_minimax-m2.5_aime_2026
  Accuracy:   1.0000
  Tasks:      1/1 completed
```

---

## Running the Full Benchmark

Remove or raise `benchmark.config.max_tasks` in the config, then rerun:

```bash
# Local mode
alphadiana run configs/examples/zeroclaw_aime2026_local_smoke.yaml \
  -o benchmark.config.max_tasks=30

# ROCK mode
alphadiana run configs/examples/zeroclaw_aime2026.yaml \
  -o benchmark.config.max_tasks=30
```

AIME 2026 has 30 problems (2 contests × 15 problems).

---

## Troubleshooting

### `zeroclaw: command not found`

Install zeroclaw locally (Rust toolchain required):

```bash
cargo install zeroclawlabs
```

Or use the optional `install_command` in agent config:

```yaml
agent:
  config:
    install_command: "cargo install zeroclawlabs"
```

### Proxy variables cause HuggingFace download failures

```bash
source scripts/rock_env.sh   # unsets ALL_PROXY, HTTP_PROXY, HTTPS_PROXY
```

Or prefix the run:

```bash
env -u ALL_PROXY -u HTTP_PROXY -u HTTPS_PROXY alphadiana run ...
```

### ROCK mode: `start_async` returns `404`

The ROCK admin and proxy bind to different ports. Check:

```bash
ss -ltnp | grep ':9002\|:9017'
```

- `9002` → `rock.admin.main --role admin`
- `9017` → `rock.admin.main --role proxy`

### ROCK mode: Redis connection refused

```bash
source scripts/activate.sh
docker rm -f "$ROCK_REDIS_CONTAINER" 2>/dev/null || true
docker run -d --restart unless-stopped \
  --name "$ROCK_REDIS_CONTAINER" \
  -p "$ROCK_REDIS_PORT:6379" \
  redis/redis-stack-server:latest
```

---

## Related Files

| File | Purpose |
|------|---------|
| [`configs/examples/zeroclaw_aime2026_local_smoke.yaml`](../configs/examples/zeroclaw_aime2026_local_smoke.yaml) | Local-mode smoke config (validated) |
| [`configs/examples/zeroclaw_aime2026.yaml`](../configs/examples/zeroclaw_aime2026.yaml) | ROCK auto-deploy config |
| [`zeroclaw_deploy/Dockerfile`](../zeroclaw_deploy/Dockerfile) | ROCK-mode sandbox image |
| [`zeroclaw_deploy/zeroclaw_bridge.py`](../zeroclaw_deploy/zeroclaw_bridge.py) | HTTP bridge injected into ROCK sandbox |
| [`scripts/start_zeroclaw.sh`](../scripts/start_zeroclaw.sh) | ROCK-mode host startup helper |
| [`alphadiana/agent/zeroclaw.py`](../alphadiana/agent/zeroclaw.py) | ZeroClaw agent implementation |
| [`external_benchmark/docs/zeroclaw.md`](../external_benchmark/docs/zeroclaw.md) | external_benchmark / KernelBench path |
