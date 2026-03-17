# AlphaDiana Dashboard User Guide

Dashboard is AlphaDiana’s web management interface, providing the following capabilities:

- **Browse evaluation results**: View the accuracy rate, pass@k/avg@k, classification score, and time-consuming distribution of historical runs
- **Compare multiple runs**: Compare each question side by side to quickly find differences
- **Create Assessment Task**: Configure and launch a Direct LLM or OpenClaw assessment through a form, view progress and logs in real time
- **Manage Sandbox**: Deploy OpenClaw sandbox, view gateway status and model information

## Contents

- [Preconditions](#preconditions)
- [Install](#install)
- [Start](#start)
- [Page features](#page-features)
  - [Runs](#runs-historical-results)
  - [Run Detail](#run-detail-single-run)
  - [Compare](#compare-multi-run-comparison)
  - [Jobs](#jobs-task-management)
  - [New Evaluation](#new-evaluation)
- [API Key Configuration](#api-key-configuration)
- [Environment Variable Reference](#environment-variable-reference)
- [FAQ](#FAQ)

---

## Preconditions

Dashboard runs on top of the existing AlphaDiana environment. Please complete the Quick Start in the main README or install manually:


```bash
conda activate alphadiana
pip install -e '.[all]' # or pip install -e '.[all,dev]'
```


Node.js >= 18 (for building the frontend).

---

## Installation

### 1. Install Dashboard dependencies


```bash
conda activate alphadiana
pip install -e '.[dashboard]'
```


This will additionally install `fastapi`, `uvicorn`, `httpx`.

### 2. Build the frontend


```bash
cd alphadiana/dashboard/frontend
npm install
npm run build
cd ../../..
```


> Re-run `npm run build` after modifying frontend code, except in development mode.

---

## Start

### Recommended method: use run.sh


```bash
conda activate alphadiana
source dev/rock_env.sh # Load the ROCK environment (required for sandbox features)
source dev/.rock_ports.env # Load dynamic ports

cd alphadiana/dashboard
./run.sh # Development mode: frontend hot reload + backend auto reload
./run.sh --prod # Production mode: serve the frontend and backend on one port
```


After startup:
- Development mode: Browser access `http://localhost:5173` (Vite dev server, supports hot update)
- Production mode: Browser access `http://localhost:8000`

If the default port is occupied, `run.sh` will automatically detect and switch to the next available port, and output the actual port used in the terminal.

### Manual start

If you need to control the front and back ends separately:


```bash
# Terminal 1: Launch Backend
conda activate alphadiana
source dev/rock_env.sh
uvicorn alphadiana.dashboard.backend.main:app --reload --port 8000

# Terminal 2: Launch Frontend (Dev Mode)
cd alphadiana/dashboard/frontend
VITE_BACKEND_PORT=8000 npm run dev
```


### Use the backend only (frontend already built)


```bash
conda activate alphadiana
uvicorn alphadiana.dashboard.backend.main:app --host 0.0.0.0 --port 8000
# Browser access http://<host>:8000
```


---

## Page Features

### Runs (historical results)

Lists the evaluation results corresponding to all `.jsonl` files in the `results/` directory, supporting:

- Sort by time and accuracy
- Search and filter (run ID, agent, benchmark)
- Enter the Compare page after multiple selections
- Click on any row to enter Run Detail

**Multiple sample results (num_samples > 1)** additionally display pass@k and avg@k metrics.

### Run Detail (single run)

Complete view of a single assessment:

| Block | Content |
|------|------|
| Summary | Total accuracy, pass@k, avg@k, classification score, token usage |
| Score Matrix | True or false matrix for each sample of each question (automatically displayed when multiple samples are taken) |
| Time Chart | Time-consuming distribution of each task (histogram) |
| Token Chart | Token usage distribution for each task |
| Config | Corresponding YAML configuration (automatically saved to `configs/` by Dashboard) |
| Results Table | For the score, answer, and correctness of each question, click to expand Trajectory to view the complete reasoning track |

### Compare (multi-run comparison)

Check 2 or more runs from the Runs list and click **Compare** to enter the comparison page.

- Display summary indicators of each run side by side
- Line-by-line comparison by topic: the answers and correct or incorrect status of each question in each run are clear at a glance
- Suitable for comparing the performance of different agents and different models under the same benchmark

### Jobs (task management)

View all created assessment tasks:

- **Progress Bar**: Real-time display of completed/total number of tasks
- **Real-time accuracy**: dynamically updated as tasks are completed
- **Log Drawer**: Click on any task to expand and view real-time logs (logs do not interfere with each other when multiple tasks are concurrent)
- **Cancel**: Click Cancel on a running task to mark it as canceled.

> Note: The task status is only saved in memory. The historical task records will be cleared after restarting the backend, but the JSONL file in `results/` will not be affected.

### New Evaluation

Configure and submit assessment tasks through forms, supporting two types of agents:

#### Direct LLM

Good for quick baseline testing, no ROCK sandbox required.

| Field | Description |
|------|------|
| Benchmark | Choose preset benchmark or customize (AIME 2024/2025, MATH, etc.) |
| Model | OpenAI API-compatible model name (such as `Qwen3-8B`) |
| API Base URL | Model service address (such as `http://localhost:8000/v1`) |
| API Key | Paste directly, or enter `$VARIABLE_NAME` to reference the key in `.env` |
| Max Concurrent | Number of concurrent tasks |
| Num Samples | Number of samples for each question (automatically calculated when >1 pass@k/avg@k) |

#### OpenClaw (requires ROCK sandbox)

Two operations are provided:

**Use an existing sandbox**: Select the deployed OpenClaw instance from the Sandbox drop-down box to view the gateway status and loaded model information, and directly submit for evaluation.

**Deploy & Run**: Fill in the model API configuration and evaluation configuration at the same time. After submission, the Dashboard will:
1. Start sandbox deployment in the background (allow about 5-10 minutes, including `npm install`)
2. Automatically detect the health status of the gateway
3. Automatically start evaluation after deployment is complete

The entire process can be monitored on the Jobs page.

---

## API Key configuration

The API Key input box supports two modes:

**Paste directly**: Paste the complete key in the input box.

**Referencing environment variables**: Enter `$VARIABLE_NAME` (such as `$OPENROUTER_API_KEY`), and the backend will read the actual value from the `.env` file in the project root directory or from a system environment variable. The key will not be exposed in the API response.

Create the `.env` file in the project root directory:


```bash
cat > .env <<'EOF'
OPENROUTER_API_KEY="sk-or-v1-xxxxxxxx"
OPENAI_API_KEY="sk-xxxxxxxx"
SILICONFLOW_API_KEY="sk-xxxxxxxx"
ARK_API_KEY="xxxxxxxx"
DEEPSEEK_API_KEY="sk-xxxxxxxx"
EOF
```


When entering the API Base URL, Dashboard will automatically match and fill in the corresponding variable name based on the domain name:

| API Base URL | Automatch variables |
|---|---|
| `https://openrouter.ai/api/v1/` | `$OPENROUTER_API_KEY` |
| `https://api.openai.com/v1/` | `$OPENAI_API_KEY` |
| `https://api.siliconflow.cn/v1/` | `$SILICONFLOW_API_KEY` |
| `https://ark.cn-beijing.volces.com/api/...` | `$ARK_API_KEY` |
| `https://api.deepseek.com/v1/` | `$DEEPSEEK_API_KEY` |

---

## Environment Variable Reference

| Variable | Default | Description |
|------|--------|------|
| `ALPHADIANA_RESULTS_DIR` | `./results` | Directory to store `.jsonl` result files |
| `ALPHADIANA_CONFIGS_DIR` | `./configs` | Directory to store the `.yaml` configuration file (automatically written by Dashboard) |
| `ALPHADIANA_BACKEND_PORT` | `8000` | Backend preferred port (automatically incremented when occupied) |
| `ALPHADIANA_FRONTEND_PORT` | `5173` | Front-end preferred port (automatically incremented when occupied) |
| `ROCK_BASE_URL` | Automatic detection | ROCK admin URL, usually injected by `dev/rock_env.sh` |
| `ROCK_PROXY_URL` | Automatic detection | ROCK proxy URL, usually injected by `dev/rock_env.sh` |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace mirror site (automatically used when not set) |

Port variables are automatically handled by `run.sh` and usually do not need to be set manually.

---

## FAQ

**Q: Startup shows `ModuleNotFoundError: No module named 'fastapi'`**


```bash
pip install -e '.[dashboard]'
```


**Q: The frontend cannot be opened, or it shows "Cannot connect to backend"**

Confirm that the backend is started and `VITE_BACKEND_PORT` is consistent with the actual port of the backend:


```bash
# Development Mode: Starting with run.sh will automatically sync the ports
cd alphadiana/dashboard && ./run.sh
```


**Q: Jobs page logs are out of order or confused**

Dashboard uses `contextvars` to isolate the logs of concurrent tasks. In Python 3.12+, ThreadPoolExecutor child threads will also inherit the context correctly. If running Python 3.10/3.11, there may be slight aliasing in the concurrent task logs, but this does not affect the result files.

**Q: Sandbox related functions are not available (gray)**

The ROCK service is not running, or `dev/rock_env.sh` is not sourced. Sandbox-independent functionality (browse results, Direct LLM evaluation) is still available.

**Q: The Config area of the Run Detail page is blank**

Config is automatically saved to `configs/<run_id>.yaml` by Dashboard when creating a task. Tasks started through the command line `alphadiana run` will not automatically generate this file. You need to manually copy the configuration file to the `configs/` directory.
