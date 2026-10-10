# AlphaDiana: A System for Evaluating Agentic Reasoning

**AlphaDiana** is an open-source framework for **system-level evaluation of LLM-based reasoning agents**. It enables standardized, reproducible benchmarking of agentic systems—such as **[OpenClaw](https://github.com/openclaw/openclaw)**—on frontier reasoning tasks, with support for sandboxed code execution, multi-turn tool use, and full trajectory logging.

## Why AlphaDiana?

Reasoning performance in agent systems depends on more than the base model alone. It is also shaped by the **agent framework, tool interface, execution environment, and evaluation protocol**.

AlphaDiana is designed for **fair, reproducible, and transparent** evaluation of agentic reasoning systems.

With AlphaDiana, you can:
- evaluate **OpenClaw-style reasoning agents**
- compare against a **direct LLM baseline**
- run evaluations with **sandboxed execution**
- benchmark on **AIME 2026, GPQA-Diamond, HLE, IMO-AnswerBench, MMMU-Pro,
  SWE-bench Verified, Terminal-Bench 2, and custom tasks**
- record and inspect **full execution traces**
- launch and compare runs through both **CLI and dashboard**

Typical questions AlphaDiana helps answer:
- How much does an agent improve over the base model?
- How well does it perform on a target benchmark?
- How much do tools and sandbox settings affect results?
- What failure modes appear in execution traces?

## Key Features

- **Standardized evaluation** for reproducible benchmarking of agentic reasoning systems  
- **OpenClaw support** for multi-turn reasoning with tool use and code execution  
- **ROCK sandbox integration** for safe, isolated execution  
- **Direct LLM baseline** for clean agent-vs-model comparison  
- **Built-in benchmarks** covering AIME 2026, GPQA-Diamond, HLE,
  IMO-AnswerBench, MMMU-Pro, SWE-bench Verified, Terminal-Bench 2, and custom tasks
- **Full trace logging** for debugging, inspection, and analysis  
- **Web dashboard** for launching, monitoring, and comparing runs  
- **Automatic sandbox management** with configurable concurrency

## Quick Start

### Install and configure

Python 3.10 or newer is required. Container runtimes are optional for direct
inference; native agent harnesses have their own runtime prerequisites.

```bash
git clone https://github.com/tmlr-group/AlphaDiana.git
cd AlphaDiana
python -m venv .venv
source .venv/bin/activate
pip install -e '.[analysis,benchmarks,agents]'

export OPENAI_BASE_URL="https://your-provider.example/v1"
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL_NAME="your-model-id"
```

LLM judges also require an explicitly configured endpoint, key and model.
`OPENAI_MODEL` takes precedence over `OPENAI_MODEL_NAME` for judging. Offline
analysis requires no model service. Optional Analyzer judgments can use separate
`ANALYSIS_MODEL`, `ANALYSIS_BASE_URL` and `ANALYSIS_API_KEY` settings. See [Python API and analysis](docs/getting-started/python-api.md)
for composition, saved-run analysis and optional model annotations.

### Run an evaluation

```bash
alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o benchmark.config.max_tasks=1
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=quickstart_aime_directllm_t1_k1 \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1
```

This DirectLLM path does not require ROCK. A successful provider call writes one
scored task record; the accuracy depends on the model response and is not fixed.

### Use the Python API

The same evaluation pipeline is available from Python on this branch:

```python
import alphadiana as ad

agent = ad.agent("direct_llm", version="1.0")
data = ad.dataset("MathArena/aime_2026", benchmark="aime", max_tasks=3)
scorer = ad.scorer.create("numeric")
artifacts = ad.runner(run_id="python_demo").compute(
    agent=agent, data=data, scorer=scorer,
)
ad.analyzer(artifacts).export("figures/python_demo")
```

This uses the provider environment settings above. Results and execution evidence
are saved under `results/`; analysis exports include tables and PDF/SVG/PNG figures.
See [Python API and analysis](docs/getting-started/python-api.md) for reopening,
rescoring and comparing saved runs. Native harnesses still require their own
container/runtime configuration.

### Print a report

```bash
alphadiana report results/
```

`alphadiana report` prints Markdown to standard output. Redirect it when you
want a file, for example: `alphadiana report results/ > report.md`.

For a full walkthrough, see [Getting Started](docs/getting-started/quick-start.md).
For the documentation entry point, see [Welcome to AlphaDiana](docs/README.md).
For Podman-backed paths, start from the matching page under [Benchmarks](docs/benchmarks/README.md).
For manual setup and recovery, see [Installation](docs/getting-started/installation.md)
and [Troubleshooting](docs/getting-started/troubleshooting.md).
For ZeroClaw, see the [harness guide](docs/harnesses/zeroclaw.md) and
[AIME benchmark page](docs/benchmarks/aime.md).

## Repository map

```text
alphadiana/
  engine/        # execution, configuration, and rollout campaigns
  harness/       # agent implementations and runtime skills
  benchmarks/    # task loaders and official verifier integrations
  analysis/      # saved-run measurements, annotations, and reports
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

See the [analysis module map](alphadiana/analysis/README.md) and
[operator workflow map](scripts/README.md) for implementation entry points.

## Configuration

AlphaDiana is configured with a YAML file. At a high level, you specify:

- the **agent**
- the **benchmark**
- the **scorer**
- the **runtime settings** such as concurrency and output directory

Ready-to-run macro and micro experiments are indexed in
[`configs/README.md`](configs/README.md).

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

AlphaDiana treats a score as a property of the model, harness, task, scorer,
environment, and budget together. The [Results](docs/results.md) document presents
selected draft tables and process-analysis figures; reports from saved results
can be regenerated with `alphadiana report <results_dir>`.

When reviewing a result, start from `score_status`, scorer identity, expected
sample count, and the recorded `isolation_mode`. Report Pass@k and Avg@k with
the configured `num_samples`; do not compare rows whose runtime or evaluation
contracts differ.

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
