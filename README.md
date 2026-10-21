# AlphaDiana

**Measure LLM agents on verifiable reasoning tasks.** Compare scores, inspect trajectories, and test tools, skills and memory.

AlphaDiana runs configured agents against benchmarks, scores their outputs, and saves the evidence needed to analyze each attempt.

[**Scope**](#scope-at-a-glance) | [**Quick Start**](#quick-start) | [**Python API**](#python-api) | [**Analyze**](#analysis-and-outputs) | [**Docs**](#documentation)

## Positioning

Changing the harness can change the behavior of the same model. AlphaDiana compares the complete agent and connects score differences to its execution evidence.

![Position of AlphaDiana](docs/assets/alphadiana-motivation.png)

*A model, its harness and its execution settings jointly shape agent behavior.*

## Why AlphaDiana?

A **harness** controls how a model uses prompts, tools and persistent state.

- **Compare agents:** run different model–harness combinations on the same tasks and compare their scores and resource use.
- **Inspect failures:** connect wrong answers to execution status, tool interactions and action patterns in saved trajectories.
- **Test components:** change tools, skills or memory settings and compare outcomes under recorded task and budget settings.

![AlphaDiana measurement examples: benchmark scores, failure modes, output-token usage, and component ablations.](docs/assets/alphadiana-overview.png)

*The upper panel shows how execution produces evidence. The lower panels use paper experiments to illustrate scores, failure-label incidence, output tokens by outcome, and tool-removal effects. Each plot labels its model, benchmark and metric.*

## Scope at a glance

Read the table from experiment setup to analysis and extension. Linked rows lead to configuration options, runtime requirements and examples.

| Layer | Dimension | Options |
|---|---|---|
| **CONFIGURE** | Models | Qwen3.5-27B, Gemma-4-31B-IT, Kimi-K2.6 (paper)<br>Configurable OpenAI-compatible endpoints |
| | [Harnesses](docs/harnesses/README.md) | OpenClaw, OpenCode, ZeroClaw, Pi, DeepSeek Harness<br>SWE-Agent (SWE-bench), Terminus2 (Harbor/TB2), DirectLLM baseline |
| | [Components](configs/micro_runs/README.md) | **Tools**: sets, filters, removal<br>**Skills**: math/general libraries<br>**Memory**: within-task, cross-sample, cross-task<br>**Prompts/context**: instructions, native compaction |
| | [Containers](docs/architecture/sandboxes.md) | **None, Docker, Podman**<br>Host/process, ROCK-managed or benchmark-specific environments |
| **RUN** | [Benchmarks](docs/benchmarks/README.md) | AIME 2026, GPQA-Diamond, HLE, IMO-AnswerBench<br>MMMU-Pro, SWE-bench Verified, Terminal-Bench 2, Custom |
| | [Controls](docs/configuration/README.md) | Task selection, Repeats, Sampling, Token/time/tool budgets<br>Concurrency, Retries, Resume, State isolation |
| | [Interfaces](docs/getting-started/python-api.md) | **CLI/YAML and Python API**: run, batch, rescore, analyze |
| **MEASURE** | [Measurements](docs/getting-started/python-api.md#built-in-measurements) | **Outcomes**: accuracy, Pass@k, Avg@k, status, failure labels |
| | | **Resources**: output tokens, wall time, cost per correct answer |
| | | **Process**: action composition/transitions, token/post-tool entropy |
| | | **Components**: score and behavior comparisons |
| | [Scoring](docs/architecture/registries.md#scorers) | Exact/numeric match, Math equivalence, Model judge<br>IMO, SWE-bench and Terminal-Bench verifiers |
| | [Evidence & exports](docs/getting-started/python-api.md) | JSONL, Traces, Tool calls, Usage, Available logprobs/artifacts<br>Markdown, Tables, PDF/SVG/PNG, Source hashes & coverage |
| **EXTEND** | [Plugins](docs/architecture/registries.md) | Harnesses, Benchmarks, Environments, Scorers, Metrics<br>[Saved-run analysis plugins](docs/getting-started/python-api.md#optional-model-judgments) |

<details>
<summary>Runtime combinations & measurement requirements</summary>

- Select supported combinations in the [API acceptance matrix](configs/acceptance/api/README.md) and harness guides.
- **None** means a direct API call or a supported host/process runtime.
- **Entropy** uses captured probabilities; post-tool curves require tool-feedback attribution.
- **Action/failure labels** require an explicitly enabled analysis model. Execution and analysis can use separate models.
- Read **Pass@k / Avg@k** with the configured repeat count; compare runs with matched scorer, isolation and budget settings.

</details>

## Quick Start

**Run:** one AIME 2026 task with DirectLLM.<br>
**Requires:** Qwen3.5-27B endpoint, Python ≥ 3.10.

This example uses a direct model call without a tool loop or task container. It produces one scored attempt and a saved result you can analyze afterward.

### 1. Install

Create an isolated Python environment and install the execution and analysis dependencies:

```bash
git clone https://github.com/tmlr-group/AlphaDiana.git
cd AlphaDiana
python -m venv .venv
source .venv/bin/activate
pip install -e '.[analysis,benchmarks,agents]'
```

### 2. Test the connection of the model

Replace the URL and key with your provider's values. The model name must match an ID served by that endpoint.

```bash
export OPENAI_BASE_URL="https://your-provider.example/v1"
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL_NAME="Qwen/Qwen3.5-27B"
```

This preset requires streaming, logprobs and `extra_body.chat_template_kwargs.enable_thinking` support.

<details>
<summary>Preset settings / another model</summary>

The preset uses `temperature=0.0`, `top_p=0.95` and a 32768-token output budget. Set `OPENAI_MODEL_NAME` to your endpoint's served model ID. For another endpoint, select or adapt a preset through the [Configuration guide](docs/configuration/README.md).

</details>

### 3. Configure and run

```bash
alphadiana validate configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o benchmark.config.max_tasks=1
alphadiana run configs/macro_runs/aime2026_directllm_qwen35_27b.yaml \
  -o run_id=quickstart_aime_directllm_t1_k1 \
  -o benchmark.config.max_tasks=1 \
  -o num_samples=1
```

`validate` checks configuration locally; `run` loads tasks and calls the model endpoint.

The overrides select one task and one sample, then assign a recognizable run ID to the output.

### 4. Read results

```bash
alphadiana report results/
```

**Saved run:** `results/quickstart_aime_directllm_t1_k1.jsonl`<br>
**Check:** score, `score_status` and execution status. [Troubleshooting →](docs/getting-started/troubleshooting.md)

The report summarizes saved attempts. Check status alongside the score to distinguish an incorrect answer from an execution or scoring failure.

## Python API

**OpenCode → 3 AIME tasks → scores, traces and figures.** Use the endpoint variables above.

The Python API lets you select the agent, dataset and scorer separately, then pass them to the runner. This example runs OpenCode inside the Podman controller built below.

<details>
<summary><strong>Setup required:</strong> build the OpenCode controller image with Podman</summary>

Run from the repository root with Podman installed:

```bash
podman build \
  -f alphadiana/harness/opencode/deploy/Containerfile.podman-controller \
  -t localhost/alphadiana-opencode-podman:latest \
  alphadiana/harness/opencode/deploy
```

The image contains OpenCode **1.3.2** and task tools. Its tag must match `controller_image` below. [Podman setup →](docs/benchmarks/podman.md)

</details>

```python
import alphadiana as ad

agent = ad.agent(
    "opencode", version="1.3.2", controller_mode="podman",
    controller_image="localhost/alphadiana-opencode-podman:latest",
)
data = ad.dataset("MathArena/aime_2026", benchmark="aime", max_tasks=3)
scorer = ad.scorer.create("numeric")

artifacts = ad.runner(run_id="aime_opencode_demo").compute(
    agent=agent, data=data, scorer=scorer,
)

ad.analyzer(artifacts).export("figures/aime_opencode_demo")
```

**Results:** `results/`<br>
**Exports:** tables + PDF/SVG/PNG.

`compute()` executes and scores the tasks, then returns references to the saved artifacts. `ad.analyzer(artifacts).export(...)` reads those artifacts and generates measurement tables and figures.

![AlphaDiana functional interface: agent, dataset, environment, scorer, runner, and analysis composition.](docs/assets/alphadiana-interface.png)

*The paper's functional interface connects experiment components to analysis outputs. [Figure sources](docs/assets/README.md).*

<details>
<summary>API composition details</summary>

Use the current API shown above: `ad.scorer.create(...)`, explicit harness `version=`, and `benchmark=` for dataset hub paths. OpenCode owns its Podman controller. Harnesses accepting a separate environment use `ad.environment(...)` and `ad.runner(env=...)`.

[Full API guide: compose, reopen, rescore and extend →](docs/getting-started/python-api.md)

</details>

## Analysis and outputs

**Already have a saved run? Export measurements without rerunning the agent.**

Default analysis reads saved scores, usage counters and available probability evidence. It computes performance, resource use and entropy where the required evidence is present.

```python
import alphadiana as ad

ad.analyzer("results/quickstart_aime_directllm_t1_k1.jsonl").export(
    "figures/quickstart_aime"
)
```

**Outputs:** tables, figures, plotted data, source hashes and evidence coverage.

Coverage records show which measurements have supporting evidence. For example, a run can report accuracy and output tokens even when logprobs are unavailable for entropy.

**Need action or failure labels?** Enable the optional analysis model below to annotate saved trajectories.

<details>
<summary>Add action & failure labels: optional model calls</summary>

Configure the analysis model, then enable `judge=True`:

```bash
export ANALYSIS_BASE_URL="https://your-provider.example/v1"
export ANALYSIS_API_KEY="your-api-key"
export ANALYSIS_MODEL="your-analysis-model-id"
```

```python
ad.analyzer(
    "results/quickstart_aime_directllm_t1_k1.jsonl", judge=True
).export("figures/quickstart_aime_annotated")
```

[Measurement requirements and annotation caching →](docs/getting-started/python-api.md#built-in-measurements)

</details>

## Documentation

1. **Configure:** select tasks, a model and harness, execution environments, a scorer and budgets.
2. **Execute & score:** the runner coordinates task sessions; the native harness runs the agent, and the scorer evaluates its output.
3. **Save evidence:** result records link scores and execution status to retained outputs, traces and resource measurements.
4. **Analyze & export:** reopen those records to compare runs, inspect behavior and produce tables and figures.

![AlphaDiana system design: configuration, runner, session management, native agent runtimes, inference endpoints, and artifact analysis.](docs/assets/alphadiana-system.png)

*The runner coordinates sessions and native harnesses. Saved outputs, traces and resource measurements connect execution to scoring and analysis.*

[Architecture →](docs/architecture/README.md)

<!-- Temporarily hidden until the dashboard includes analyze content and interactions.
## Dashboard

The dashboard lets you launch evaluations, monitor job logs, inspect trajectories, and compare saved runs. From the repository root, with your Python environment active:

```bash
pip install -e '.[dashboard]'
cd alphadiana/dashboard/frontend
npm install
cd ../../..
cd alphadiana/dashboard
./run.sh
```

Open the frontend URL printed by the launcher. See [Dashboard](docs/dashboard.md) for production mode, data directories, and supported ROCK sandbox controls.

<p align="center">
  <img src="./docs/assets/dashboard_0.png" width="50%" alt="Dashboard: New Evaluation">
  <img src="./docs/assets/dashboard_2.png" width="47%" alt="Dashboard: Results view">
</p>

*Configure a run in New Evaluation (left) and inspect saved runs in the Results view (right). The dashboard connects run setup, monitoring, and result inspection.*
-->

| I want to… | Start here |
|---|---|
| **Run** an experiment | [Getting Started](docs/getting-started/README.md), [Configuration](docs/configuration/README.md), [Presets](configs/README.md) |
| **Choose** a harness or benchmark | [Harnesses](docs/harnesses/README.md), [Benchmarks](docs/benchmarks/README.md) |
| **Analyze** saved runs | [Python API](docs/getting-started/python-api.md), [Paper results](docs/results.md) |
| **Extend** the system | [Architecture](docs/architecture/README.md), [Analysis modules](alphadiana/analysis/README.md), [Scripts](scripts/README.md) |

<details>
<summary>CLI reference</summary>

| Command | Description |
|---|---|
| `alphadiana env` | Check configured service health |
| `alphadiana run <config.yaml>` | Run an evaluation |
| `alphadiana validate <config.yaml>` | Validate a config locally |
| `alphadiana report <results_dir>` | Print Markdown reports from saved result JSONL files |
| `alphadiana batch <config1> <config2> ...` | Run multiple experiments |
| `alphadiana list-benchmarks` | List the benchmark registry used by the Runner |

Use `-o key=value` to override configuration values, for example `-o max_concurrent=4`.

</details>

<details>
<summary>Repository map</summary>

```text
alphadiana/
  cli/           # command-line entry points
  engine/        # execution, configuration, and rollout campaigns
  harness/       # agent implementations and runtime skills
  benchmarks/    # task loaders and official verifier integrations
  scorer/        # answer evaluation and scorer interfaces
  results/       # persistence, traces, score status, and sample statistics
  analysis/      # measurement plugins, Analyzer composition, and exports
  dashboard/     # React frontend and FastAPI backend
scripts/
  setup/         # environment and dependency setup
  validation/    # preflight, smoke tests, and evaluator audits
  action_audit/  # human action annotation and agreement
  paper_analysis/ # archived paper inputs and plotting workflows
tests/           # grouped by analysis, API, engine, harness, and benchmark
configs/         # experiment configurations
docs/            # guides, contracts, and documentation assets
```

</details>

## Citation and acknowledgement
If you find the project helpful, please cite:

```bibtex
@article{zhou2026alphadiana,
  title = {{AlphaDiana}: A Measurement System for Agents in Verifiable Reasoning Tasks},
  author={Zhanke Zhou and Zongze Li and Weikai Huang and Xuan Li and Chentao Cao and Xiao Feng and Xiangyu Lu and Jinbo Hu and Menghan Lu and Yi Xie and Nico Pelleriti and Shiyang Liu and Max Zimmer and Brando Miranda and Jiangchao Yao and Bo Liu and Sanmi Koyejo and Sebastian Pokutta and Bo Han},
  journal = {arXiv preprint arXiv:2610.20675},
  year = {2026}
}
```

We thank the contributors, collaborators and open-source communities behind [OpenClaw](https://github.com/openclaw/openclaw), [ROCK](https://github.com/alibaba/ROCK) and the other tools that support AlphaDiana.

## Related Project

Please also check out [AlphaApollo](https://alphaapollo.org/), our system for deep agentic reasoning ([arXiv](https://arxiv.org/abs/2510.06261), [GitHub](https://github.com/tmlr-group/AlphaApollo)).
