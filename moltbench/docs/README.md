# MoltBench — LLM Agent Evaluation for GPU Kernel Optimization

MoltBench evaluates LLM coding agents on long-horizon, stateful GPU kernel
optimization tasks. The current evaluation task is **KernelBench** (270
problems across 4 difficulty levels). Agents are given a seed project inside
an ephemeral Docker container and must iteratively edit code, benchmark, and
keep or revert changes — all autonomously.

---

## Quick Start

### Prerequisites

- Docker 28+ with [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- NVIDIA GPU with Driver 570+ (CUDA 12.8; RTX 5880 Ada 48 GB verified)
- LLM API access — see [Supported Agents](#supported-agents) for which keys you need
- Python 3.11+ on host (for AlphaDiana CLI)

### Step 1: Build Docker Images

```bash
cd /path/to/AlphaDiana

# GPU base image — clones AutoKernel at build time (~15 min)
docker build --network host -f moltbench/docker/Dockerfile.base -t tmlrgroup/alphadiana:gpu-base .

# Pick one or more agent layers:
docker build --network host -f moltbench/docker/Dockerfile.openclaw    -t tmlrgroup/alphadiana:openclaw .     # ~5 min
docker build --network host -f moltbench/docker/Dockerfile.opencode    -t tmlrgroup/alphadiana:opencode .     # ~2 min
docker build --network host -f moltbench/docker/Dockerfile.claude-code -t tmlrgroup/alphadiana:claude-code .  # ~2 min
```

### Step 2: Configure API Keys

Each agent requires different API credentials. You only need to configure
the keys for the agent(s) you plan to use:

```bash
cat > .env << 'EOF'
# ── OpenClaw / OpenCode (any OpenAI-compatible API) ──
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.example.com/v1/
OPENAI_MODEL_NAME=deepseek-chat

# ── Claude Code (optional if host ~/.claude auth is already set up) ──
ANTHROPIC_API_KEY=sk-ant-...
EOF

set -a && source .env && set +a
```

If you already use Claude Code on the host, MoltBench can mount
`~/.claude/` and `~/.claude.json` into the container, so an explicit
`ANTHROPIC_API_KEY` is optional for the `claude-code` agent.

### Step 3: Run a Smoke Test — KernelBench Single Problem (~5-20 min)

```bash
# Claude Code (works with host ~/.claude auth or ANTHROPIC_API_KEY)
python3 -m alphadiana.cli run configs/examples/moltbench_claude_code_codex.yaml

# OpenClaw (requires OPENAI_* keys)
python3 -m alphadiana.cli run configs/examples/moltbench_openclaw_smoke.local.yaml

# OpenCode (requires OPENAI_* keys)
python3 -m alphadiana.cli run configs/examples/moltbench_opencode_smoke.local.yaml
```

This runs **KernelBench L1 P001** (square matrix multiplication). Expected
output: a per-execution artifact directory under
`moltbench_artifacts_*/moltbench_L1_P001/sample_0/<execution_id>/`
containing `results.json` with `correctness` and `speedup` fields.

Verified on **2026-04-08**:

- `claude-code` completed successfully using host Claude auth mounts, with no
  explicit `ANTHROPIC_API_KEY`.
- `openclaw` with `minimax-m2.5` needed roughly 10 minutes and passed with the
  longer local-smoke timeout defaults.
- `opencode` with `minimax-m2.5` needed roughly 17 minutes and passed with
  `OPENCODE_TIMEOUT_SEC=1200`.

### Step 4: Run a Full Batch — KernelBench L1 P1-20 (~5 hours)

```bash
# OpenClaw — 20 problems, sequential, ~15 min each
python3 -m alphadiana.cli run configs/examples/moltbench_openclaw_L1_batch_v2.yaml

# OpenCode — same 20 problems
python3 -m alphadiana.cli run configs/examples/moltbench_opencode_L1_batch.yaml
```

KernelBench has 4 levels (L1-L4, 270 problems total). L1 with 20 problems is
a practical starting point — enough to see meaningful pass rates and failure
patterns without requiring days of GPU time.

---

## Supported Agents

MoltBench supports three LLM coding agents. Each agent runs inside its own
Docker image and requires different API credentials:

| Agent | Docker Image | API Keys Required | LLM | Config Example |
|-------|-------------|-------------------|-----|----------------|
| **OpenClaw** | `tmlrgroup/alphadiana:openclaw` | `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `OPENAI_MODEL_NAME` | Any OpenAI-compatible (e.g., DeepSeek-chat) | `moltbench_openclaw_smoke.local.yaml` |
| **OpenCode** | `tmlrgroup/alphadiana:opencode` | `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `OPENAI_MODEL_NAME` | Any OpenAI-compatible (e.g., DeepSeek-chat) | `moltbench_opencode_smoke.local.yaml` |
| **Claude Code** | `tmlrgroup/alphadiana:claude-code` | `ANTHROPIC_API_KEY` or host `~/.claude` auth | Claude (Anthropic) | `moltbench_claude_code_codex.yaml` |

**Switching agents** is done by choosing a different YAML config — no code
changes needed. The `agent_type` field in the config selects the Docker image
and the corresponding `launch_*()` function in `run_task.sh`:

```yaml
# configs/examples/moltbench_openclaw_smoke.yaml
task: kernelbench                          # <-- evaluation task
agent:
  config:
    agent_type: openclaw                   # <-- change this to switch agents
    image_prefix: tmlrgroup/alphadiana     # image = image_prefix:agent_type
```

All three agents share the same `MoltBenchDockerAgent` class and `run_task.sh`
entrypoint — the only difference is the thin Docker layer and the agent CLI.

---

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
  |     |       tmlrgroup/alphadiana:{agent_type}
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

See [Supported Agents](#supported-agents) for details on each agent's API
requirements and how to switch between them.

### Image Layering

```
nvidia/cuda:12.8.0-devel-ubuntu22.04
  |
  +-- tmlrgroup/alphadiana:gpu-base      ~24 GB  (PyTorch + Triton + AutoKernel)
        |
        +-- tmlrgroup/alphadiana:claude-code   +0.3 GB  (Node.js + @anthropic-ai/claude-code)
        +-- tmlrgroup/alphadiana:openclaw      +2.5 GB  (Node.js + OpenClaw gateway)
        +-- tmlrgroup/alphadiana:opencode      +1.1 GB  (Node.js + opencode-ai)
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

## Evaluation Tasks

### KernelBench (Active)

The current evaluation task is **KernelBench** — 270 GPU kernel optimization
problems across 4 difficulty levels (L1-L4), powered by the AutoKernel seed
project. Each problem provides a PyTorch model; the agent must optimize
`kernel.py` to produce a correct, faster implementation.

## Seed Projects

Seed projects are external repositories used as evaluation targets. They are
**not vendored** — instead, they are cloned at Docker build time from the
commit pinned in `moltbench/seeds.yaml`.

| Seed | Repo | Task | Status |
|------|------|------|--------|
| **AutoKernel** | `RightNow-AI/autokernel` @ `7843582` | **KernelBench** L1-L4 (270 problems) — optimize `kernel.py` | Verified |
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
       -t tmlrgroup/alphadiana:my-project-base .
   # Then build agent layers on top and run a smoke test
   ```

## Output Structure

Each task produces:

```
moltbench_artifacts_{agent}_{task}/
  +-- moltbench_L1_P001/
        +-- sample_0/
              +-- <execution_id>/
                    +-- results.json                    # final scored result
                    +-- final_verification_results.json # raw final bench result
                    +-- kernel.py                       # Final optimized kernel
                    +-- kernel_history/                # Per-round snapshots
                    +-- final_bench.log                # Last bench_kb.py output
                    +-- trajectory.jsonl               # Agent tool call trace
                    +-- container_stdout.log
                    +-- container_stderr.log
```

Artifacts are isolated by `task_id/sample_index/execution_id`, so repeated
runs, retries, and multi-sample evaluation do not overwrite each other. If an
agent crashes or times out before producing a valid final result,
`results.json` is written as `INVALID`, while
`final_verification_results.json` is still preserved for debugging.

## Reproducing Results

### Hardware and Software

| Component | Version / Spec |
|-----------|---------------|
| GPU | NVIDIA RTX 5880 Ada (48 GB VRAM) |
| NVIDIA Driver | 570+ |
| CUDA (in container) | 12.8 |
| PyTorch | >= 2.4.0 (from `cu128` index) |
| Triton | >= 3.3.0 |
| AutoKernel | commit `7843582` |
| OpenClaw | `tmlrgroup/alphadiana:openclaw` |
| OpenCode | `opencode-ai@1.3.2` |
| Claude Code | `@anthropic-ai/claude-code@latest` |

### LLM Configuration

| Agent | LLM | API Endpoint | Notes |
|-------|-----|-------------|-------|
| OpenClaw | DeepSeek-chat | `OPENAI_BASE_URL` | Any OpenAI-compatible API |
| OpenCode | DeepSeek-chat | `OPENAI_BASE_URL` | Any OpenAI-compatible API |
| Claude Code | Claude (Anthropic) | `ANTHROPIC_API_KEY` or host `~/.claude` auth | Host auth mount is supported |

### Evaluation Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| `max_rounds` | 10 | Max bench_kb.py invocations per task |
| `backend` | triton | Triton JIT (no nvcc needed) |
| `request_timeout` | 900 s | Container-level timeout |
| `OPENCLAW_AGENT_TIMEOUT_SEC` | 600 s | Safer default for slower providers |
| `OPENCODE_TIMEOUT_SEC` | 1200 s | OpenCode may need 15-20 min for a single smoke run |
| `gpus` | `"device=N"` | Single GPU allocation |
| `network_mode` | host | Required for LLM API access |

### Verified Results

**KernelBench L1 P1-20 (via AutoKernel), aligned prompts, DeepSeek-chat, 2026-04-06:**

| Metric | OpenClaw | OpenCode |
|--------|----------|----------|
| PASS | 12/20 (60%) | 12/20 (60%) |
| ERROR | 6/20 (30%) | 7/20 (35%) |
| FAIL | 2/20 (10%) | 1/20 (5%) |
| fast_p@1.0 | 6/20 (30%) | 5/20 (25%) |
| fast_p@1.5 | 0/20 (0%) | 0/20 (0%) |
| Best speedup | 1.074x (P017) | 1.075x (P017) |
| Median speedup (PASS) | 1.0x | 0.999x |

Both agents have the same pass rate but different failure patterns — OpenCode
is more conservative (fewer segfaults), OpenClaw is more aggressive (attempts
Triton/torch.compile but crashes more often).

**Framework alignment verification** (baseline kernel_time_ms, no agent, same GPU):

| Problem | Host (AutoKernel) | Docker (no agent) | Pipeline |
|---------|-------------------|--------------------|----------|
| P013 | 9.49 ms | 9.69 ms | 9.46 ms |
| P017 | 13.72 ms | 13.79 ms | 13.84 ms |
| P019 | 32.33 ms | 32.37 ms | 32.37 ms |

All differences < 5%, confirming the Docker + pipeline layer does not
introduce measurement bias.

**Single-problem smoke validation (2026-04-08):**

| Agent | Model / Auth | Outcome | Notes |
|-------|--------------|---------|-------|
| Claude Code | host `~/.claude` auth | PASS | No explicit `ANTHROPIC_API_KEY` required |
| OpenClaw | `minimax-m2.5` via OpenAI-compatible API | PASS | Needed `OPENCLAW_AGENT_TIMEOUT_SEC=600` |
| OpenCode | `minimax-m2.5` via OpenAI-compatible API | PASS | Needed `OPENCODE_TIMEOUT_SEC=1200` |

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
| Slow providers may exceed short smoke defaults | Healthy runs can look like failures if agent timeouts are too short | Use the longer local-smoke defaults (OpenClaw 600 s, OpenCode 1200 s) |
| Triton/CUDA-extension attempts may fail with missing `Python.h` | Some generated kernels cannot compile inside AutoKernel's Python 3.10 venv | Agents often need to fall back to pure PyTorch or JIT paths |
