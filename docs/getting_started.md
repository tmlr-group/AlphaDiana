# AlphaDiana Complete Getting Started Tutorial

AlphaDiana is an evaluation framework for Foundation Model and Agent systems, supporting the evaluation of different Agents on multiple Benchmarks (AIME, MATH, HLE, etc.).

This tutorial takes **Qwen3-8B + AIME 2024** as an example to start from scratch and build and run the evaluation step by step.

## Directory

- [0. Architecture Overview](#0-Architecture Overview)
- [1. Environment preparation](#1-Environment preparation)
- [2. Install AlphaDiana](#2-Install-alphadiana)
- [3. Quick verification (Direct LLM baseline)] (#3-Quick verification direct-llm-baseline)
- [4. Build a ROCK sandbox environment] (#4-Build-rock-sandbox environment)
- [5. Deploy OpenClaw Agent](#5-deployment-openclaw-agent)
- [6. Run OpenClaw-evaluation](#6-run-openclaw-evaluation)
- [7. View and analyze results](#7-View and analyze results)
- [8. Custom configuration](#8-Custom configuration)
- [Appendix A: Project Structure](#Appendix-aProject Structure)
- [Appendix B: Frequently Asked Questions](#Appendix-bFrequently Asked Questions)

---

## 0. Architecture Overview

AlphaDiana has two evaluation modes:

### Mode 1: Direct LLM (direct LLM adjustment, simple and fast)


```
AlphaDiana Runner
       │  Direct calls to OpenAI-compatible APIs
       ▼
  vLLM/any OpenAI compatible API
```


- Just need a running LLM service
- The test is **bare model ability** (single round of question and answer, without tool invocation)
- Perfect for quick baseline testing

### Mode 2: OpenClaw (Agent system, complete evaluation)


```
AlphaDiana Runner
       │
       ▼
  ROCK Proxy (:9001)
       │  Forward request to sandbox
       ▼
  Rock Sandbox (Docker Container)
       │
       ▼
  OpenClaw Gateway (:8080)
       │  Internal Multi-Wheel Agent Cycle:
       │  thinking → tool_call → execute → reasoning → ...
       ▼
  vLLM (:8000)
```


- Need to build ROCK sandbox + deploy OpenClaw
- What is measured is the **Agent system capability** (multiple rounds of reasoning, tool invocation, code execution, etc.)
- OpenClaw automatically builds system prompt internally (~8K tokens), including 25 tool definitions

**It is recommended to run mode one first to confirm that there is no problem with the environment, and then run mode two. **

---

## 1. Environment preparation

### 1.1 Hardware requirements

| Components | Minimum Requirements |
|------|---------|
| GPU | 1 40GB+ VRAM (A100/A800) for vLLM |
| Memory | 32GB+ |
| Disk | 50GB+ free |
| Docker | Installed, current user is in docker group |

### 1.2 Create Conda environment


```bash
conda create -n alphadiana python=3.11 -y
conda activate alphadiana
```


> Python version must be >=3.10, 3.11 is recommended. Do not use 3.13 (ROCK depends on gem-llm only supports <=3.12).

### 1.3 Install vLLM


```bash
pip install vllm
```


### 1.4 Prepare the model

Take Qwen3-8B as an example. If the model has been downloaded locally, note the path; if not:


```bash
# Option 1: Download with huggingface-cli
pip install huggingface_hub
huggingface-cli download Qwen/Qwen3-8B --local-dir /your/path/Qwen3-8B

# Option 2: If it already exists on the server, use the path directly
# For example:/hd1/models/models--Qwen--Qwen3-8B/snapshots/b968826d...
```


### 1.5 Start vLLM

Choose a free GPU:


```bash
# View GPU Usage
nvidia-smi

# Start vLLM (replace GPU number and model path)
CUDA_VISIBLE_DEVICES=0 python -m vllm.entrypoints.openai.api_server \
    --model /path/to/Qwen3-8B \
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --max-model-len 32768 &
```


> `--enable-auto-tool-choice --tool-call-parser hermes` is for OpenClaw. If you only run the Direct LLM baseline, you don’t need to add it.
>
> `--max-model-len 32768` is important: OpenClaw's internal system prompt has about 8K tokens, and 16K will overflow.

Verification:


```bash
curl http://localhost:8000/v1/models
# Model list should be returned
```


---

## 2. Install AlphaDiana


```bash
git clone <repo_url> AlphaDiana-dev
cd AlphaDiana-dev

# Installation (with all optional dependencies)
pip install -e ".[all,dev]"
```


Verify installation:


```bash
alphadiana list-benchmarks
```


Output:


```
Registered benchmarks:
  - aime
  - math
  - hle
  - frontier_math
  - terminal_bench
  - osworld
```


---

## 3. Quick verification (Direct LLM baseline)

This is the simplest mode, which does not require ROCK and OpenClaw, and only requires vLLM to be running. Direct LLM is a pure LLM baseline Agent that directly calls the LLM API in a single round without tool calls and multi-round inference.

### 3.1 Write configuration file

Create `configs/my_first_run.yaml`:


```yaml
run_id: "direct-llm-qwen3-8b-aime2024"

agent:
  name: direct_llm
  version: "1.0"
  config:
    model: "/path/to/Qwen3-8B" # ← Change to your model path
    api_base: "http://localhost:8000/v1" # <- vLLM address
    api_key: "EMPTY"
    temperature: 0.0
    max_tokens: 4096

benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

scorer:
  name: numeric
  config:
    tolerance: 1e-6

max_concurrent: 1
output_dir: "./results"
```


> **Note:** The `model` field must be **exactly consistent** with the `--model` parameter when vLLM is started (including the full path).

### 3.2 Verify configuration


```bash
alphadiana validate configs/my_first_run.yaml
# Output: Config is valid.
```


### 3.3 Run Evaluation


```bash
alphadiana run configs/my_first_run.yaml
```


This will:
1. Load the AIME 2024 dataset from HuggingFace (30 questions)
2. Send the questions to vLLM one by one to get answers.
3. Score with NumericScorer
4. Write the result to `results/direct-llm-qwen3-8b-aime2024.jsonl`

Output example:


```
Run completed: direct-llm-qwen3-8b-aime2024
  Accuracy:   0.1333
  Mean Score: 0.1333
  Tasks:      30/30 completed
```


### 3.4 Run with Python API (optional)

If you want more flexible control, you can use Python:


```python
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner

config = ExperimentConfig.from_yaml("configs/my_first_run.yaml")
runner = Runner(config)

runner.setup()
try:
    summary = runner.run()
    print(f"Accuracy: {summary.accuracy:.4f}")
finally:
    runner.teardown()
```


**At this point, if the Direct LLM baseline can run through, it means there is no problem with the AlphaDiana + vLLM environment. ** Build the Agent system below.

---

## 4. Build a ROCK sandbox environment

ROCK is Alibaba's open source sandbox system, which uses Docker containers to run Agents in isolation. OpenClaw needs to be deployed in the ROCK sandbox.

### 4.1 Install ROCK


```bash
cd /path/to/your/workspace
git clone https://github.com/alibaba/ROCK.git
cd ROCK
pip install -e .
pip install gem-llm # rock Internal dependency with gem module
```


### 4.2 Start Redis Stack

ROCK relies on Redis Stack (requires JSON.SET command, ordinary Redis does not work):


```bash
# If you need sg docker to get docker group permissions:
sg docker -c 'docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest'

# Or directly (if docker permissions are configured):
docker run -d --name redis-stack -p 6379:6379 redis/redis-stack-server:latest
```


Verification:


```bash
redis-cli ping
# should return pong
```


### 4.3 Configure ROCK

Create configuration file:


```bash
mkdir -p ~/.rock
cat > ~/.rock/config.ini << 'EOF'
[ray]
address = auto
EOF
```


Create the `.venv` soft link (ROCK’s `LocalRuntimeEnv` needs it to mount the Python environment into the container):


```bash
cd /path/to/ROCK
ln -sf $(python -c "import sys; print(sys.prefix)") .venv
```


### 4.4 Start Ray + ROCK service```bash
# Set rock project root (important, Ray worker needs this)
export ROCK_PROJECT_ROOT=/path/to/ROCK

# Start Ray
ray start --head --port=6380

# Start rock admin (port 9000, handle writes)
sg docker -c 'python -m rock.deployments.admin &'

# Start rock proxy (port 9001, handling read/runtime operations)
sg docker -c 'python -m rock.deployments.proxy &'
```


> If `sg docker` is not needed (you already have docker permissions), remove `sg docker -c '...'` and run it directly.

Verification:


```bash
curl http://localhost: 9000/# should return {"message": "hello, rock!"}
curl http://localhost: 9001/# Ditto
```


---

## 5. Deploy OpenClaw Agent

OpenClaw is an Agent framework that is installed into the ROCK sandbox via npm. After it receives user questions, it automatically performs multiple rounds of reasoning and tool invocation.

### 5.1 Configuring OpenClaw

Edit `openclaw_deploy/rock_agent_config.yaml`:


```yaml
working_dir: "."

run_cmd: >
  NPM_BIN_DIR=$(find /tmp/rock-runtime-envs/node -name 'npm' -type f ! -path '*/nodewin/*' ! -path '*/shims/*' 2>/dev/null | head -1 | xargs -r dirname) &&
  export PATH=${NPM_BIN_DIR:+${NPM_BIN_DIR}:}$PATH
  OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json
  OPENCLAW_HOME=/tmp/oc_home
  OPENCLAW_BUNDLED_PLUGINS_DIR=/tmp/empty-bundled
  mkdir -p /tmp/empty-bundled /tmp/oc_home &&
  nohup openclaw gateway >> /tmp/gateway.log 2>&1 &

runtime_env_config:
  type: node
  npm_registry: https://registry.npmmirror.com
  custom_install_cmd: if command -v openclaw >/dev/null 2>&1; then echo "Using preinstalled OpenClaw from sandbox image"; else git config --global url.'https://github.com/'.insteadOf 'ssh://git@github.com/' && npm install -g openclaw@2026.3.7 --registry https://registry.npmmirror.com; fi
  install_timeout: 1200

env:
  OPENAI_BASE_URL: "http://<YOUR_HOST_IP>:8000/v1" # vLLM address inside the container
  OPENAI_API_KEY: "EMPTY"
  OPENAI_MODEL_NAME: "/path/to/Qwen3-8B" # must match vLLM's --model exactly
  OPENCLAW_GATEWAY_TOKEN: "OPENCLAW"
```


> **Important:** `OPENAI_BASE_URL` cannot write `localhost` because this is run inside a Docker container. Use the actual IP of the host (see `hostname -I | awk '{print $1}'`).
> The default sandbox image is `tmlrgroup/alphadiana:v1`. The OpenClaw model itself is still configurable via `OPENAI_MODEL_NAME`.

### 5.2 Deployment


```bash
cd AlphaDiana-dev/openclaw_deploy
python deploy.py
```


Wait 3-5 minutes (npm installation + start gateway), output:


```
Creating sandbox...
Sandbox ID: 90d4a0c01ad1497baa4f4db4b72f3923
Installing OpenClaw agent...
Running OpenClaw gateway...

OpenClaw deployed successfully!
Sandbox ID: 90d4a0c01ad1497baa4f4db4b72f3923
API base: http://127.0.0.1:9001/apis/envs/sandbox/v1/sandboxes/90d4a0c01ad1497baa4f4db4b72f3923/proxy/v1
```


**Write down the Sandbox ID** for subsequent configuration.

### 5.3 Verification OpenClaw


```bash
Sandbox_ID = ""

curl -s -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/${SANDBOX_ID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3?"}],"max_tokens":512}'
```


A JSON response containing the answer "5" should be returned.

---

## 6. Run the OpenClaw evaluation

### 6.1 Writing configuration files

Create `configs/openclaw_aime2024.yaml`:


```yaml
run_id: "openclaw-qwen3-8b-aime2024"

agent:
  name: openclaw
  version: "latest"
  config:
    api_base: "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    temperature: 0.0
    max_tokens: 8192

benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

sandbox: null # OpenClaw is already running in rock, no extra sandbox required

scorer:
  name: numeric
  config:
    tolerance: 1e-6

max_concurrent: 1
output_dir: "./results"
```


> Replace `<SANDBOX_ID>` with the Sandbox ID obtained in step 5.

### 6.2 Run


```bash
alphadiana run configs/openclaw_aime2024.yaml
```


Each question takes approximately 30-60 seconds (multiple rounds of reasoning within the Agent), and 30 questions are expected to take 15-30 minutes.

### 6.3 Or try a single question first


```python
from alphadiana.benchmark.aime import AIMEBenchmark
from alphadiana.agent.openclaw import OpenClawAgent
from alphadiana.scorer.numeric import NumericScorer

Is loading data
benchmark = AIMEBenchmark()
tasks = benchmark.load_tasks({"dataset": "HuggingFaceH4/aime_2024", "split": "train"})
task = tasks[0]

# Initialize Agent
agent = OpenClawAgent()
agent.setup({
    "version": "latest",
    "api_base": "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1",
    "model": "openclaw",
    "gateway_token": "OPENCLAW",
    "max_tokens": 8192,
})

Solve
resp = agent.solve(task)
print(f"Answer: {resp.answer}")
print(f"Wall time: {resp.wall_time_sec:.1f}s")

# View agentic trace
for step in resp.trajectory:
    role = step.get("role", "?")
    thinking = step.get("thinking", "")
    tool_calls = step.get("tool_calls", [])
    content = step.get("content", "")[:100]

    if thinking:
        print(f"  [{role}] THINKING: {thinking[:100]}...")
    for tc in tool_calls:
        print(f"  [{role}] TOOL: {tc['tool']}({list(tc['input'].keys())})")
    if content:
        print(f"  [{role}] {content}")

Review
scorer = NumericScorer()
scorer.setup({"tolerance": 1e-6})
score = scorer.score(task, resp)
print(f"Correct: {score.correct}, Score: {score.score}")
```


---

## 7. View and analyze results

### 7.1 Result file

The evaluation results are saved in `results/<run_id>.jsonl`, with one JSON record per line:


```bash
# See summary of results
alphadiana report ./results
```


### 7.2 Question-by-question analysis


```python
import json

with open("results/openclaw-qwen3-8b-aime2024.jsonl") as f:
    results = [json.loads(line) for line in f]

correct = sum(1 for r in results if r["correct"])
print(f"Accuracy: {correct}/{len(results)} = {correct/len(results):.2%}")

print(f"\n{'Task':<15} {'GT':>5} {'Pred':>8} {'OK':>4} {'Time':>6}")
print("-" * 45)
for r in results:
    mark = "Y" if r["correct"] else "N"
    print(f"{r['task_id']:<15} {r['ground_truth']:>5} {str(r['predicted']):>8} {mark:>4} {r['wall_time_sec']:>5.0f}s")
```


### 7.3 View the Agent’s reasoning process

The `trajectory` field of each result contains the complete agentic trace:


```python
r = results [0] # Question 1

for step in r["trajectory"]:
    role = step.get("role", "?")

    # thinking: Agent's internal thinking
    if step.get("thinking"):
        print(f"[{role}] THINKING: {step['thinking'][:200]}...")

    # tool_calls: What tool was called by the Agent
    for tc in step.get("tool_calls", []):
        print(f"[{role}] TOOL CALL: {tc['tool']}({json.dumps(tc['input'])[:100]})")

    # tool_results: Tool execution results
    for tr in step.get("tool_results", []):
        print(f"[{role}] TOOL RESULT: {str(tr['content'])[:100]}")

    # content: The text output of the Agent
    if step.get("content"):
        print(f"[{role}] OUTPUT: {step['content'][:200]}")
```


---

## 8. Custom configuration

### 8.1 Configuration file field description


```yaml
run_id: string # Run ID (leave blank to auto-generate UUID)

agent:
  name: string           # "direct_llm" | "openclaw"
  version: string # Version number (e.g. "latest", "v1.0")
  config: {} # Agent-specific configuration (see below)

benchmark:
  name: string           # "aime" | "math" | "hle" | "frontier_math" | "terminal_bench" | "osworld"
  config: {} # Benchmark specific configuration (see below)

sandbox: null | {} # Sandbox configuration (required for terminal_bench and osworld, optional)
  name: string           # "local" | "rock" | "boxlite"
  config: {}

scorer:
  name: string           # "numeric" | "exact_match" | "llm_judge"
  config: {}

max_concurrent: int # number of parallel tasks (default 1)
output_dir: string # Output directory (default "./results")
metadata: {} # Custom Metadata
```


### 8.2 Agent configuration

**Direct LLM (directly adjust LLM baseline):**


```yaml
agent:
  name: direct_llm
  version: "1.0"
  config:
    model: "Model name"
    api_base: "http://localhost:8000/v1"
    api_key: "EMPTY"
    temperature: 0.0
    max_tokens: 4096
    # system_prompt: "Custom system prompt" # Optional
```


**OpenClaw (Agent system):**


```yaml
agent:
  name: openclaw
  version: "latest"
  config:
    api_base: "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<ID>/proxy/v1"
    model: openclaw
    gateway_token: "OPENCLAW"
    temperature: 0.0
    max_tokens: 8192
    # Note: OpenClaw has its own system prompt and does not require manual setup
```


### 8.3 Benchmark configuration


```yaml
# AIME 2024
benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

# MATH
benchmark:
  name: math
  config:
    dataset: "hendrycks/competition_math"
    split: "test"
    # subset: "algebra" # Optional, only run subsets

# HLE (Humanity's Last Exam)
benchmark:
  name: hle
  config:
    split: "test"
```


### 8.4 Scorer configuration


```yaml
# Numeric Comparison (for AIME)
scorer:
  name: numeric
  config:
    tolerance: 1e-6

# Exact Match (for math)
scorer:
  name: exact_match
  config: {}

# LLM judgment (for HLE)
scorer:
  name: llm_judge
  config:
    judge_model: "gpt-4o"
    api_base: "https://api.openai.com/v1"
    api_key: "sk-..."
```


### 8.5 Direct LLM vs OpenClaw comparison

| | Direct LLM | OpenClaw |
|---|---------|----------|
| Evaluation objects | Naked model capabilities | Agent system capabilities |
| Reasoning method | Single round Q&A | Multi-round reasoning + tool calling |
| ROCK REQUIRED | NO | YES |
| Requires Docker | No | Yes |
|Each question takes 5-20 seconds | 30-120 seconds |
| Construction Difficulty | Low | Medium |
| Trajectory | Simple (system/user/assistant) | Rich (thinking/tool_call/tool_result) |

---

## Appendix A: Project Structure


```
AlphaDiana-dev/
├── alphadiana/# Core Code
│   ├── cli.py # CLI entry (alphadiana run/validate/report)
│   ├── agent/# Agent implementation
│   │   ├── base.py # Agent Base Class + AgentResponse
│   │   ├── registry.py # Agent Registry
│   │   ├── direct_llm.py # Direct LLM: Direct LLM Baseline
│   │   └── openclaw.py # OpenClaw: Agent System (Multiple Reasoning + Tool Calls)
│   ├── benchmark/# Benchmark dataset
│   │   ├── base.py # Benchmark Base Class + BenchmarkTask
│   │   ├── registry.py # Benchmark Registry
│   │   ├── aime.py # AIME Competition Math
│   │   ├── math_bench.py          #   Hendrycks MATH
│   │   ├── hle.py                 #   Humanity's Last Exam
│   │   ├── frontier_math.py       #   FrontierMath
│   │   ├── terminal_bench.py # Terminal operation (sandbox required)
│   │   └── osworld.py # OS Interaction (requires sandbox)
│   ├── scorer/# Scorer
│   │   ├── base.py # Scorer Base Class + ScoreResult
│   │   ├── numeric.py # Numeric Comparison
│   │   ├── exact_match.py # Exact Match
│   │   └── llm_judge.py # LLM Judge
│   ├── runner/# Run Engine
│   │   ├── runner.py # Runner: Main orchestrator
│   │   └── task_dispatcher.py # TaskDispatcher: Parallel Scheduling
│   ├── results/# Results Management
│   │   ├── result_store.py # JSONL Result Storage
│   │   └── report.py # Report Generation
│   ├── sandbox/# sandbox implementation
│   │   ├── local.py # Local subprocess
│   │   ├── rock.py # rock remote sandbox
│   │   └── boxlite.py # Boxlite Cloud Sandbox
│   └── config/# Configure the system
│       ├── experiment_config.py # ExperimentConfig data class
│       └── validator.py # Configure Validator
├── configs/examples/# Sample configuration
├── openclaw_deploy/# OpenClaw deployment configuration
│   ├── deploy.py # Deployment script
│   ├── rock_agent_config.yaml # rock agent configuration
│   └── openclaw.json # OpenClaw gateway configuration
├── results/# Output directory of evaluation results
├── tests/# testing (6 phases)
└── docs/# Documentation
```


### Core Data Flow


```
YAML Config
    │  ExperimentConfig.from_yaml()
    ▼
Runner.setup()
    Load Benchmark/Agent/Scorer │  from the registry
    ▼
Runner.run()
    │  benchmark.load_tasks () → Load dataset
    │  for each task:
    │    agent.solve(task) → AgentResponse (answer, trajectory, ...)
    │    scorer.score(task, response) → ScoreResult (correct, score, ...)
    │    result_store.append (task, response, score) → to JSONL
    │  report_generator.generate() → RunSummary
    ▼
Runner.teardown()
    │  Clean up resources
    ▼
Output: results/<run_id>.jsonl + console report
```


---

## Appendix B: Frequently Asked Questions

### Q: vLLM reports insufficient video memory when starting?

Reduce `--max-model-len`, or use a GPU with larger memory:


```bash
# with a smaller context length
--max-model-len 16384

# Note: If you want to run OpenClaw, you need at least 32768 (OpenClaw system prompt ~ 8K tokens)
```


### Q: Does the ROCK sandbox container exit immediately?

Check the following items:
1. Is the `ROCK_PROJECT_ROOT` environment variable set?
2. Whether the `.venv` soft link points to a valid Python environment
3. Is the `gem-llm` package installed?
4. Whether Ray restarts after setting `ROCK_PROJECT_ROOT`


```bash
# Complete restart process
export ROCK_PROJECT_ROOT=/path/to/ROCK
ray stop
ray start --head --port=6380
```


### Q: OpenClaw returns "Context overflow"?

vLLM's `--max-model-len` is too small. OpenClaw's internal system prompt has about 8K tokens and requires at least 32K context windows.


```bash
# Restart vLLM
--max-model-len 32768
```


### Q: OpenClaw returns "404 The model does not exist"?

`OPENAI_MODEL_NAME` in `rock_agent_config.yaml` must be exactly the same as the `--model` parameter of vLLM (including the full path).

### Q: What to fill in for `OPENAI_BASE_URL`?

In `rock_agent_config.yaml`, this is the address within the Docker container to access the vLLM. You can't use `localhost` (that's localhost inside the container). Use host IP:


```bash
hostname -I | awk '{print $1}'
# Example: 127.0.0.1
# then: http://127.0.0.1: 8000/v1
```


### Q: How to run only part of the data set?

Currently you need to modify the Benchmark implementation or manually slice in the Python API:


```python
tasks = benchmark.load_tasks(config)
tasks = tasks [: 5] # Top 5 questions only
```


### Q: How to register a custom Agent?


```python
# my_agent.py
from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry

class MyAgent(Agent):
    name = "my_agent"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "1.0")
        # Initializing your Agent...

    def solve(self, task, sandbox=None) -> AgentResponse:
        # Calling your Model/Agent system...
        return AgentResponse(answer="42", raw_output="...", trajectory=[...])

    def teardown(self) -> None:
        pass

AgentRegistry.register("my_agent", MyAgent)
```


Then use `agent.name: "my_agent"` in the configuration.

### Q: Where are the assessment results files?

In the `output_dir` directory of the configuration file (default `./results/`), the file name is `<run_id>.jsonl`.

One JSON record per line, containing:

```json
{
  "task_id": "aime_60",
  "problem": "Question content...",
  "ground_truth": "204",
  "predicted": "204",
  "correct": true,
  "score": 1.0,
  "rationale": "Numeric comparison: expected=204.0, predicted=204.0, ...",
  "trajectory": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "thinking": "...", "tool_calls": [...]}
  ],
  "token_usage": {"prompt_tokens": 150, "completion_tokens": 1200},
  "wall_time_sec": 47.3,
  "timestamp": "2026-03-09T..."
}
```
