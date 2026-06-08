# mini-SGLang Seed Project — Setup, Execution & Evaluation Guide

This document explains how to set up, run, and evaluate the mini-SGLang
throughput optimization pipeline from scratch on an A800 GPU server.

---

## Task Description

An LLM agent (running inside a Docker container) autonomously optimizes the
[mini-SGLang](https://github.com/sgl-project/mini-sglang) inference engine to
maximize **Qwen3-0.6B throughput (tokens/second)**.

The agent operates in a loop:
1. **Read** mini-sglang Python source code
2. **Edit** scheduler, engine, or attention files
3. **Benchmark** by calling `/eval/run_eval` (restarts the server + measures throughput)
4. **Keep or revert** based on the benchmark result
5. Repeat for up to N rounds

The agent is given context from real SGLang optimization PRs
(sglang#7563, sglang#20501, mini-sglang#97, mini-sglang#110) as reference.

### Architecture (Pattern A — Single Container)

```
┌─────────────────────────────────────────────────────────────────┐
│  Docker Container  (GPU + OpenClaw agent)                       │
│                                                                 │
│  run_task.sh (ENTRYPOINT)                                       │
│    ├── Read task.json                                           │
│    ├── Git init /workspace/repo (track agent changes)           │
│    ├── Start OpenClaw gateway (port 8080)                       │
│    ├── Send optimization task to agent via gateway               │
│    │     └── Agent loop:                                        │
│    │           read code → edit → /eval/run_eval → keep/revert  │
│    └── EXIT trap: independent final benchmark → results.json    │
│                                                                 │
│  /workspace/repo/python/minisgl/   ← agent-editable source     │
│  /eval/run_eval                    ← benchmark tool             │
│  /eval/bench_serving.py            ← throughput measurement     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Metrics

| Metric | Definition |
|--------|-----------|
| **throughput_tok_per_s** | Total output tokens / total wall time (64 concurrent requests, input=128, output=128 tokens) |
| **OG (optimization gain)** | `final_throughput / baseline_throughput` — how much faster the optimized code is vs. baseline |
| **correct** | All 64 benchmark requests completed without error |
| **baseline_throughput** | 5,026 tok/s (Qwen3-0.6B on A800, measured with page-aware scheduling from mini-sglang#97) |

**Scoring**: OG > 1.0 means the agent improved throughput. Higher is better.

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Server | NVIDIA A800 (or any GPU with ≥40GB VRAM) |
| Docker | 28+ with [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) |
| Python | 3.10+ on host (for AlphaDiana CLI) |
| LLM API | OpenRouter API key (for `qwen/qwen3-235b-a22b-2507`) |
| Base image | `jinbo/mini-sglang:clean` (pre-built with CUDA + PyTorch + flashinfer) |
| Model weights | Qwen3-0.6B at `/data0/models/modelscope/Qwen/Qwen3-0___6B` |

---

## Setup (One-Time)

### 1. Clone the repository

```bash
git clone git@github.com:tmlr-group/AlphaDiana.git
cd AlphaDiana
git checkout feat/mini-sglang-seed-project
```

### 2. Install AlphaDiana

```bash
pip install -e .
# or if using conda:
conda activate alphadiana
pip install -e .
```

### 3. Configure API keys

```bash
cat > .env << 'EOF'
OPENROUTER_API_KEY=sk-or-v1-your-key-here
# Alternative: set OPENAI_API_KEY + OPENAI_BASE_URL directly
EOF
set -a && source .env && set +a
```

### 4. Build Docker images

```bash
# Step 1: Base image — mini-sglang source + eval scripts (~5 min)
docker build --network host \
  -f moltbench/docker/Dockerfile.mini-sglang-base \
  -t tmlrgroup/alphadiana:mini-sglang-base .

# Step 2: OpenClaw agent layer — Node.js + OpenClaw gateway (~3 min)
docker build --network host \
  -f moltbench/docker/Dockerfile.mini-sglang-openclaw \
  -t tmlrgroup/alphadiana:mini-sglang-openclaw .
```

### 5. Verify images

```bash
# Verify GPU access
docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=7 \
  tmlrgroup/alphadiana:mini-sglang-base \
  python -c "import torch; print(f'GPU: {torch.cuda.get_device_name(0)}'); import flashinfer; print('flashinfer OK')"
# Expected: GPU: NVIDIA A800-SXM4-80GB \n flashinfer OK

# Verify /eval/run_eval works (takes ~120s — server startup + benchmark)
docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=7 \
  -v /data0/models:/data0/models:ro --network host \
  --entrypoint bash tmlrgroup/alphadiana:mini-sglang-openclaw \
  -c '/eval/run_eval 2>&1 | tail -5'
# Expected: {"throughput_tok_per_s": ~800, "correct": true, ...}
# (Cold start throughput is lower than warm baseline)
```

---

## Execution

### Option A: Via AlphaDiana CLI (Recommended)

```bash
cd AlphaDiana
set -a && source .env && set +a

python3 -m alphadiana.cli run configs/examples/moltbench_minisglang_openclaw.yaml
```

This takes **~20-30 minutes**. The CLI:
1. Creates a Docker container on the specified GPU
2. Mounts task config and artifact directories
3. Waits for the container to complete
4. Collects results

### Option B: Direct Docker Run (Manual)

```bash
mkdir -p /tmp/task /tmp/artifacts
echo '{"task_id":"test","model_path":"/data0/models/modelscope/Qwen/Qwen3-0___6B","num_prompts":64,"input_len":128,"output_len":128,"max_rounds":3,"timeout_sec":3600,"baseline_throughput":5026.0}' > /tmp/task/task.json

docker run --rm --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=7 \
  --network host \
  --memory=64g \
  -v /tmp/task:/workspace/task:ro \
  -v /tmp/artifacts:/workspace/artifacts \
  -v /data0/models:/data0/models:ro \
  -e OPENAI_API_KEY=$OPENROUTER_API_KEY \
  -e OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
  -e OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507 \
  -e OPENCLAW_GATEWAY_TOKEN=MOLTBENCH \
  tmlrgroup/alphadiana:mini-sglang-openclaw

cat /tmp/artifacts/results.json
```

### Monitoring Progress

While the experiment is running:

```bash
# Check container status
docker ps --filter ancestor=tmlrgroup/alphadiana:mini-sglang-openclaw

# View container logs (real-time)
docker logs -f $(docker ps -q --filter ancestor=tmlrgroup/alphadiana:mini-sglang-openclaw)

# Check if agent has produced benchmark results
docker exec $(docker ps -q --filter ancestor=tmlrgroup/alphadiana:mini-sglang-openclaw) \
  cat /tmp/eval_result.json 2>/dev/null
```

---

## Evaluation

### Reading Results

```bash
# After experiment completes:
python3 -m json.tool mini_sglang_artifacts_openclaw_v1/mini_sglang_optimize_v1/results.json
```

Example output:
```json
{
    "baseline_throughput": 5026.0,
    "final_throughput": 17789.06,
    "og": 3.5394,
    "rounds_completed": 0,
    "rounds": [],
    "correct": true
}
```

### Interpreting Results

| Field | Meaning |
|-------|---------|
| `baseline_throughput` | Known baseline: 5,026 tok/s |
| `final_throughput` | Throughput measured by EXIT trap on agent's final code state |
| `og` | `final / baseline` — the optimization gain |
| `correct` | Whether all benchmark requests succeeded |
| `rounds_completed` | Number of successful `/eval/run_eval` calls saved to rounds/ |

### Examining Agent Behavior

```bash
# What code did the agent change?
cat mini_sglang_artifacts_openclaw_v1/mini_sglang_optimize_v1/agent_diff.patch | grep -v .pyc

# Agent's full reasoning output
python3 -c "
import json
d = json.load(open('mini_sglang_artifacts_openclaw_v1/mini_sglang_optimize_v1/agent_response.json'))
print(f'Time: {d[\"elapsed_sec\"]}s')
print(d['raw_output'][:3000])
"

# Container logs (shows timeline)
cat mini_sglang_artifacts_openclaw_v1/mini_sglang_optimize_v1/container.log
```

---

## Prompt & Configuration

### Agent Prompt (System Message)

The agent receives a system prompt that includes:

1. **Role**: "You are a systems optimization engineer"
2. **Task**: Optimize `/workspace/repo/python/minisgl/` to maximize throughput
3. **Actions**: Read/edit source, call `/eval/run_eval` via bash tool, git revert on failure
4. **Reference PRs**: 4 verified SGLang optimizations with descriptions:
   - mini-sglang#97: Better Estimation Policy (+15.71%, already implemented)
   - sglang#7563: Avoid cudaStreamSync (+4%, agent should try)
   - mini-sglang#110: page_size>1 (NOT compatible with flashinfer 0.6.3)
   - sglang#20501: Fused sampling (1-4x potential)
5. **Constraints**: No pip install, no manual server management, must revert on failure

Full prompt source: `moltbench/docker/mini_sglang/call_gateway.py:build_system_prompt()`

### YAML Configuration

```yaml
# configs/examples/moltbench_minisglang_openclaw.yaml
run_id: "mini-sglang-docker-openclaw-v1"

agent:
  name: mini_sglang_docker
  config:
    image: "moltbench/mini-sglang-openclaw:latest"
    gpu_id: "7"                    # GPU index (pick a free one)
    runtime: "nvidia"
    timeout_sec: 3600              # 60 min hard limit
    model_path: "/data0/models/modelscope/Qwen/Qwen3-0___6B"
    num_prompts: 64                # Benchmark concurrency
    input_len: 128                 # Input tokens per request
    output_len: 128                # Output tokens per request
    max_rounds: 3                  # Agent optimization rounds
    baseline_throughput: 5026.0    # Known baseline for OG calculation
    output_dir: "./mini_sglang_artifacts_openclaw_v1"
    env:
      OPENAI_MODEL_NAME: "qwen/qwen3-235b-a22b-2507"  # Agent LLM

benchmark:
  name: mini_sglang
  config:
    mode: docker

scorer:
  name: mini_sglang_eval
  config:
    baseline_throughput: 5026.0
```

### Key Configuration Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| `gpu_id` | "7" | Change to any free GPU on your machine |
| `timeout_sec` | 3600 | Container killed after 60 min |
| `max_rounds` | 3 | Agent gets 3 benchmark opportunities |
| `num_prompts` | 64 | Concurrent requests in benchmark |
| `input_len` / `output_len` | 128 | Token lengths per request |
| `baseline_throughput` | 5026.0 | From our Qwen3-0.6B measurement on A800 |
| `OPENAI_MODEL_NAME` | qwen/qwen3-235b-a22b-2507 | Agent LLM (via OpenRouter) |

---

## Verified Results

### Reproduction Run (2026-04-09, A800 GPU 7)

| Metric | Value |
|--------|-------|
| Baseline | 5,026 tok/s |
| Agent cold-start measurement | 669 tok/s |
| Agent best measurement | 15,657 tok/s |
| EXIT trap final (independent) | **17,789 tok/s** |
| **OG** | **3.54x** |
| Agent time | 1,301s (~22 min) |
| Agent actions | Explored synchronize() calls, tried removing copy_done.synchronize(), reverted on failure, explored fused sampling |

### Previous Run (2026-04-08, A800 GPU 6)

| Metric | Value |
|--------|-------|
| Final throughput | 18,716 tok/s |
| OG | 3.72x |
| Agent actions | Removed torch.cuda.synchronize() + fused sampling optimization |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `nvidia-smi` shows GPU in use | Pick a different GPU: `nvidia-smi --query-gpu=index,memory.used --format=csv` |
| `/eval/run_eval` verification hangs | Server startup takes ~120s — wait, or check `nvidia-smi` for GPU activity |
| Port 31001 EADDRINUSE | `pkill -f "minisgl.server.launch"; sleep 5` (stale server from previous run) |
| OOM Killed (container dies silently) | Ensure `--memory=64g` in YAML config |
| Agent says "I don't see /eval/" | Agent file tools can't access /eval/ — prompt tells it to use bash tool instead |
| `npm install` fails during build | Use pre-built OpenClaw from `ghcr.io/tsrigo/openclaw-reasoning:20260320` (already in Dockerfile) |
| Cold-start throughput ~670 vs baseline 5026 | Normal — first run has no CUDA graph cache or flashinfer JIT warmup |

---

## File Layout

```
AlphaDiana/
├── moltbench/
│   ├── seeds.yaml                              # Seed project registry
│   ├── docs/
│   │   ├── README.md                           # MoltBench overview
│   │   └── mini-sglang-guide.md                # This document
│   └── docker/
│       ├── Dockerfile.mini-sglang-base         # GPU base + mini-sglang + eval
│       ├── Dockerfile.mini-sglang-openclaw     # + Node.js + OpenClaw
│       ├── run_task.sh                         # Shared entrypoint (all tasks)
│       └── mini_sglang/
│           ├── run_eval.sh                     # Server restart + benchmark
│           ├── bench_serving.py                # Throughput measurement
│           └── call_gateway.py                 # OpenClaw gateway driver + prompt
├── alphadiana/
│   └── benchmark/moltbench.py                  # Task loader (task: mini_sglang)
└── configs/examples/
    └── moltbench_minisglang_openclaw.yaml      # Evaluation config
```
