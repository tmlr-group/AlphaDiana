# OpenCode Agent: Docker Isolation

Run the `opencode` CLI inside a Docker container instead of directly on the
host, providing a practical process and filesystem boundary for benchmark
execution on supported paths.

This is not a formal security sandbox claim. It is a weaker, paper-safe
containment story: the benchmark controller runs inside a disposable container
instead of as a host process, while the benchmark still keeps normal network
access and Docker-level limitations.

## Quick Start

```bash
# 1. Build the controller image (one-time)
docker build --network host \
  -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .

# 2. Run any opencode config with Docker isolation
python -m alphadiana.cli run configs/examples/opencode_minimax_imo_answerbench.yaml \
  -o agent.config.controller_mode=docker
```

## How It Works

### Host Mode

```
AlphaDiana Runner
  -> subprocess.Popen("opencode run --format json ...")
  -> opencode runs as a host process with full filesystem access
```

### Docker Mode (`controller_mode=docker`)

```
AlphaDiana Runner
  -> docker run --rm
       --network=host
       --user=UID:GID
       -v /tmp/workdir:/tmp/workdir
       -e HOME=/tmp/workdir/.controller-home
       -e OPENAI_API_KEY=...
       -e OPENAI_BASE_URL=...
       alphadiana/tb2-opencode-controller:latest
       node /usr/lib/node_modules/opencode-ai/bin/opencode
         run --format json ...
  -> opencode runs inside a disposable controller container
```

Key properties:
- `--user=UID:GID` matches the host user, preventing root-owned files on cleanup
- `HOME` is set to a writable directory inside the mounted workdir (prevents Bun `mkdir /.local` errors)
- `--network=host` allows the container to reach the LLM API endpoint
- Only the temporary workdir is mounted by default; arbitrary host paths are
  not exposed to the controller container

## YAML Config

```yaml
agent:
  name: opencode
  config:
    controller_mode: docker                                    # Required: enables Docker isolation
    # controller_image: alphadiana/tb2-opencode-controller:latest  # Optional: default image
    # controller_network: host                                     # Optional: default network
    model_name: qwen/qwen3-235b-a22b-2507
    api_base: ${OPENAI_BASE_URL}
    api_key: ${OPENAI_API_KEY}
    timeout: 1800
```

| Config Key | Default | Description |
|------------|---------|-------------|
| `controller_mode` | `"host"` if omitted in a custom config | Exact value: `"host"` = direct subprocess, `"docker"` = container isolation |
| `controller_image` | `alphadiana/tb2-opencode-controller:latest` | Docker image containing opencode CLI |
| `controller_network` | `"host"` | Docker network mode |

The checked-in benchmark configs in this repo now explicitly set
`controller_mode: docker` for the plain OpenCode benchmark paths. The agent
still supports `controller_mode: host` when you need the older local CLI path
for debugging.

## Prerequisites

### Build the Controller Image

```bash
docker build --network host \
  -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .
```

The Dockerfile installs:
- Ubuntu 22.04
- Node.js 22 (via nodesource)
- opencode-ai CLI 1.3.2 (via npm)
- Standard shell tools (git, curl, python3, sqlite3, etc.)

If Docker Hub is blocked (e.g., behind a proxy), use `--network host` and ensure a transparent proxy or mirror is configured for `archive.ubuntu.com` and `registry.npmjs.org`.

### For Host Mode (no Docker)

Install opencode CLI directly:

```bash
# Node.js 22+ required
conda create -n node22 -y -c conda-forge nodejs=22
conda activate node22
npm install -g opencode-ai@1.3.2

# Add to PATH when running AlphaDiana
export PATH="/path/to/node22/bin:$PATH"
```

## Smoke Test Reproduction

### IMO-AnswerBench (Docker Mode)

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=sk-or-v1-...

# Build image if not already done
docker build --network host \
  -f docker/terminal_bench2/Dockerfile.opencode-controller \
  -t alphadiana/tb2-opencode-controller:latest .

# Run with Docker isolation
python -m alphadiana.cli run configs/examples/opencode_minimax_imo_answerbench.yaml \
  -o run_id=imo_opencode_docker_test \
  -o agent.config.controller_mode=docker \
  --redo-all

# Verify
python3 -c "
import json, glob
for f in glob.glob('results/*/imo_opencode_docker_test/tasks/*.json'):
    d = json.load(open(f))
    if isinstance(d, list): d = d[-1]
    print('correct:', d.get('correct'), 'error:', d.get('error') is not None)
"
# Expected: correct: True/False  error: False
```

### HLE (Docker Mode)

```bash
export HF_TOKEN=hf_...  # Required for gated dataset

python -m alphadiana.cli run configs/examples/opencode_minimax_hle.yaml \
  -o run_id=hle_opencode_docker_test \
  -o agent.config.controller_mode=docker \
  --redo-all
```

## Multimodal Support

Multimodal image input is already supported on `main` (independent of this PR). When a benchmark task has image attachments (e.g., HLE items with embedded images), the opencode agent:

1. Saves attachments to the workdir via `write_attachments()`
2. Declares `modalities: {input: [text, image]}` in opencode config
3. Passes images via `--file` flags to the opencode CLI

This works in both host and Docker mode. Docker mode mounts the workdir, so image files are accessible inside the container.

**Important**: The LLM model must support both **vision input** and **tool calling**. Example models:

| Model | Vision | Tool Call | Works with opencode |
|-------|--------|-----------|---------------------|
| `qwen/qwen3-vl-235b-a22b-instruct` | Yes | Yes | Yes |
| `qwen/qwen2.5-vl-72b-instruct` | Yes | No | No (opencode needs tool_call) |
| `qwen/qwen3-235b-a22b-2507` | No | Yes | Text only |

## Verified Results (A800, 2026-04-19)

### Docker vs Host Comparison

| Benchmark | Host Mode | Docker Mode | Notes |
|-----------|-----------|-------------|-------|
| IMO-AnswerBench | score=1.0, wall=163s | score=1.0, wall=168s | Both correct |
| HLE | score=0.0, wall=329s | score=1.0, wall=691s | Both complete, no errors |

### OpenRouter Qwen3.5-27B Smoke Recheck

- Local recheck on April 19, 2026 against `https://openrouter.ai/api/v1`
  with `qwen/qwen3.5-27b` also completed cleanly in Docker mode.
- Evidence:
  - `pr32_review_imo_opencode_docker_qwen35_20260419` -> `score=1.0`
  - `pr32_review_hle_opencode_docker_qwen35_20260419` -> `score=1.0`
- Reviewer-facing commands and task-level evidence are recorded in
  `context/qwen-openrouter-pilots/pilot-validation.md`.

### OpenRouter Qwen3.5-27B Benchmark Confirmation (2026-04-20)

- `pilot_20260420_qwen35_27b_gpqa_diamond_opencode_t3_docker_default`:
  `3/3` task records, all `score=1`, every task recorded
  `controller_mode=docker` and `transport=opencode_cli_container`
- `pilot_20260420_qwen35_27b_imo_answerbench_opencode_t3_docker_default`:
  `3/3` normal task records, scores `1/0/0`, every task recorded
  `controller_mode=docker` and `transport=opencode_cli_container`
- `pilot_20260420_qwen35_27b_hle_opencode_t3_docker_default`:
  `3/3` normal task records, scores `1/0/0`, every task recorded
  `controller_mode=docker` and `transport=opencode_cli_container`
- `pilot_20260420_qwen35_27b_mmmu_pro_opencode_t3_vision_docker_default`:
  `3/3` normal task records, scores `0/1/1`, every task recorded
  `controller_mode=docker`, `transport=opencode_cli_container`, and
  `num_attachments=1`
- Reviewer-facing commands, logs, and task-level evidence are recorded in
  `context/qwen-openrouter-pilots/pilot-validation.md`.

### Multimodal (VL Model + Image)

```
Model:  qwen/qwen3-vl-235b-a22b-instruct via OpenRouter
Task:   HLE idx=53 (image contains Python pseudocode)
Result: predicted="F. does not terminate" (model analyzed image content)
```

## Bug Fixes During Development

| Bug | Root Cause | Fix |
|-----|-----------|-----|
| `PermissionError` on temp dir cleanup | Docker ran as root, created root-owned files | `--user=$(id -u):$(id -g)` |
| `EACCES: mkdir /.local` | Bun runtime needs writable HOME, defaults to `/` for non-root | `HOME={workdir}/.controller-home` |

## Architecture

### Isolation Level Comparison

| Agent | IMO/HLE | terminal-bench-2 | Level |
|-------|---------|-------------------|-------|
| `direct_llm` | Host HTTP call | Docker task + host relay | Low |
| `opencode (host)` | Host process | Docker controller | Low |
| **`opencode (docker)`** | **Docker container** | Docker controller | **Medium** |
| `openclaw` | ROCK sandbox | Docker controller | High |

### Limitations

1. **Not ROCK-level isolation** — no CPU/memory quotas, no sandbox lifecycle management
2. **`--network=host`** — container shares host network; opencode can reach any endpoint
3. **Requires Docker daemon** — the user running AlphaDiana must have `docker` access
4. **opencode is third-party** — CLI behavior controlled by the opencode-ai npm package

## Files Changed

| File | Change |
|------|--------|
| `alphadiana/agent/opencode.py` | Add `controller_mode`, `_run_in_docker()`, dispatch in `_solve_cli()` |
| `docs/opencode-docker-isolation.md` | This document |
