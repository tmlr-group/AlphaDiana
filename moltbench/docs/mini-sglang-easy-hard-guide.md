# mini-SGLang Easy/Hard Task — Complete Reproduction Guide

This document walks you through reproducing the mini-SGLang Easy and Hard
optimization tasks from scratch on an A800 GPU server. No prior knowledge
of the codebase is required.

---

## What This Task Does

An AI agent (qwen3-235b, running inside a Docker container) autonomously
optimizes the [mini-SGLang](https://github.com/sgl-project/mini-sglang)
inference engine to maximize **Qwen3-0.6B throughput (tokens/second)**.

There are two difficulty levels:

| | Easy | Hard |
|--|------|------|
| **Prompt** | Agent receives detailed PR references with rationale, key files, and implementation steps | Agent only told to "optimize throughput" — must discover bottlenecks independently |
| **Expected behavior** | Implement PR#97 (Better Estimation Policy) for ~15% improvement | Explore source code, hypothesize, test, iterate |
| **Use case** | Measures agent's ability to follow technical guidance | Measures agent's autonomous optimization ability |

Both tasks use the same Docker image, evaluation scripts, and scoring.
The only difference is the system prompt content.

### How the Agent Works

Each "round" = one call to `/eval/run_eval`. The agent gets 10 rounds.

```
Agent receives task
  │
  ├── /eval/run_eval           → Get baseline throughput (~669 tok/s cold start)
  ├── grep/read source code    → Understand the codebase
  ├── edit source code         → Implement optimization
  ├── /eval/run_eval           → Verify: throughput improved? correct?
  │     ├��─ Yes → git commit, try next optimization
  │     └── No  → git checkout -- . (revert), try different approach
  └── Repeat for up to 10 rounds
```

### Metrics

| Metric | Definition |
|--------|-----------|
| **throughput_tok_per_s** | Total output tokens / wall time (64 concurrent requests, 128 in/out tokens) |
| **OG (optimization gain)** | `final_throughput / baseline_throughput` |
| **correct** | All 64 benchmark requests completed without error |
| **baseline_throughput** | ~4,813 tok/s (Qwen3-0.6B on A800, pre-PR#97) |

---

## Prerequisites

| Requirement | Details |
|-------------|---------|
| Server | NVIDIA A800 (or any GPU with >=40 GB VRAM) |
| Docker | 20.10+ with [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) |
| Base image | `jinbo/mini-sglang:clean` (pre-built with CUDA + PyTorch + flashinfer) |
| Model weights | Qwen3-0.6B at `/data0/models/modelscope/Qwen/Qwen3-0___6B` |
| LLM API | OpenRouter API key (for agent LLM: `qwen/qwen3-235b-a22b-2507`) |

---

## Step-by-Step Reproduction

### Step 1: Prepare the Build Context

The Docker image needs the mini-sglang source code at a specific commit
**before** PR#97 was merged, plus the evaluation scripts.

```bash
# SSH to the A800 server
ssh <user>@<gpu-host>

# The mini-sglang repo is at ~/mini-sglang with PR#97 as uncommitted changes.
# HEAD (20fcd7f) is the clean pre-PR#97 state.
cd ~/mini-sglang
git log --oneline -3
# Expected: 20fcd7f [Fix] Fix race condition in flashinfer backend (#103)

# Create a clean build context using git archive (excludes uncommitted PR#97 changes)
BUILD_DIR=~/mini-sglang-build
rm -rf $BUILD_DIR && mkdir -p $BUILD_DIR/_eval
git archive HEAD python/ pyproject.toml | tar -x -C $BUILD_DIR

# Verify no PR#97 code:
grep -c "_EstimatePolicy" $BUILD_DIR/python/minisgl/scheduler/decode.py
# Expected: 0
```

### Step 2: Copy Evaluation Scripts

The evaluation scripts come from the AlphaDiana repository. If you
don't have GitHub access from the server, download them from a machine
that does and scp them over.

```bash
# Option A: Clone AlphaDiana (if GitHub is accessible)
cd ~
git clone https://github.com/tmlr-group/AlphaDiana.git
cd AlphaDiana
git checkout feat/mini-sglang-easy

# Copy eval scripts to build context
cp moltbench/docker/mini_sglang/run_eval.sh   ~/mini-sglang-build/_eval/
cp moltbench/docker/mini_sglang/bench_serving.py ~/mini-sglang-build/_eval/
cp moltbench/docker/mini_sglang/call_gateway.py  ~/mini-sglang-build/_eval/
cp moltbench/docker/run_task.sh                   ~/mini-sglang-build/_eval/

# Option B: scp from your local machine (if GitHub not accessible from server)
# On your local machine:
cd AlphaDiana && git checkout feat/mini-sglang-easy
scp moltbench/docker/mini_sglang/{run_eval.sh,bench_serving.py,call_gateway.py} \
    <user>@<gpu-host>:~/mini-sglang-build/_eval/
scp moltbench/docker/run_task.sh <user>@<gpu-host>:~/mini-sglang-build/_eval/
```

### Step 3: Write the Dockerfile

```bash
cat > ~/mini-sglang-build/Dockerfile << 'DOCKERFILE'
# MoltBench mini-SGLang Base Image (pre-PR#97)
FROM jinbo/mini-sglang:clean

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PATH="/data3/jinbo/envs/mini_sglang/bin:${PATH}"

RUN apt-get update -qq && apt-get install -y --no-install-recommends \
      lsof psmisc iproute2 procps \
    && rm -rf /var/lib/apt/lists/*

# Copy mini-sglang source (pre-PR#97 clean state)
COPY python/     /workspace/repo/python/
COPY pyproject.toml /workspace/repo/

# Install mini-sglang in editable mode
RUN pip install msgpack accelerate httpx --quiet && \
    pip install -e /workspace/repo/ --quiet --no-deps

# Initialize git for change tracking
RUN cd /workspace/repo && git init && \
    git config user.email "moltbench@eval" && \
    git config user.name "MoltBench" && \
    git add -A && \
    git commit -m "baseline source (pre-PR#97, commit 20fcd7f)" --quiet

# Copy eval scripts (hidden from agent)
COPY _eval/run_eval.sh      /eval/run_eval
COPY _eval/bench_serving.py /eval/bench_serving.py
COPY _eval/call_gateway.py  /eval/call_gateway.py
RUN chmod +x /eval/run_eval

ENV MODEL_PATH=/data0/models/modelscope/Qwen/Qwen3-0___6B
ENV MINISGL_PORT=31000
ENV EVAL_NUM_PROMPTS=64
ENV EVAL_INPUT_LEN=128
ENV EVAL_OUTPUT_LEN=128

RUN mkdir -p /workspace/artifacts /workspace/task

COPY _eval/run_task.sh /workspace/run_task.sh
RUN chmod +x /workspace/run_task.sh

WORKDIR /workspace
ENTRYPOINT ["/workspace/run_task.sh"]
DOCKERFILE
```

### Step 4: Write the OpenClaw Dockerfile

```bash
cat > ~/openclaw-build/Dockerfile << 'DOCKERFILE'
FROM moltbench/mini-sglang-base:pre-pr97

RUN apt-get update && apt-get install -y --no-install-recommends \
      wget ca-certificates xz-utils && rm -rf /var/lib/apt/lists/*

ARG NODE_SHA256="c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7"
RUN wget -q -O /tmp/node.tar.xz \
      https://npmmirror.com/mirrors/node/v22.18.0/node-v22.18.0-linux-x64.tar.xz \
    && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum -c - \
    && tar -xf /tmp/node.tar.xz -C /opt \
    && mv /opt/node-v22.18.0-linux-x64 /opt/node \
    && rm /tmp/node.tar.xz
ENV PATH="/opt/node/bin:$PATH"

RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g openclaw@2026.3.7 --omit=optional \
    && npm cache clean --force

WORKDIR /workspace
ENTRYPOINT ["/workspace/run_task.sh"]
DOCKERFILE
mkdir -p ~/openclaw-build
```

### Step 5: Build Docker Images

```bash
# Step 1: Base image (~2-5 min, uses cached layers from jinbo/mini-sglang:clean)
cd ~/mini-sglang-build
docker build --network host -t moltbench/mini-sglang-base:pre-pr97 .

# Step 2: OpenClaw layer (~3 min)
cd ~/openclaw-build
docker build --network host -t moltbench/mini-sglang-openclaw:pre-pr97 .

# Verify
docker images | grep pre-pr97
# Expected:
# moltbench/mini-sglang-openclaw   pre-pr97   ...   ~43GB
# moltbench/mini-sglang-base       pre-pr97   ...   ~42GB
```

### Step 6: Verify the Image

```bash
# Verify GPU access
docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=7 \
  --entrypoint python moltbench/mini-sglang-base:pre-pr97 \
  -c "import torch; print(f'GPU: {torch.cuda.get_device_name(0)}'); \
      import flashinfer; print('flashinfer OK')"
# Expected: GPU: NVIDIA A800-SXM4-80GB \n flashinfer OK

# Verify pre-PR#97 (no _EstimatePolicy class)
docker run --rm --entrypoint grep moltbench/mini-sglang-base:pre-pr97 \
  -c "_EstimatePolicy" /workspace/repo/python/minisgl/scheduler/decode.py
# Expected: 0
```

### Step 7: Configure API Key

```bash
# Set your OpenRouter API key
export OPENROUTER_API_KEY="sk-or-v1-your-key-here"

# Or create a .env file:
echo 'OPENROUTER_API_KEY="sk-or-v1-your-key-here"' > ~/.env
source ~/.env
```

### Step 8: Run the Easy Task

```bash
# Create task directory
mkdir -p /tmp/easy_task /tmp/easy_artifacts

# Write task.json
cat > /tmp/easy_task/task.json << 'EOF'
{
  "task_id": "mini_sglang_easy_v1",
  "benchmark_id": "mini_sglang",
  "difficulty": "easy",
  "model_path": "/data0/models/modelscope/Qwen/Qwen3-0___6B",
  "num_prompts": 64,
  "input_len": 128,
  "output_len": 128,
  "max_rounds": 10,
  "timeout_sec": 3600,
  "baseline_throughput": 4813.0
}
EOF

# Kill any stale server processes
pkill -f "minisgl.server.launch" 2>/dev/null; sleep 2

# Run the Easy task (~20-30 minutes)
docker run --rm --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=7 \
  -e MOLTBENCH_AGENT=openclaw \
  --network host \
  --memory=64g \
  -v /tmp/easy_task:/workspace/task:ro \
  -v /tmp/easy_artifacts:/workspace/artifacts \
  -v /data0/models:/data0/models:ro \
  -e OPENAI_API_KEY=$OPENROUTER_API_KEY \
  -e OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
  -e OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507 \
  -e OPENCLAW_GATEWAY_TOKEN=MOLTBENCH \
  moltbench/mini-sglang-openclaw:pre-pr97
```

### Step 9: Run the Hard Task

```bash
# Create task directory
mkdir -p /tmp/hard_task /tmp/hard_artifacts

# Write task.json (only difference: difficulty=hard)
cat > /tmp/hard_task/task.json << 'EOF'
{
  "task_id": "mini_sglang_hard_v1",
  "benchmark_id": "mini_sglang",
  "difficulty": "hard",
  "model_path": "/data0/models/modelscope/Qwen/Qwen3-0___6B",
  "num_prompts": 64,
  "input_len": 128,
  "output_len": 128,
  "max_rounds": 10,
  "timeout_sec": 3600,
  "baseline_throughput": 4813.0
}
EOF

# Kill stale processes + run
pkill -f "minisgl.server.launch" 2>/dev/null; sleep 2

docker run --rm --runtime=nvidia \
  -e NVIDIA_VISIBLE_DEVICES=7 \
  -e MOLTBENCH_AGENT=openclaw \
  --network host \
  --memory=64g \
  -v /tmp/hard_task:/workspace/task:ro \
  -v /tmp/hard_artifacts:/workspace/artifacts \
  -v /data0/models:/data0/models:ro \
  -e OPENAI_API_KEY=$OPENROUTER_API_KEY \
  -e OPENAI_BASE_URL=https://openrouter.ai/api/v1 \
  -e OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507 \
  -e OPENCLAW_GATEWAY_TOKEN=MOLTBENCH \
  moltbench/mini-sglang-openclaw:pre-pr97
```

### Step 10: Read Results

```bash
# Easy results
python3 -m json.tool /tmp/easy_artifacts/results.json

# Hard results
python3 -m json.tool /tmp/hard_artifacts/results.json

# Example output:
# {
#   "baseline_throughput": 4813.0,
#   "final_throughput": 17789.06,
#   "og": 3.69,
#   "correct": true
# }

# Check what the agent changed
cat /tmp/easy_artifacts/agent_diff.patch 2>/dev/null | head -50

# Agent's reasoning
python3 -c "
import json
d = json.load(open('/tmp/easy_artifacts/agent_response.json'))
print(f'Time: {d[\"elapsed_sec\"]}s, Difficulty: {d.get(\"difficulty\",\"?\")}')
print(d['raw_output'][:2000])
" 2>/dev/null
```

---

## Monitoring While Running

```bash
# Check container status
docker ps --filter "ancestor=moltbench/mini-sglang-openclaw:pre-pr97"

# View logs in real time
docker logs -f $(docker ps -q --filter "ancestor=moltbench/mini-sglang-openclaw:pre-pr97")

# Check GPU utilization
nvidia-smi -i 7

# Check latest benchmark result inside container
docker exec $(docker ps -q --filter "ancestor=moltbench/mini-sglang-openclaw:pre-pr97") \
  cat /tmp/eval_result.json 2>/dev/null
```

---

## Understanding the Results

### Key Fields in results.json

| Field | Meaning |
|-------|---------|
| `baseline_throughput` | Pre-PR#97 baseline: ~4,813 tok/s |
| `final_throughput` | Throughput measured by EXIT trap on agent's final code state |
| `og` | `final / baseline` — the optimization gain |
| `correct` | Whether all benchmark requests succeeded |
| `rounds_completed` | Number of `/eval/run_eval` calls the agent made |

### Expected Results

| Metric | Easy (expected) | Hard (expected) |
|--------|-----------------|-----------------|
| Agent behavior | Follows PR#97 guidance, implements estimation policy | Explores code, tries various optimizations |
| Code changes | env.py + scheduler/decode.py + prefill.py | Varies — may try synchronize removal, sampling fusion, etc. |
| OG range | 1.0-3.5x (depends on cold/warm start) | 1.0-3.5x (similar range, different path) |

### Cold Start vs Warm Start

The first `/eval/run_eval` call shows ~669 tok/s (cold start) due to:
- CUDA graph capture for the first time
- flashinfer JIT kernel compilation
- Model weight loading

Subsequent calls show ~14,000-18,000 tok/s (warm). This is normal and not
caused by the agent's optimizations.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `nvidia-smi` shows GPU in use | Pick a different GPU: `nvidia-smi --query-gpu=index,memory.used --format=csv` |
| GitHub not accessible from server | Use `git archive` from local mini-sglang repo (Step 1) or scp files (Step 2 Option B) |
| Container exits immediately | Check `docker logs` — likely missing API key or wrong MOLTBENCH_AGENT |
| Port 31000 EADDRINUSE | `pkill -f "minisgl.server.launch"; sleep 5` |
| Agent says "I don't see /eval/" | Correct — agent must use `bash tool` to call `/eval/run_eval` |
| OOM Killed | Ensure `--memory=64g` in docker run |
| Cold start ~670 vs warm ~14k tok/s | Normal — CUDA graph + flashinfer JIT not cached on first run |
| `jinbo/mini-sglang:clean` not found | Ask the team for the pre-built base image |

---

## File Layout

```
mini-sglang-build/          ← Docker build context
├── Dockerfile              ← Base image definition
├── pyproject.toml          ← mini-sglang package config
├── python/                 ← mini-sglang source (pre-PR#97, commit 20fcd7f)
│   └── minisgl/
│       ├── env.py          ← No SCHEDULE_CONSERVATIVENESS (agent adds it in Easy)
│       ├── scheduler/
│       │   ├── decode.py   ← No _EstimatePolicy (agent adds it in Easy)
│       │   └── prefill.py  ← No page-aligned calc (agent adds it in Easy)
│       ├── engine/
│       └── attention/
└── _eval/
    ├── run_eval.sh         ← Agent's benchmark tool
    ├── bench_serving.py    ← 64-concurrent throughput measurement
    ├── call_gateway.py     ← Prompt builder (--difficulty easy/hard)
    └── run_task.sh         ← Container entrypoint

openclaw-build/             ← OpenClaw layer build context
└── Dockerfile              ← Adds Node.js + openclaw@2026.3.7
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Pre-PR#97 baseline | Gives agent real optimization room (~15% throughput gain) |
| 10 rounds (not 3) | Each `/eval/run_eval` = 1 round; agent needs rounds for exploration + testing |
| Easy/Hard same image | Only the system prompt differs; reproducible comparison |
| `--difficulty` flag | `call_gateway.py` selects prompt variant; no Docker rebuild needed |
| Commit 20fcd7f | Last commit before PR#97 changes; verified clean state |
| baseline_throughput=4813 | Measured on A800 with 64 concurrent, 128 in/out tokens, pre-PR#97 |
