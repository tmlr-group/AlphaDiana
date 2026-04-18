# AlphaDiana Evaluation Tutorial: OpenClaw + Qwen3-8B + AIME 2024

This is a historical tutorial kept for reference.

For the current onboarding path, use `README.md`, `docs/README.md`, and
`docs/getting_started.md`.

This tutorial takes **OpenClaw agent + Qwen3-8B model + AIME 2024 data set** as an example to demonstrate how to use the AlphaDiana evaluation framework to complete a complete end-to-end evaluation.

## Architecture Overview


```
AlphaDiana Runner
       │
       ▼
  ROCK Proxy (:9001)
       │
       ▼
  ROCK Sandbox (Docker)
       │
       ▼
  OpenClaw Gateway (:8080)
       │
       ▼
  vLLM (Qwen3-8B, :8000)
```


- **AlphaDiana**: Evaluation orchestration framework, responsible for loading data sets, scheduling tasks, scoring, and saving results
- **ROCK**: Sandbox system, running OpenClaw gateway in Docker container
- **OpenClaw**: Agent system, inference through multiple rounds of LLM calls
- **vLLM**: High-performance LLM inference service, providing OpenAI compatible API

## Preconditions

- Linux server with NVIDIA GPU (A800/A100 recommended, video memory ≥ 40GB)
- Docker is installed and the current user is in the docker group
- Conda package manager

## Step 1: Create Conda environment


```bash
conda create -n alphadiana python=3.11 -y
conda activate alphadiana
```


## Step 2: Install AlphaDiana


```bash
cd /hd2/chentao/AlphaDiana-dev
pip install -e .
```


Verify installation:


```bash
alphadiana list-benchmarks
Data Type
#   - aime
#   - math
#   - hle
#   - frontier_math
#   - terminal_bench
#   - osworld
```


## Step 3: Start vLLM service

Select an idle GPU to start Qwen3-8B:


```bash
# Check GPU usage
nvidia-smi

# Choose a free GPU (e.g. GPU 2)
CUDA_VISIBLE_DEVICES=2 python -m vllm.entrypoints.openai.api_server \
    --model /hd1/models/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218 \
    --host 0.0.0.0 \
    --port 8000 \
    --trust-remote-code \
    --enable-auto-tool-choice \
    --tool-call-parser hermes \
    --max-model-len 16384 &
```


> **Note**: `--enable-auto-tool-choice --tool-call-parser hermes` is required by OpenClaw because OpenClaw uses tool calling for multi-round inference.

Verify vLLM readiness:


```bash
curl http://localhost:8000/v1/models
# Model list should be returned
```


## Step 4: Start the ROCK sandbox environment

### 4.1 Install ROCK


```bash
cd /hd2/chentao/ROCK
pip install -e .
```


### 4.2 Install Redis Stack

ROCK depends on Redis Stack (requires JSON.SET command):


```bash
sg docker -c 'docker run -d --name redis-stack \
    -p 6379:6379 \
    redis/redis-stack-server:latest'
```


### 4.3 Configure ROCK

Create configuration file `~/.rock/config.ini`:


```ini
[ray]
address = auto
```


Set environment variables and create `.venv` soft link:


```bash
export ROCK_PROJECT_ROOT=/hd2/chentao/ROCK

# LocalRuntimeEnv requires .venv to point to a Python environment
ln -sf $(conda info --base)/envs/alphadiana /hd2/chentao/ROCK/.venv
```


### 4.4 Start Ray + ROCK service


```bash
# Start Ray (rock based on Ray)
export ROCK_PROJECT_ROOT=/hd2/chentao/ROCK
ray start --head --port=6380

# Starting rock admin (write operation, port 9000)
sg docker -c 'python -m rock.deployments.admin &'

# Start rock proxy (read/runtime operation, port 9001)
sg docker -c 'python -m rock.deployments.proxy &'
```


Verify ROCK is ready:


```bash
curl http://localhost: 9000/health # admin Health Check
curl http://localhost: 9001/health # proxy Health Check
```


## Step 5: Deploy OpenClaw Gateway

### 5.1 Prepare deployment configuration

The deployment configuration is already in the `openclaw_deploy/` directory:

**`openclaw_deploy/rock_agent_config.yaml`** — ROCK agent configuration:


```yaml
working_dir: "."

run_cmd: >
  NPM_BIN_DIR=$(find /tmp/rock-runtime-envs/node -name 'npm' -type f ! -path '*/nodewin/*' ! -path '*/shims/*' 2>/dev/null | head -1 | xargs -r dirname) &&
  export PATH=${NPM_BIN_DIR:+${NPM_BIN_DIR}:}$PATH
  mkdir -p /tmp/empty-bundled /tmp/oc_home &&
  OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json
  OPENCLAW_HOME=/tmp/oc_home
  OPENCLAW_BUNDLED_PLUGINS_DIR=/tmp/empty-bundled
  nohup openclaw gateway >> /tmp/gateway.log 2>&1 &

runtime_env_config:
  type: node
  npm_registry: https://registry.npmmirror.com
  custom_install_cmd: if command -v openclaw >/dev/null 2>&1; then echo "Using preinstalled OpenClaw from sandbox image"; else git config --global url.'https://github.com/'.insteadOf 'ssh://git@github.com/' && npm install -g openclaw@2026.3.7 --registry https://registry.npmmirror.com; fi
  install_timeout: 1200

env:
  OPENAI_BASE_URL: "http://127.0.0.1:8000/v1" # ← Change to your vLLM address
  OPENAI_API_KEY: "EMPTY"
  OPENAI_MODEL_NAME: "/hd1/models/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218"
  OPENCLAW_GATEWAY_TOKEN: "OPENCLAW"
```


**`openclaw_deploy/openclaw.json`** — OpenClaw gateway configuration, defining model provider and gateway authentication methods.
The default image used by `deploy.py` is `tmlrgroup/alphadiana:v1`. The model stays configurable through `OPENAI_MODEL_NAME`.

### 5.2 Create Sandbox and deploy


```bash
cd /hd2/chentao/AlphaDiana-dev/openclaw_deploy
python deploy.py
```


The script will output the Sandbox ID, write it down:


```
Sandbox ID: a37118857bee43188375c7ce2f343eb5
API base: http://127.0.0.1:9001/apis/envs/sandbox/v1/sandboxes/a37118857bee43188375c7ce2f343eb5/proxy/v1
```


Set environment variables (for subsequent testing):


```bash
export OPENCLAW_SANDBOX_ID=a37118857bee43188375c7ce2f343eb5
```


Verify that the OpenClaw gateway is available:


```bash
curl -X POST \
  "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/${OPENCLAW_SANDBOX_ID}/proxy/v1/chat/completions" \
  -H "Authorization: bearer OPENCLAW" \
  -H "Content-Type: application/json" \
  -d '{"model":"openclaw","messages":[{"role":"user","content":"What is 2+3?"}],"max_tokens":256}'
```


## Step 6: Write evaluation configuration

Create YAML configuration file `configs/openclaw_aime2024.yaml`:


```yaml
run_id: "openclaw-qwen3-8b-aime2024-001"

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
metadata:
  author: "your_name"
  gpu: "A800"
  notes: "OpenClaw + Qwen3-8B on AIME 2024"
```


> **IMPORTANT**: Replace `<SANDBOX_ID>` with the actual Sandbox ID obtained in Step 5.

### Configuration field description

| Field | Description |
|------|------|
| `run_id` | The unique identifier of this run, the result file name is based on this |
| `agent.name` | The agent name used (`openclaw`, `direct_llm`) |
| `agent.config` | Agent specific configuration (API address, model name, token, etc.) |
| `benchmark.name` | Data set name (`aime`, `math`, `hle`, etc.) |
| `benchmark.config` | Dataset configuration (HuggingFace path, split) |
| `scorer.name` | Scoring method (`numeric`, `exact_match`, `llm_judge`) |
| `max_concurrent` | Number of parallel tasks (1 = serial) |
| `output_dir` | JSONL result file output directory |

## Step 7: Run the evaluation

### Method A: CLI command (recommended)


```bash
# Validate the configuration first
alphadiana validate configs/openclaw_aime2024.yaml

Run assessment
alphadiana run configs/openclaw_aime2024.yaml
```


Output example:


```
Run completed: openclaw-qwen3-8b-aime2024-001
  Accuracy:   0.2333
  Mean Score: 0.2333
  Tasks:      30/30 completed
```


### Method B: Python API


```python
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner

config = ExperimentConfig.from_yaml("configs/openclaw_aime2024.yaml")
runner = Runner(config)

runner.setup()
try:
    summary = runner.run()
    print(f"Accuracy: {summary.accuracy:.4f} ({int(summary.accuracy * 30)}/30)")
    print(f"Mean Score: {summary.mean_score:.4f}")
    print(f"Mean Wall Time: {summary.mean_wall_time_sec:.1f}s")
    print(f"Total Tokens: {summary.total_tokens}")
finally:
    runner.teardown()
```


### Method C: Construct ExperimentConfig directly (no YAML file required)


```python
from alphadiana.config.experiment_config import ExperimentConfig
from alphadiana.runner.runner import Runner

config = ExperimentConfig(
    run_id="my-test-run",
    agent_name="openclaw",
    agent_version="latest",
    agent_config={
        "api_base": "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<SANDBOX_ID>/proxy/v1",
        "model": "openclaw",
        "gateway_token": "OPENCLAW",
        "temperature": 0.0,
        "max_tokens": 8192,
    },
    benchmark_name="aime",
    benchmark_config={
        "dataset": "HuggingFaceH4/aime_2024",
        "split": "train",
    },
    scorer_name="numeric",
    scorer_config={"tolerance": 1e-6},
    max_concurrent=1,
    output_dir="./results",
)

runner = Runner(config)
runner.setup()
try:
    summary = runner.run()
finally:
    runner.teardown()
```


## Step 8: View results

### 8.1 Result JSONL file

The evaluation results are saved in `{output_dir}/{run_id}.jsonl`, with one JSON record per line:


```bash
cat results/openclaw-qwen3-8b-aime2024-001.jsonl | python -m json.tool | head -30
```


Each record contains:


```json
{
  "task_id": "aime_60",
  "problem": "Every morning Aya goes for a ...",
  "ground_truth": "204",
  "predicted": "204",
  "correct": true,
  "score": 1.0,
  "rationale": "Parsed predicted=204.0, expected=204.0, diff=0.0 <= tolerance=1e-06",
  "trajectory": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "... (full inference process)..."}
  ],
  "token_usage": {"prompt_tokens": 150, "completion_tokens": 1200, "total_tokens": 1350},
  "wall_time_sec": 45.3,
  "timestamp": "2026-03-08T08:07:39.226945+00:00"
}
```


### 8.2 Generate Markdown report


```bash
alphadiana report ./results
```


Or in Python:


```python
from alphadiana.results.report import ReportGenerator
from alphadiana.results.result_store import ResultStore

store = ResultStore(output_dir="./results", run_id="openclaw-qwen3-8b-aime2024-001")
gen = ReportGenerator()
summary = gen.generate(store, config)
print(gen.to_markdown(summary))
```


### 8.3 Question-by-question analysis


```python
import json

with open("results/openclaw-qwen3-8b-aime2024-001.jsonl") as f:
    results = [json.loads(line) for line in f]

correct = sum(1 for r in results if r["correct"])
print(f"Accuracy: {correct}/{len(results)} = {correct/len(results):.4f}")

print(f"\n{'Task ID':<20} {'GT':>6} {'Pred':>6} {'OK':>4} {'Time':>8}")
print("-" * 50)
for r in results:
    mark = "Y" if r["correct"] else "N"
    print(f"{r['task_id']:<20} {r['ground_truth']:>6} {str(r['predicted']):>6} {mark:>4} {r['wall_time_sec']:>7.1f}s")
```


## Step 9: Run the test suite (verification environment)

AlphaDiana offers 6 stages of testing:


```bash
# Phase1: Data loading and scoring (no external service required)
python -m pytest tests/test_phase1_data.py -v

# Phase 2: vLLM Service (requires vLLM to be started)
python -m pytest tests/test_phase2_vllm.py -v -m integration

# Phase 3: rock Sandbox (requires rock to be activated)
python -m pytest tests/test_phase3_rock.py -v -m integration

# Phase4: OpenClaw Gateway (requires OpenClaw deployed)
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/test_phase4_openclaw.py -v -m integration

# Phase5: Single Question Validation
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/test_phase5_single.py -v

# Phase 6: Full AIME 2024 assessment (30 questions, time consuming)
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/test_phase6_full.py -v -m integration

# Run all at once (without integration testing)
python -m pytest tests/ -v -k "not integration"

# Test all (requires all services to be ready)
OPENCLAW_SANDBOX_ID=<your_id> python -m pytest tests/ -v
```


## Extension: Use other Agent/Benchmark combinations

### Direct LLM + MATH dataset```yaml
run_id: "direct-llm-qwen3-8b-math"
agent:
  name: direct_llm
  config:
    model: "/hd1/models/models--Qwen--Qwen3-8B/snapshots/..."
    api_base: "http://localhost:8000/v1"
    api_key: "EMPTY"
    temperature: 0.0
    max_tokens: 4096
benchmark:
  name: math
  config:
    dataset: "hendrycks/competition_math"
    split: "test"
scorer:
  name: exact_match
max_concurrent: 4
output_dir: "./results"
```


> **Direct LLM** directly calls vLLM API without going through OpenClaw/ROCK, suitable for quick baseline testing.

### Custom Agent

Inherit the `Agent` base class and register:


```python
# alphadiana/agent/my_agent.py
from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry

class MyAgent(Agent):
    name = "my_agent"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "1.0")
        # Initializing your agent...

    def solve(self, task, sandbox=None) -> AgentResponse:
        # Calling your model/service...
        return AgentResponse(
            answer="42",
            trajectory=[...],
            raw_output="...",
            token_usage={},
            wall_time_sec=1.0,
        )

    def teardown(self) -> None:
        pass

AgentRegistry.register("my_agent", MyAgent)
```


## FAQ

**Q: OpenClaw times out and returns an empty response? **
Timeouts exist at multiple layers and should be checked one by one:
1. **Gateway agent timeout**: `openclaw.json` → `agents.defaults.timeoutSeconds` (configured to 3600s)
2. **ROCK proxy timeout**: `post_proxy` timeout in ROCK source `sandbox_proxy_service.py` (changed to read from `proxy_config.timeout`, default 180s; can be increased via ROCK YAML config `proxy_service.timeout: 600`)
3. **Client request timeout**: AlphaDiana YAML → `agent.config.request_timeout` (default 1800s)
4. **max_tokens**: Recommended 65536+, to prevent thinking models from exhausting tokens and producing no output

AlphaDiana automatically retries on empty responses (default 5 attempts). The retry interval for `empty_response` type starts at 60s.

**Q: vLLM reports 400 error "auto tool choice requires --enable-auto-tool-choice"? **
OpenClaw uses tool calling, which requires adding `--enable-auto-tool-choice --tool-call-parser hermes` when vLLM is started.

**Q: Does the ROCK sandbox container exit immediately? **
Check:
1. Is `ROCK_PROJECT_ROOT` set correctly?
2. Whether the `.venv` soft link points to a valid Python environment
3. Whether the `gem-llm` package has been installed (`pip install gem-llm`)
4. Whether Ray restarts after setting `ROCK_PROJECT_ROOT`

**Q: How to run only a subset of the data set? **
`split` and other HuggingFace parameters can be specified in `benchmark_config` to control the amount of data loaded.
