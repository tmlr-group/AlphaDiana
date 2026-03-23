# AlphaDiana: Evaluation Framework for Agentic Reasoning with OpenClaw

**AlphaDiana** is an open-source evaluation framework for benchmarking LLM agents on frontier reasoning tasks. It provides standardized, reproducible evaluation of agentic systems — such as [OpenClaw](https://github.com/openclaw/openclaw) — across mathematical reasoning benchmarks like AIME 2024/2025 and MATH, with sandboxed code execution, multi-turn tool use, and full trajectory recording.

Use AlphaDiana to answer: *How well does your agent actually reason, compared to a bare model?*

<p align="center">
  <img src="./assets/fig1.jpg" width="100%">
</p>
<p align="center">
  <em><strong>Figure 1.</strong> Why agentic reasoning requires system-level evaluation: foundation models are evaluated like engines, agents behave like cars shaped by tools and state, and AlphaDiana serves as the tournament organizer that standardizes evaluation and records canonical traces.</em>
</p>

## Key Features

- **OpenClaw Agent Evaluation** — First-class integration with OpenClaw for multi-turn agentic reasoning with tool use, code execution, and chain-of-thought traces
- **Sandboxed Execution via ROCK** — Run agent-generated code safely inside Docker-based ROCK sandboxes with full isolation and automatic lifecycle management
- **Reasoning Benchmarks** — Built-in benchmark loaders for AIME 2024, MATH, and user-defined custom problem sets
- **Direct LLM Baseline** — Evaluate any OpenAI-compatible model (vLLM, OpenRouter, etc.) directly without agent orchestration, for apples-to-apples comparison
- **Web Dashboard** — Visual UI for launching evaluations, browsing results, and comparing runs side-by-side
- **Automatic Sandbox Management** — Sandboxes are created and torn down automatically per run; just set `max_concurrent` and go

## Quick Start

### Prerequisites

- Linux environment
- Python >= 3.10, conda
- Docker (current user in the `docker` group)
- Git
- (Optional) An API key for remote model providers (e.g., OpenRouter); not needed for localhost deployments

### Install

```bash
git clone https://github.com/tmlr-group/AlphaDiana
cd AlphaDiana

# One-click setup: conda env, ROCK, Redis, Ray, admin, proxy
bash scripts/quickstart.sh

# Pull the recommended reasoning image (pre-installed OpenClaw)
docker pull ghcr.io/tsrigo/openclaw-reasoning:20260320

# Set your model provider (or point to a local vLLM server)
export OPENAI_BASE_URL=...
export OPENAI_API_KEY=...
export OPENAI_MODEL_NAME=...
```

`quickstart.sh` creates a conda environment, installs all dependencies, detects available ports, and starts Redis, Ray, ROCK admin, and ROCK proxy. After it finishes, source the environment in every new terminal:

```bash
conda activate alphadiana
source scripts/rock_env.sh
source scripts/.rock_ports.env
```

For a complete walkthrough, see [`docs/getting_started.md`](docs/getting_started.md). For manual setup and troubleshooting, see [`docs/setup_detail.md`](docs/setup_detail.md).

### Configure

All service ports are automatically configured by `quickstart.sh`. The main things you need to set are the **model provider** (via environment variables above) and the **experiment config** (a YAML file). Here is an annotated example:

```yaml
run_id: "openclaw-qwen3-8b-aime2024-001"

agent:
  name: openclaw                    # "openclaw" or "direct_llm"
  config:
    rock_image: "ghcr.io/tsrigo/openclaw-reasoning:20260320"
    rock_agent_config_path: "openclaw_deploy/rock_agent_config.prebuilt.yaml"
    openclaw_config_path: "openclaw_deploy/openclaw.json"
    rock_memory: "4g"               # Memory per sandbox container
    rock_cpus: 1                    # CPUs per sandbox container
    system_prompt: You are an expert problem solver. ...

benchmark:
  name: aime                        # Benchmark loader (see alphadiana/benchmark/)
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

scorer:
  name: math_verify
  config:
    tolerance: 1e-6

max_concurrent: 1                   # Number of sandboxes to run in parallel
output_dir: "./results"
```

Ready-made examples are in `configs/examples/`.

### Run OpenClaw Agent

The recommended starting point is the **single-sandbox example**. AlphaDiana automatically creates a ROCK sandbox (with OpenClaw pre-installed from the Docker image), runs the evaluation, and tears it down afterward — no manual `deploy.py` step required.

```bash
# Validate the config first
alphadiana validate configs/examples/openclaw_aime2024.yaml

# Run the evaluation
alphadiana run configs/examples/openclaw_aime2024.yaml

# View results
alphadiana report results/
```

This defaults to a single sandbox with `4g` memory and `1` CPU. To increase parallelism, change `max_concurrent` in the YAML — the CLI creates the matching number of sandboxes automatically. See `configs/examples/openclaw_aime2024_multisandbox.yaml` for a multi-sandbox example.

### Run Direct LLM (No Agent)

Evaluate any OpenAI-compatible model directly — useful for quick baselines. No ROCK sandbox required.

```bash
alphadiana run configs/examples/direct_llm.yaml
```

Fill in the `model`, `api_base`, and `api_key` fields in the config. See [`docs/getting_started.md`](docs/getting_started.md) for a step-by-step Direct LLM tutorial.

### Run a Custom Problem Set

The `custom` benchmark lets you define problems directly in YAML — no dataset download needed. Useful for spot-checking a specific problem or quick debugging.

```yaml
run_id: "my-custom-run"

agent:
  name: openclaw  # or direct_llm
  config:
    # ... (same agent config as above)

benchmark:
  name: custom
  config:
    problems:
      - id: "problem_1"
        problem: "Find the number of ordered pairs (x,y) of positive integers satisfying x + y = 100 and x * y is divisible by 6."
        answer: "117"
      - id: "problem_2"
        problem: "What is the sum of all prime numbers less than 20?"
        answer: "77"

scorer:
  name: numeric
  config:
    tolerance: 1e-6

max_concurrent: 1
output_dir: "./results"
```

Any agent and scorer work with `custom` the same way as with any other benchmark.

## CLI Reference

| Command | Description |
|---|---|
| `alphadiana run <config.yaml>` | Run an evaluation experiment |
| `alphadiana validate <config.yaml>` | Validate a config without running |
| `alphadiana report <results_dir>` | Generate reports from result files |
| `alphadiana batch <config1> <config2> ...` | Run multiple experiments (use `--parallel` for concurrent execution) |
| `alphadiana list-benchmarks` | List all registered benchmarks |

Use `-o key=value` to override config values from the command line (e.g., `-o max_concurrent=4`).

## Data Flow

```
YAML Config ──→ Runner ──→ Benchmark (load tasks)
                  │              │
                  │              ▼
                  │         Agent (generate answers)
                  │              │ ↔ Sandbox (code execution)
                  │              ▼
                  │         Scorer (verify answers)
                  │              │
                  ▼              ▼
               results/<run_id>.jsonl ──→ Report / Dashboard
```

## Project Structure

```
AlphaDiana/
├── alphadiana/                   # Core package
│   ├── cli.py                    # CLI entry point
│   ├── agent/                    # Agent implementations
│   │   ├── direct_llm.py         #   Direct LLM baseline (single-turn)
│   │   └── openclaw.py           #   OpenClaw agent (multi-turn + tools)
│   ├── benchmark/                # Benchmark loaders
│   │   ├── aime.py               #   AIME competition math
│   │   └── custom.py             #   User-defined inline problems
│   ├── scorer/                   # Answer scorers
│   │   ├── numeric.py            #   Numeric comparison
│   │   ├── exact_match.py        #   Exact string match
│   │   ├── math_verify_scorer.py #   Math-aware verification
│   │   └── llm_judge.py          #   LLM-as-judge
│   ├── runner/                   # Orchestration
│   ├── sandbox/                  # Sandbox backends (ROCK, local, etc.)
│   ├── results/                  # Result storage and reporting
│   ├── config/                   # Config parsing and validation
│   └── dashboard/                # Web UI (FastAPI + React)
├── configs/examples/             # Ready-made experiment configs
├── openclaw_deploy/              # OpenClaw deployment configs
├── scripts/                      # Setup and utility scripts
├── docs/                         # Documentation
└── tests/                        # Test suite
```

## Dashboard

A web UI for managing and viewing evaluation tasks without manually editing YAML or reading JSONL files.

![](./assets/fig2.png)

**Features:** browse run history, compare multiple runs side-by-side, launch evaluations via forms, monitor job progress with real-time logs, and manage ROCK sandboxes.

### Install & Build

```bash
pip install -e '.[dashboard]'        # Backend dependencies (fastapi, uvicorn, httpx)
cd alphadiana/dashboard/frontend
npm install && npm run build          # Build the frontend
cd ../../..
```

### Start the Dashboard

```bash
source scripts/rock_env.sh           # Load ROCK environment
source scripts/.rock_ports.env       # Load dynamic ports

cd alphadiana/dashboard
./run.sh                              # Dev mode (hot reload at localhost:5173)
./run.sh --prod                       # Production mode (single port at localhost:8000)
```

If the default port is occupied, `run.sh` will automatically switch to the next available port.

For full usage details (page features, API key configuration, environment variables), see [`docs/dashboard.md`](docs/dashboard.md).

## Documentation

| Document | Description |
|---|---|
| [`docs/getting_started.md`](docs/getting_started.md) | Complete step-by-step tutorial (Qwen3-8B + AIME 2024) |
| [`docs/setup_detail.md`](docs/setup_detail.md) | Manual setup and troubleshooting |
| [`docs/dashboard.md`](docs/dashboard.md) | Dashboard user guide |
