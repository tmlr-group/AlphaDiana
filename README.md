# AlphaDiana: A Measurement System for Agents in Verifiable Reasoning Tasks

**AlphaDiana** is an open-source measurement system for **LLM-based agents on verifiable reasoning tasks**. It connects benchmarks, agents, environments, and scorers through modular interfaces while preserving native harness control loops. Saved results link evaluation outcomes to execution status, available trajectories, and resource use.

![Position of AlphaDiana](docs/assets/alphadiana-motivation.png)

## Why AlphaDiana?

Reasoning performance in agent systems depends on more than the base model alone. It is also shaped by the **agent framework, tool interface, execution environment, and evaluation protocol**.

AlphaDiana supports comparisons of complete agents and controlled changes to components such as tools, skills, and memory. It measures answer correctness alongside execution failures, action patterns, and resource costs under recorded task and runtime settings.

![AlphaDiana executes configured agents, saves artifacts, and measures outcomes, failures, resource use, and component interventions.](docs/assets/alphadiana-overview.png)

*Overview from the latest shared draft (Figure 2).*

With AlphaDiana, you can:
- evaluate native harnesses such as **OpenClaw, OpenCode, and ZeroClaw**
- compare against a **direct LLM baseline**
- run evaluations with **sandboxed execution**
- benchmark on **AIME 2026, GPQA-Diamond, HLE, IMO-AnswerBench, MMMU-Pro, SWE-bench Verified, Terminal-Bench 2, and custom tasks**
- record and inspect **full execution traces**
- launch and compare runs through both **CLI and dashboard**

Typical questions AlphaDiana helps answer:
- How much does an agent improve over the base model?
- How well does it perform on a target benchmark?
- How much do tools and sandbox settings affect results?
- What failure modes appear in execution traces?

## Key Features

- **Standardized evaluation** for reproducible benchmarking of agentic reasoning systems  
- **Native harness adapters** for multi-turn reasoning with tool use and code execution
- **ROCK sandbox integration** for safe, isolated execution  
- **Direct LLM baseline** for clean agent-vs-model comparison  
- **Built-in benchmarks** covering AIME 2026, GPQA-Diamond, HLE, IMO-AnswerBench, MMMU-Pro, SWE-bench Verified, Terminal-Bench 2, and custom tasks
- **Full trace logging** for debugging, inspection, and analysis  
- **Web dashboard** for launching, monitoring, and comparing runs  
- **Automatic sandbox management** with configurable concurrency

## How it works

![AlphaDiana system design: configuration, runner, session management, native agent runtimes, inference endpoints, and artifact analysis.](docs/assets/alphadiana-system.png)

Configuration selects tasks, agents (model and harness), environments, and scorers. The runner coordinates sessions and native harness execution, then saves available outputs, execution status, and scores for analysis. Model endpoints are separate from the environments that execute tools; file reuse and memory retention are recorded separately. See [Architecture](docs/architecture/README.md) for implementation details.

## Quick Start

### 1. Install

The commands below assume a Linux shell and Python 3.10 or newer. You also need an OpenAI-compatible model endpoint, its model ID, and an API key. The direct-model check needs no container runtime; the OpenCode example requires Podman and the controller image built below.

```bash
git clone https://github.com/tmlr-group/AlphaDiana.git
cd AlphaDiana
python -m venv .venv
source .venv/bin/activate
pip install -e '.[analysis,benchmarks,agents]'
```

### 2. Configure your model

Replace the placeholders with your provider's values. Set these variables in the same terminal used to run the examples; in a new terminal, activate `.venv` and set them again.

```bash
export OPENAI_BASE_URL="https://your-provider.example/v1"
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL_NAME="your-model-id"
```

LLM judges also require an explicitly configured endpoint, key and model. `OPENAI_MODEL` takes precedence over `OPENAI_MODEL_NAME` for judging. Offline analysis requires no model service. Optional Analyzer judgments can use separate `ANALYSIS_MODEL`, `ANALYSIS_BASE_URL` and `ANALYSIS_API_KEY` settings. See [Python API and analysis](docs/getting-started/python-api.md) for composition, saved-run analysis and model annotations.

### 3. Check the direct-model baseline

This runs one AIME problem without an agent harness or ROCK. The checked-in configuration uses Qwen-style thinking options and requests log probabilities; use a compatible endpoint or adapt those options in the YAML to your provider before running it.

```bash
alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o benchmark.config.max_tasks=1
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=quickstart_aime_directllm_t1_k1 \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1
```

<a id="use-the-python-api"></a>

### 4. Use the Python API

Run OpenCode on three AIME 2026 problems, score its answers, and export the analysis from Python. From the repository root, build its controller image once with a working Podman installation:

```bash
podman build \
  -f alphadiana/harness/opencode/deploy/Containerfile.podman-controller \
  -t localhost/alphadiana-opencode-podman:latest \
  alphadiana/harness/opencode/deploy
```

The image pins OpenCode 1.3.2. The example uses the model service settings above; see the [Podman setup guide](docs/benchmarks/podman.md) for runtime checks.

![Figure 4: Functional interface of AlphaDiana, reproduced from the draft.](docs/assets/alphadiana-interface.png)

The original manuscript figure illustrates the workflow; some API spellings still differ from the implementation. Use the executable example below: the harness name and version are separate, dataset paths specify `benchmark=`, and scorer construction uses `ad.scorer.create(...)`. OpenCode manages its own Podman controller, so this example does not pass `env="sandbox"`.

```python
import alphadiana as ad

# Select the agent harness and the problems to evaluate.
agent = ad.agent(
    "opencode", version="1.3.2", controller_mode="podman",
    controller_image="localhost/alphadiana-opencode-podman:latest",
)
data = ad.dataset("MathArena/aime_2026", benchmark="aime", max_tasks=3)
scorer = ad.scorer.create("numeric")

# Run the agent and save its answers, scores, and execution traces.
artifacts = ad.runner(run_id="aime_opencode_demo").compute(
    agent=agent, data=data, scorer=scorer,
)

# Analyze the saved run and export tables and figures.
ad.analyzer(artifacts).export("figures/aime_opencode_demo")
```

This uses the provider environment settings above. Results and execution evidence are saved under `results/`; analysis exports include tables and PDF/SVG/PNG figures. See [Python API and analysis](docs/getting-started/python-api.md) for reopening, rescoring and comparing saved runs. Native harnesses still require their own container/runtime configuration.

### 5. Print a report

```bash
alphadiana report results/
```

`alphadiana report` prints Markdown to standard output. Redirect it when you want a file, for example: `alphadiana report results/ > report.md`.

For a full walkthrough, see [Getting Started](docs/getting-started/quick-start.md). For the documentation entry point, see [Welcome to AlphaDiana](docs/README.md). For Podman-backed paths, start from the matching page under [Benchmarks](docs/benchmarks/README.md). For manual setup and recovery, see [Installation](docs/getting-started/installation.md) and [Troubleshooting](docs/getting-started/troubleshooting.md). For ZeroClaw, see the [harness guide](docs/harnesses/zeroclaw.md) and [AIME benchmark page](docs/benchmarks/aime.md).

## Repository map

```text
alphadiana/
  engine/        # execution, configuration, and rollout campaigns
  harness/       # agent implementations and runtime skills
  benchmarks/    # task loaders and official verifier integrations
  results/       # persistence, traces, score status, and sample statistics
  analysis/      # measurement plugins, Analyzer composition, and exports
  dashboard/     # React frontend and FastAPI backend
scripts/
  setup/         # environment and dependency setup
  validation/    # preflight, smoke tests, and evaluator audits
  action_audit/  # human action annotation and agreement
  paper_analysis/# archived paper inputs and plotting workflows
tests/           # grouped by analysis, API, engine, harness, and benchmark
configs/         # experiment configurations
docs/            # guides, contracts, and documentation assets
```

See the [analysis module map](alphadiana/analysis/README.md) and [operator workflow map](scripts/README.md) for implementation entry points.

## Configuration

AlphaDiana is configured with a YAML file. At a high level, you specify:

- the **agent**
- the **benchmark**
- the **scorer**
- the **runtime settings** such as concurrency and output directory

Ready-to-run macro and micro experiments are indexed in [`configs/README.md`](configs/README.md).

## CLI Reference

| Command | Description |
|---|---|
| `alphadiana env` | Check service health before running |
| `alphadiana run <config.yaml>` | Run an evaluation |
| `alphadiana validate <config.yaml>` | Validate a config without running |
| `alphadiana report <results_dir>` | Print Markdown reports from saved result JSONL files |
| `alphadiana batch <config1> <config2> ...` | Run multiple experiments |
| `alphadiana list-benchmarks` | List the complete benchmark registry used by the Runner |

Use `-o key=value` to override config values from the command line (e.g., `-o max_concurrent=4`).

## Results and analysis

AlphaDiana treats a score as a property of the model, harness, task, scorer, environment, and budget together. The [Results](docs/results.md) document presents selected draft tables and process-analysis figures; reports from saved results can be regenerated with `alphadiana report <results_dir>`.

When reviewing a result, start from `score_status`, scorer identity, expected sample count, and the recorded `isolation_mode`. Report Pass@k and Avg@k with the configured `num_samples`; do not compare rows whose runtime or evaluation contracts differ.

## Dashboard

AlphaDiana includes a web dashboard for launching, monitoring, and comparing evaluation runs without manually editing YAML or inspecting raw JSONL files.

Dashboard dependencies are optional and are not installed by `quickstart.sh`:

```bash
pip install -e '.[dashboard]'
cd alphadiana/dashboard/frontend && npm install
```

<p align="center">
  <img src="./docs/assets/dashboard_0.png" width="50%" alt="Dashboard — Results view">
  <img src="./docs/assets/dashboard_2.png" width="47%" alt="Dashboard — New Evaluation">
</p>

### Dashboard features

- browse run history
- compare multiple runs side by side
- launch evaluations through forms
- monitor job progress with real-time logs
- manage ROCK sandboxes

## Documentation

| Document | Description |
|---|---|
| [Welcome](docs/README.md) | Documentation entry point and concept map |
| [Getting Started](docs/getting-started/README.md) | Installation, first run, and troubleshooting |
| [Architecture](docs/architecture/README.md) | Runner, registries, sandboxes, scoring, and observability |
| [Harnesses](docs/harnesses/README.md) | DirectLLM, OpenClaw, OpenCode, and ZeroClaw behavior |
| [Benchmarks](docs/benchmarks/README.md) | Supported benchmark loaders and runbooks |
| [Configuration](docs/configuration/README.md) | YAML schema and CLI override semantics |
| [Results](docs/results.md) | Selected draft tables and process-analysis figures |
| [Dashboard](docs/dashboard.md) | Launch, monitor, browse, and compare runs locally |

## Acknowledgements

AlphaDiana is developed to support research on trustworthy agentic reasoning and reproducible evaluation of reasoning systems. We thank the contributors, collaborators, and open-source communities behind tools and systems that make this work possible, including [OpenClaw](https://github.com/openclaw/openclaw), [ROCK](https://github.com/alibaba/ROCK), and related infrastructure.

## Citation

If you use AlphaDiana in your research, please cite:

```bibtex
@inproceedings{zhou2026reasoning,
  title={Reasoning Is More Than the Model: Harness-Aware Evaluation of Agents on Verifiable Reasoning Tasks},
  author={Zhanke Zhou and Zongze Li and Weikai Huang and Xuan Li and Chentao Cao and Xiao Feng and Xiangyu Lu and Jinbo Hu and Menghan Lu and Yi Xie and Nico Pelleriti and Shiyang Liu and Max Zimmer and Brando Miranda and Jiangchao Yao and Bo Liu and Sanmi Koyejo and Sebastian Pokutta and Bo Han},
  booktitle={3rd AI for Math Workshop: Toward Self-Evolving Scientific Agents},
  year={2026},
  url={https://openreview.net/forum?id=4vARlk9o95}
}
```

## Python API and analysis figures

Use the [composable Python API](docs/getting-started/python-api.md) to execute existing adapters, reopen artifacts, rescore saved outputs, and export analysis figures with data provenance.
