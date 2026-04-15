# Three Benchmarks Pipeline Guide

This document describes how IMO-AnswerBench, HLE, and terminal-bench-2 were integrated into AlphaDiana and validated end-to-end on A800.

## Benchmarks Overview

| Benchmark | Source | Agent | Scorer | Task Type |
|-----------|--------|-------|--------|-----------|
| IMO-AnswerBench | HuggingFace `Hwilner/imo-answerbench` | OpenClaw | math_verify | Math competition |
| HLE | HuggingFace `cais/hle` (gated) | OpenClaw | exact_match | Cross-discipline QA |
| terminal-bench-2 | Local clone of `harbor-framework/terminal-bench-2` | TerminalBench2DockerAgent | terminal_bench2 (binary) | Docker shell tasks |

## Prerequisites

### Environment Variables

```bash
# OpenRouter API (for LLM access)
export OPENROUTER_API_KEY=sk-or-v1-...
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export OPENAI_API_KEY=$OPENROUTER_API_KEY
export OPENAI_MODEL_NAME=qwen/qwen3-235b-a22b-2507

# HuggingFace (for HLE gated dataset)
export HF_TOKEN=hf_...
export HF_ENDPOINT=https://hf-mirror.com  # China mirror

# ROCK/OpenClaw (for IMO-AnswerBench and HLE)
export ROCK_PROXY_PORT=9045
export SANDBOX_ID=<from openclaw deploy>
```

### OpenClaw Deployment (for IMO-AnswerBench and HLE)

```bash
cd /path/to/AlphaDiana-dev
source scripts/rock_env.sh
python openclaw_deploy/deploy.py \
  --agent-config openclaw_deploy/rock_agent_config.prebuilt.yaml \
  --image ghcr.io/tsrigo/openclaw-reasoning:20260320
# Note the Sandbox ID from the output
```

### terminal-bench-2 Setup

```bash
# Clone the repo
git clone --depth=1 https://github.com/harbor-framework/terminal-bench-2.git /path/to/terminal-bench-2
export TERMINAL_BENCH2_DIR=/path/to/terminal-bench-2

# Pull Docker images (use mirror if Docker Hub is blocked)
for task in db-wal-recovery extract-elf extract-moves-from-video; do
  docker pull hub.rat.dev/alexgshaw/${task}:20251031
  docker tag hub.rat.dev/alexgshaw/${task}:20251031 alexgshaw/${task}:20251031
done
```

## Running the Pipelines

### IMO-AnswerBench

```bash
alphadiana run configs/examples/openclaw_imo_answerbench.yaml \
  -o benchmark.config.max_tasks=5
```

### HLE

```bash
alphadiana run configs/examples/openclaw_hle.yaml \
  -o benchmark.config.max_tasks=5
```

### terminal-bench-2

```bash
alphadiana run configs/examples/terminal_bench2.yaml \
  -o benchmark.config.max_tasks=3
```

## Validation Results (A800, Qwen3-235B)

| Benchmark | Tasks | Correct | Accuracy | Tool Calls |
|-----------|-------|---------|----------|------------|
| IMO-AnswerBench | 2 | 2 | 1.0000 | 6-10 rounds (exec Python, write files) |
| HLE | 2 | 0 | 0.0000 | 0 (pure reasoning, HLE is extremely hard) |
| terminal-bench-2 | 1 | 0 | 0.0000 | 33 bash commands via docker exec |

All three pipelines complete successfully. Scores reflect model capability, not pipeline issues.

## Architecture

### IMO-AnswerBench and HLE (OpenClaw Agent)

```
AlphaDiana Runner
  -> POST /chat/completions to OpenClaw gateway (ROCK proxy)
  -> OpenClaw internal agentic loop:
       LLM call -> tool_call(exec/write) -> execute in sandbox -> result -> LLM -> ...
  -> Final answer with \boxed{} format
  -> math_verify / exact_match scorer
```

OpenClaw with Qwen3-235B actively uses tools: writes Python scripts, executes them to verify mathematical reasoning, iterates until confident.

### terminal-bench-2 (Docker Exec Relay Agent)

```
AlphaDiana Runner
  -> docker run -d --rm -v logs:/logs -v tests:/tests:ro {task_image} sleep infinity
  -> Multi-turn LLM loop:
       POST to OpenRouter API (Qwen3-235B)
       Parse "$ cmd" lines from response
       docker exec {container} bash -c "{cmd}"
       Feed output back to LLM
       Repeat until DONE
  -> docker exec bash /tests/test.sh
  -> Read /logs/verifier/reward.txt
  -> Binary scorer: "1" = pass, anything else = fail
```

Note: OpenClaw cannot be used directly for terminal-bench-2 because it runs inside a ROCK sandbox with no access to the host Docker daemon. The TerminalBench2DockerAgent bridges this by running on the host and relaying commands via docker exec.

## Key Bug Fixes During Validation

1. **Commands skipped before DONE**: LLMs output all commands + DONE in one response. Fixed by executing commands first, then checking DONE.
2. **Missing tests/ mount**: test.sh lives in the task repo, not the Docker image. Fixed by mounting `task_dir/tests/` as `/tests:ro`.
3. **Missing /logs/verifier/**: test.sh writes reward.txt there but the directory didn't exist. Fixed by `mkdir -p` after container start.
4. **test_timeout_sec too short**: test.sh installs apt packages + uv + pytest from scratch (~8 min). Increased from 120s to 900s.
5. **YAML env var syntax**: `${VAR:-default}` not supported by `os.path.expandvars()`. Changed to `${VAR}`.

## Files Added

```
alphadiana/benchmark/imo_answerbench.py      # IMO-AnswerBench loader (HuggingFace)
alphadiana/benchmark/hle.py                  # HLE loader (HuggingFace, gated)
alphadiana/benchmark/terminal_bench2.py      # terminal-bench-2 loader (local dir + tomli)
alphadiana/agent/terminal_bench2_docker.py   # Docker exec relay agent
alphadiana/scorer/terminal_bench2_scorer.py  # Binary pass/fail scorer
configs/examples/openclaw_imo_answerbench.yaml
configs/examples/openclaw_hle.yaml
configs/examples/terminal_bench2.yaml
```

## Files Modified

```
alphadiana/runner/runner.py   # Added import registrations
alphadiana/cli.py             # Added import registrations
pyproject.toml                # Added tomli>=2.0 to benchmarks deps
```
