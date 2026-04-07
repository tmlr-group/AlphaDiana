# MoltBench — LLM Agent Evaluation for GPU Kernel Optimization

MoltBench evaluates LLM coding agents on long-horizon, stateful GPU kernel
optimization tasks. Agents are given a seed project inside an ephemeral Docker
container and must iteratively edit code, benchmark, and keep or revert changes
— all autonomously.

## Architecture

```
AlphaDiana CLI  (python3 -m alphadiana.cli run config.yaml)
  |
  +-- MoltBenchDockerAgent.solve()        # alphadiana/agent/moltbench_docker.py
  |     |
  |     +-- 1. Write task.json to temp dir
  |     +-- 2. docker run -d --gpus ...
  |     |       -v task_dir:/workspace/task:ro
  |     |       -e MOLTBENCH_AGENT={agent_type}
  |     |       moltbench/{agent_type}
  |     +-- 3. docker wait (block until container exits)
  |     +-- 4. docker cp /workspace/artifacts/ -> host
  |     +-- 5. parse_results_json() + parse_trajectory()
  |
  +-- Scorer                              # alphadiana/scorer/moltbench.py
        +-- correctness, speedup, fast_p@{1.0, 1.1, 1.25, 1.5, 2.0, 3.0, 5.0}
```

Inside the container:

```
run_task.sh (entrypoint)
  +-- read_task()          <- /workspace/task/task.json
  +-- setup_problem()      <- bridge.py setup (fetch KernelBench problem)
  +-- install_round_guard  <- wrap bench_kb.py with round counter
  +-- case $MOLTBENCH_AGENT in
  |     claude-code)  claude --print --max-turns $N "$prompt"
  |     openclaw)     openclaw gateway -> curl SSE -> wait
  |     opencode)     opencode run --format json "$prompt"
  +-- collect_artifacts()  <- EXIT trap (always runs, even on crash/timeout)
```

All three agents share one `MoltBenchDockerAgent` class and one `run_task.sh`
entrypoint. The only differences are the Docker image (thin agent layer on top
of `gpu-base`) and the `launch_*()` function in `run_task.sh`.

### Image Layering

```
nvidia/cuda:12.8.0-devel-ubuntu22.04
  |
  +-- moltbench/gpu-base      ~24 GB  (PyTorch + Triton + AutoKernel)
        |
        +-- moltbench/claude-code   +0.3 GB  (Node.js + @anthropic-ai/claude-code)
        +-- moltbench/openclaw      +2.5 GB  (Node.js + OpenClaw gateway)
        +-- moltbench/opencode      +1.1 GB  (Node.js + opencode-ai)
```

### Agent Alignment (Zero-Injection)

All agents receive **identical user messages**. No workspace instruction files
(`CLAUDE.md`, `AGENTS.md`, `SOUL.md`, etc.) are injected into the container.
This ensures that evaluation differences come from agent/model capabilities,
not from prompt engineering.

The user message:

```
Read kernelbench/program_kb.md for full optimization instructions.

Then optimize KernelBench problem Level {level}, Problem {problem_id}: {name}.
The problem is already set up — kernel.py and workspace/kb_active/ are ready.
You have {max_rounds} optimization rounds. Backend: {backend}.
```

This prompt points agents to `program_kb.md` (KernelBench-specific instructions
already in the AutoKernel repo) rather than `program.md` (the general-purpose
pipeline), because MoltBench pre-sets each problem via `bridge.py setup` and
only evaluates the optimization phase.

## Seed Projects

Seed projects are external repositories used as evaluation targets. They are
**not vendored** — instead, they are cloned at Docker build time from the
commit pinned in `moltbench/seeds.yaml`.

Currently supported:

| Seed | Repo | Eval Target | Status |
|------|------|-------------|--------|
| **AutoKernel** | `RightNow-AI/autokernel` @ `7843582` | KernelBench L1-L4 (270 problems) — optimize `kernel.py` | Verified |
| QJL | `amirzandieh/QJL` @ `648b364` | CUDA C++ kernels — minimize runtime | Planned |
| Mini-SGLang | `sgl-project/mini-sglang` | Scheduler optimization — maximize throughput | Planned |

### How to Add a New Seed Project

1. **Add an entry to `moltbench/seeds.yaml`**:

   ```yaml
   my_project:
     repo: https://github.com/org/repo.git
     commit: "abc1234"
     description: >
       What the project does and what the agent should optimize.
     benchmark_id: my_project
     workdir: /workspace/my-project
     eval_script: bench.py
     base_image: nvidia/cuda:12.8.0-devel-ubuntu22.04
   ```

2. **Create a Dockerfile** at `moltbench/docker/Dockerfile.my-project-base`:

   ```dockerfile
   FROM nvidia/cuda:12.8.0-devel-ubuntu22.04
   # ... install dependencies ...
   ARG REPO=https://github.com/org/repo.git
   ARG COMMIT=abc1234
   RUN git clone "$REPO" /workspace/my-project && \
       cd /workspace/my-project && git checkout "$COMMIT"
   # ... install Python deps, etc. ...
   COPY moltbench/docker/run_task.sh /workspace/run_task.sh
   RUN chmod +x /workspace/run_task.sh
   ENTRYPOINT ["/workspace/run_task.sh"]
   ```

3. **Add a `launch_*()` function** in `moltbench/docker/run_task.sh`:

   The function should: set up the workspace, build a prompt, invoke the agent
   CLI, and let the EXIT trap collect artifacts.

4. **Register the benchmark** in `alphadiana/benchmark/`:

   Create a loader that produces `BenchmarkTask` objects with `task_json`
   containing the fields needed by `run_task.sh` (level, problem_id, etc.).

5. **Add YAML configs** in `configs/examples/`.

6. **Build and test**:

   ```bash
   docker build --network host -f moltbench/docker/Dockerfile.my-project-base \
       -t moltbench/my-project-base .
   # Then build agent layers on top
   ```

## Building

All docker builds use the **AlphaDiana repo root** as build context.

```bash
cd /path/to/AlphaDiana

# 1. Build GPU base image (clones AutoKernel at build time, ~15 min)
docker build --network host \
    -f moltbench/docker/Dockerfile.base \
    -t moltbench/gpu-base .

# 2. Build agent images (each ~2-5 min)
docker build --network host \
    -f moltbench/docker/Dockerfile.openclaw \
    -t moltbench/openclaw .

docker build --network host \
    -f moltbench/docker/Dockerfile.opencode \
    -t moltbench/opencode .

docker build --network host \
    -f moltbench/docker/Dockerfile.claude-code \
    -t moltbench/claude-code .
```

**Build time estimates** (with network access):

| Image | Time | Size |
|-------|------|------|
| `moltbench/gpu-base` | ~15 min | ~24 GB |
| `moltbench/openclaw` | ~5 min | +2.5 GB |
| `moltbench/opencode` | ~2 min | +1.1 GB |
| `moltbench/claude-code` | ~2 min | +0.3 GB |

## Running Evaluations

### Prerequisites

- Docker 28+ with NVIDIA Container Toolkit
- NVIDIA GPU (RTX 5880 Ada 48 GB verified)
- NVIDIA Driver 570+ (CUDA 12.8)
- LLM API access (see per-agent requirements below)

### Environment Setup

```bash
cd /path/to/AlphaDiana

# Create .env with API keys
cat > .env << 'EOF'
# For OpenClaw / OpenCode (any OpenAI-compatible API)
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.example.com/v1/
OPENAI_MODEL_NAME=deepseek-chat

# For Claude Code (Anthropic API)
ANTHROPIC_API_KEY=sk-ant-...
EOF

# Load environment
set -a && source .env && set +a
```

### Single Task (Smoke Test)

```bash
python3 -m alphadiana.cli run configs/examples/moltbench_openclaw_smoke.yaml
```

### Batch Evaluation (L1 P1-20)

```bash
# OpenClaw
python3 -m alphadiana.cli run configs/examples/moltbench_openclaw_L1_batch_v2.yaml

# OpenCode
python3 -m alphadiana.cli run configs/examples/moltbench_opencode_L1_batch.yaml
```

### Output Structure

Each task produces:

```
moltbench_artifacts_{agent}_{task}/
  +-- results.json           # {correctness, speedup, kernel_time_ms, ...}
  +-- kernel.py              # Final optimized kernel (best version)
  +-- kernel_history/        # Per-round snapshots (round_01.py, ...)
  +-- final_bench.log        # Last bench_kb.py full output
  +-- trajectory.jsonl       # Agent tool call trace
  +-- container_stdout.log   # Container stdout
  +-- container_stderr.log   # Container stderr
```

## Reproducing Results

### Hardware

| Component | Spec |
|-----------|------|
| GPU | NVIDIA RTX 5880 Ada (48 GB VRAM) |
| Driver | NVIDIA 570+ |
| CUDA | 12.8 (in container) |
| CPU | Not a bottleneck |
| RAM | 64 GB+ recommended |

### Software Versions

| Component | Version |
|-----------|---------|
| PyTorch | >= 2.4.0 (installed from `cu128` index) |
| Triton | >= 3.3.0 |
| AutoKernel | commit `7843582` |
| OpenClaw | `ghcr.io/tsrigo/openclaw-reasoning:20260320` |
| OpenCode | `opencode-ai@1.3.2` |
| Claude Code | `@anthropic-ai/claude-code@latest` |

### LLM Configuration

| Agent | LLM | API Endpoint | Notes |
|-------|-----|-------------|-------|
| OpenClaw | DeepSeek-chat | `OPENAI_BASE_URL` | Any OpenAI-compatible API |
| OpenCode | DeepSeek-chat | `OPENAI_BASE_URL` | Any OpenAI-compatible API |
| Claude Code | Claude (Anthropic) | `ANTHROPIC_API_KEY` | Requires Anthropic API access |

### Evaluation Configs

Key parameters in batch YAML configs:

| Parameter | Value | Notes |
|-----------|-------|-------|
| `max_rounds` | 10 | Max bench_kb.py invocations per task |
| `backend` | triton | Triton JIT (no nvcc needed) |
| `request_timeout` | 900 s | Container-level timeout |
| `OPENCODE_TIMEOUT_SEC` | 600 s | OpenCode agent-level timeout |
| `gpus` | `"device=N"` | Single GPU allocation |
| `network_mode` | host | Required for LLM API access |

### Verified Results (AutoKernel L1 P1-20, aligned prompts, 2026-04-06)

| Metric | OpenClaw | OpenCode |
|--------|----------|----------|
| PASS | 12/20 (60%) | 12/20 (60%) |
| ERROR | 6/20 (30%) | 7/20 (35%) |
| FAIL | 2/20 (10%) | 1/20 (5%) |
| fast_p@1.0 | 6/20 (30%) | 5/20 (25%) |
| fast_p@1.5 | 0/20 (0%) | 0/20 (0%) |
| Best speedup | 1.074x (P017) | 1.075x (P017) |
| Median speedup (PASS) | 1.0x | 0.999x |

Both agents use DeepSeek-chat via the same API endpoint. The two agents have
the same pass rate but different failure patterns — OpenCode is more
conservative (fewer segfaults), OpenClaw is more aggressive (attempts
Triton/torch.compile but crashes more).

### Framework Alignment Verification

Baseline `kernel_time_ms` across three environments (no agent, same GPU):

| Problem | Host (AutoKernel) | Docker (no agent) | Pipeline |
|---------|-------------------|--------------------|----------|
| P013 | 9.49 ms | 9.69 ms | 9.46 ms |
| P017 | 13.72 ms | 13.79 ms | 13.84 ms |
| P019 | 32.33 ms | 32.37 ms | 32.37 ms |

All differences < 5%, confirming the Docker + pipeline layer does not
introduce measurement bias.

## File Layout

```
AlphaDiana/
  +-- alphadiana/
  |     +-- agent/moltbench_docker.py      # Docker container lifecycle
  |     +-- benchmark/moltbench.py         # Task loading (KernelBench problems)
  |     +-- benchmark/moltbench_utils.py   # Prompt builder, result parser
  |     +-- scorer/moltbench.py            # Correctness + fast_p scoring
  |
  +-- moltbench/
  |     +-- docker/
  |     |     +-- Dockerfile.base          # GPU base (clones AutoKernel)
  |     |     +-- Dockerfile.claude-code   # + Claude Code CLI
  |     |     +-- Dockerfile.openclaw      # + OpenClaw gateway
  |     |     +-- Dockerfile.opencode      # + OpenCode CLI
  |     |     +-- run_task.sh              # Shared entrypoint
  |     |     +-- openclaw.json            # OpenClaw config template
  |     |     +-- opencode.json.template   # OpenCode config template
  |     +-- seeds.yaml                     # Seed project declarations
  |     +-- docs/
  |           +-- README.md                # This file
  |
  +-- configs/examples/
        +-- moltbench_openclaw_L1_batch_v2.yaml
        +-- moltbench_opencode_L1_batch.yaml
        +-- moltbench_openclaw_smoke.yaml
        +-- ...
```

## Known Issues

| Issue | Impact | Status |
|-------|--------|--------|
| `bridge.py super()` bug | `ModelNew` instantiation fails for problems using `super(Model, self).__init__()` | Fixed in `run_task.sh` (auto-sed) |
| `profile.py` naming conflict | AutoKernel's `profile.py` shadows stdlib `profile` module, breaks `torch.compile` | Known upstream bug; affects P007 |
| RTX 5880 Ada not in GPU database | Roofline estimates are approximated | Does not affect correctness or speedup |
| Most problems timeout (600s) | Agent fails to optimize within time limit | DeepSeek-chat model capability limitation |
