# Python API and analysis

AlphaDiana's Python API uses the same component registries, configuration validator,
Runner, session lifecycle, scoring, and result store as the CLI. It does not replace
native harness control loops.

## Run OpenCode on three AIME tasks

Install with `uv pip install -e '.[analysis,benchmarks,agents]'` in the environment
described in [Installation](installation.md). Set `OPENAI_BASE_URL`,
`OPENAI_API_KEY`, and `OPENAI_MODEL_NAME` for your existing model service.
This is the same example as the repository README. It uses one sample per task,
the default `results/` directory, and OpenCode's default execution settings.

### Build the OpenCode controller image

With a working Podman installation, run this command from the repository root:

```bash
podman build \
  -f alphadiana/harness/opencode/deploy/Containerfile.podman-controller \
  -t localhost/alphadiana-opencode-podman:latest \
  alphadiana/harness/opencode/deploy
```

This builds a local container image from [`Containerfile.podman-controller`](../../alphadiana/harness/opencode/deploy/Containerfile.podman-controller). The recipe starts from Ubuntu 22.04 and installs Node.js 22, OpenCode **1.3.2**, and task tools. The final directory supplies the build context; `-t` assigns the image name `localhost/alphadiana-opencode-podman:latest`.

Use that same image name in `controller_image` below. OpenCode runs inside this controller and calls the configured model endpoint for inference. See the [Podman setup guide](../benchmarks/podman.md) for runtime checks.

### Run and analyze

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

Results and execution evidence are saved under `results/`. The final line performs offline analysis and exports tables and PDF/SVG/PNG figures.

`OPENAI_MODEL_NAME` supplies the execution model when `model` is omitted.
Explicit agent settings take precedence. Each harness keeps its own provider
and gateway boundary.

`export()` defaults to PDF, SVG and PNG; `formats` is an optional override.
Execution and structured analysis do not import Matplotlib. Figure exports
contain `report.json`, source hashes, coverage, plotted data, and `export.json`.
Use a new export directory to preserve earlier evidence.

The default renderer uses gray/blue bars, light grids, and thin axes. It uses
installed Arial, then Arimo, then DejaVu Sans. PDF/SVG are vector exports;
PNG is a review preview. All formats retain the same measured data.

### DirectLLM alternative

For the sandbox-free baseline, replace the agent with
`ad.agent("direct_llm", version="1.0")` and choose a separate run ID.
Use checked-in YAML presets through the CLI when reproducing sampling, thinking,
and logprob settings beyond the API defaults.

The [API acceptance configurations](../../configs/acceptance/api/README.md) provide
28 explicit benchmark/harness combinations. Execute a configuration through the
existing CLI or validation driver; reopen and analyze its artifacts separately.
Model service setup and container installation remain explicit prerequisites.

For a local pipeline check, run:

```bash
python -m pytest -q tests/api/test_api_pipeline_demo.py
```

This uses the actual DirectLLM client against a local HTTP fixture with three
scripted answers. It checks execution, persistence, reopening, rescoring without
another solver call, rerunning without replacing earlier evidence, and PDF/SVG/PNG
exports. Answers, usage and probabilities are synthetic; this does not validate a
real model, the seven-benchmark matrix, native containers or semantic annotations.

## CLI equivalents

The CLI uses the same `compute` and `analyzer` names:

```bash
alphadiana compute configs/examples/aime2026_opencode.yaml
alphadiana analyzer results/aime_opencode_cli.jsonl --output-dir figures/aime_opencode_cli
```

The YAML component sections are `agent`, `data`, `scorer` and optional `env`,
matching `compute(agent=..., data=..., scorer=...)` and `runner(env=...)`.
Run controls stay at the YAML top level or on `ad.runner(...)`.
Legacy `run`, `benchmark` and `sandbox` spellings remain supported.

For structured measurements only, omit `--output-dir`, equivalent to `ad.analyzer(...).compute()`.
For comparisons, pass multiple JSONL paths, equivalent to a list of sources in Python.
Use `--judge` for `judge=True` and `--formats svg` for `formats=("svg",)`.

## Explicit composition

Factories return typed component specifications. Options are copied into the
existing `ExperimentConfig`; specify run-wide options on `runner`, component
options on their factories. Supplying conflicting configuration raises an error.
Choose environments explicitly, for example `ad.environment("podman", image=...)`,
and pass them as `ad.runner(env=...)` where the selected harness supports an
external environment. Native runtimes that own their containers retain that policy.

The API keeps three boundaries explicit:

- `ad.scorer.create(...)` preserves the existing `alphadiana.scorer` Python package
  and all downstream submodule imports. `alphadiana.api.scorer(...)` is also a factory.
- Dataset hub paths require `benchmark=` to select their schema adapter; unknown
  schemas are not guessed from names.
- Harness versions are explicit `version=` values. A version is configuration
  provenance, not an installer or automatic guarantee that a binary matches it;
  validation must record actual runtime versions. Environment backends must name
  a registered backend, not an ambiguous `sandbox` or an unsupported Docker alias.

Custom components register through `BenchmarkRegistry`, `AgentRegistry`,
`SandboxRegistry`, and `ScorerRegistry`. No public-API routing table needs editing.
Existing CLI/YAML configurations and imports remain supported.

`strict_report=True` also applies to Python execution: missing or invalid results
raise an exception after runtime cleanup, with the retained evidence path. Without
strict mode, a partially successful run remains inspectable through `Artifacts`.

## Reopen and rescore

```python
from alphadiana.results.artifacts import Artifacts

saved = Artifacts("results/aime_opencode_demo.jsonl")
all_attempts = saved.records(latest=False)
rescored = saved.rescore(ad.scorer.create("numeric", tolerance=1e-6))
```

The latest attempt for each task/sample is used for analysis; failures are not
hidden by preferring older successes. Rescoring writes a new JSONL, preserves the
original file, and records the source hash and original score. It never runs the
agent. Malformed JSONL is rejected rather than silently losing observations.

New result records carry an `artifact_manifest.attempt_id` and reference retained
copies of their trace, native files and probability files. Repeating the same
task/sample cannot replace those copies. Historical task/sample paths remain
latest-view copies for older scripts, so retention uses additional disk space.
Use declared references (`saved.file(record, key)`) to inspect a particular attempt.
Already-overwritten files from older runs cannot be recovered by this change;
archive legacy runs before reusing their run IDs.

A returned rescore judgment replaces an earlier scoring error, whether the new
answer is correct or incorrect. The original error is retained in
`rescoring.original_error`, alongside the original score status and score
metadata. Solver/provider failures remain disqualifying, and the source files
are unchanged. For Terminal-Bench replay, the fresh verifier result determines
verifier validity while retaining those solver failures.

Environment-backed scorers require `allow_environment=True`. SWE-bench rebuilds
its verifier environment from the saved task and predicted patch through the
existing official scorer. Terminal-Bench runs must opt into
`retain_verifier_snapshot=True` in their agent configuration. This retains a local
immutable container image before verification; keep that image and the original
verifier files while rescoring is required. Replay checks image identity and the
verifier digest, uses fresh logs, runs only the verifier, and cleans up its runtime.
Missing snapshots or changed verifier inputs fail explicitly. Snapshot images are
private execution evidence, not public report assets.

For native `terminus2`, also set `python_executable` to the Python executable of
your Harbor installation. With `retain_verifier_snapshot=True`, its native
pre-verification callback captures the Docker task state; Harbor still owns the
solver, verifier and container lifecycle. Other Harbor environment backends are
rejected for snapshot capture. The existing replay path restores the retained
image and invokes only the verifier.

## Optional historical data adapter

This optional operator requires a source checkout; it is not installed in the
`alphadiana` wheel. The paper-table loader reads frozen numerical inputs without running
legacy scripts, changing archived files, or embedding result constants in code:

```python
from scripts.paper_analysis.load_figure_tables import PaperFigureTables

archive = PaperFigureTables("/path/to/paper-figures")
paper = ad.analyzer([], plugins=[archive])
paper.export("figures/archive")
```

It renders macro/interactive accuracy; overlapping error incidence; action
composition/transitions; entropy/length and archived trajectory-position entropy; macro efficiency;
matched tool/skill/memory interventions; library access; memory accumulation;
solution cost and unsuccessful-token allocation. Historical input hashes and its
status as historical evidence are retained. These plots do not certify new runs.
Architecture diagrams, screenshots, and exact manuscript typography are outside
empirical analysis. Labels, units, and measured values—not pixels—are preserved.

## Extend analysis

An `AnalysisPlugin` has a name and `measure(artifacts) -> Measurement`. A measurement
contains tables, declarative `Figure` objects, and coverage/provenance. Renderers
consume those tables separately, so adding a measurement cannot change execution
or scoring. `TableAnalysis` accepts existing measurement tables with explicit
source provenance. Model-based action and failure plugins require explicit opt-in. The default
offline pipeline computes scores, resources, and entropy without action labels.


## Built-in measurements

Default analysis is offline and reads saved artifacts. It includes:

| Measurement | Tables and figures | Required evidence |
|---|---|---|
| Performance and reliability | Accuracy, empirical Pass@k and Avg@k | Scores, expected tasks and configured repeat count |
| Resource usage | Mean output tokens and elapsed seconds per observed attempt; cost per correct answer | Provider/native usage counters and elapsed time |
| Process entropy | Outcome-conditioned post-tool entropy with standard-error bands; log10 output length versus mean entropy | Observed top-K probabilities; request attribution for post-tool curves; measured usage for length |

Pass@k is the fraction of expected tasks with at least one successful attempt;
Avg@k averages successes over the configured k attempts per task. Missing attempts
and execution/scoring failures do not count as successes. These are empirical
run statistics, not a subsampling estimator. Resource means use only measured
attempts; token and timing coverage are reported separately. Missing usage is
not a zero cost. Three-task checks demonstrate functionality, not benchmark rankings.

`EntropyAnalysis` aligns position 1 to the first token of each request explicitly
marked as following tool feedback. Geometric bins use a ratio of 2 by default,
plot their geometric midpoints on a logarithmic x-axis, and require at least five
observations per run/outcome/bin. The y-axis is renormalized observed top-K entropy
in nats, not full-vocabulary entropy. Values are token-weighted; bands show
mean ± descriptive standard error, not confidence intervals (tokens are correlated).
All bins, boundaries, token/call counts and suppressed-bin counts remain available.

The length scatter uses log10 **measured output tokens**, not the number of captured
probability records, against mean entropy over observed probability records.
A two-standard-deviation covariance ellipse is drawn only for groups with at
least ten samples; it is not a confidence region for the mean. Incomplete capture
remains visible in coverage. Direct inference has no post-tool curve. Legacy
sidecars without explicit attribution cannot supply one. The older `RequestEntropy`
plugin remains available with its distinct equal-call, fixed-width-bin statistics.

## Optional model judgments

Install `pip install -e '.[judges]'` for model annotations and the `llm_judge` scorer.
Execution and analysis may use separate models, endpoints and credentials.
Configure the execution provider through `OPENAI_BASE_URL`, `OPENAI_API_KEY` and
`OPENAI_MODEL_NAME` (or an explicit agent `model`). For independent analysis:

```bash
export ANALYSIS_MODEL="your-judge-model"
export ANALYSIS_BASE_URL="https://your-judge-provider.example/v1"
export ANALYSIS_API_KEY="your-judge-api-key"
# Optional immutable revision for provenance:
export ANALYSIS_MODEL_VERSION="your-judge-revision"
```

```python
report = ad.analyzer(saved, judge=True)
report.export("figures/annotated-run")
```

This adds action and failure annotations to the default offline measurements.
The Analyzer creates and closes its judge clients for each computation, including
error paths. Responses are cached under `$XDG_CACHE_HOME/alphadiana/annotations`
(or `~/.cache/alphadiana/annotations`); override with `cache_dir=` when needed.

Explicit analysis settings override environment defaults and enable annotations:

```python
report = ad.analyzer(saved, model="another-judge", base_url="https://judge.example/v1")
report.export("figures/another-judge")
```

Analysis precedence is explicit `model/base_url/api_key` → corresponding
`ANALYSIS_*` variable → existing judge fallback (`OPENAI_MODEL`, then
`OPENAI_MODEL_NAME`, then `JUDGE_MODEL`; `OPENAI_BASE_URL`, then `JUDGE_API_BASE`;
`OPENAI_API_KEY`). Each field resolves independently. Configure `ANALYSIS_API_KEY`
when the analysis provider uses a different credential. Merely setting environment
variables does not enable annotations: `ad.analyzer(saved)` remains offline.
There is no built-in judge endpoint/model/credential; missing required values fail
at construction. The scoring `llm_judge` remains independent and ignores
`ANALYSIS_*`; use its existing explicit `judge_model/api_base/api_key` settings
for a separate grading provider.

For custom taxonomies, provider options or individual plugins, retain the explicit
plugin interface below. Supply either `plugins=` or Analyzer judge options; mixing
them raises rather than silently overriding plugin configuration. Caller-created
plugins retain their own lifecycle:

```python
from alphadiana.analysis.pipeline import ExecutionMetrics
from alphadiana.analysis.measurements import EntropyAnalysis
from alphadiana.analysis.actions.llm_annotations import ActionAnnotations
from alphadiana.analysis.failure_annotations import FailureAnnotations

execution = ExecutionMetrics()
entropy = EntropyAnalysis(bin_ratio=2, min_observations=5)
with ActionAnnotations(cache_dir="analysis-cache/actions") as actions:
    with FailureAnnotations(cache_dir="analysis-cache/failures") as failures:
        plugins = [execution, entropy, actions, failures]
        report = ad.analyzer(saved, plugins=plugins)
        report.export("figures/annotated-run")
```

Action annotation now defaults to **LLM-selected semantic boundaries and labels
over complete saved trajectories** (`llm-trajectory-actions-v1`). Both
`ActionAnnotations(...)` and `ad.analyzer(saved, judge=True)` use this method.
The normal case makes one judge request per trajectory, not one per action.
No `action_targets` artifact or rule-based split is required.

The classifier reuses the definitions and operational rules from the canonical
[human protocol](../../alphadiana/analysis/actions/action_prompt.md), including the priority
Finalization > Tool Use > Verification > Reasoning > Planning > Understanding
between functions actually evidenced by a span. It does not use keyword labels.
The model chooses ranges inside logged text; Python validates ordered, complete,
nonoverlapping Unicode source ranges. Actual logged calls remain indivisible;
user/system messages and tool observations are context, not targets. Reports save
exact selected text, source pointers, offsets, trace hashes, action IDs, request
digests and labels. The raw trace and result index are never rewritten.

Input defaults to a conservative 200,000-token budget, 262,144-token context and
16,384 output tokens. **Set `context_window` and `output_budget` to the judge's
documented capacity** when using a different model. The estimator uses UTF-8
bytes plus framing allowance, an upper bound for byte-based tokenizers rather
than a measured provider token count. Large requests are windowed only between
logged source blocks with context-only overlap. An indivisible source that does
not fit fails explicitly; no text is truncated or divided by character rules.
Malformed/incomplete model output fails the analysis instead of producing a
partial action count or falling back to rules. Retry is explicit; valid cache
entries are reused only for the exact source, prompt, model and settings.

For the original fixed-target human audit only, explicitly select
`ActionAnnotations(protocol="human-protocol-v2", cache_dir="audit-cache")`.
That mode still requires the trace-bound `action_targets` artifact, with
`schema_version: 1`, `trace_sha256`, and `items` containing the original `item_id`,
`full_text`, `context_text`, `following_text`, and optional `sequence_index`.
Do not resegment the original human targets when reproducing that comparison.

Historical character segmentation is retained in
`analysis/actions/llm_annotations.py`; the supported explicit `custom-spans-v1`
option writes `legacy_custom_annotated_actions`. Historical keyword labels remain
available at `analysis/actions/keyword_baseline.py` for old scripts. Neither is called by
the standard Analyzer pipeline. Analyzer report schema v2 removes the old `execution.actions`,
`execution.action_composition` and `execution.action_transitions` tables and figures.
Use `annotated_actions` for the new measurement; keep historical exports unchanged.
Offline analysis without a judge still computes
scores/resources/entropy, but produces no substitute action labels or action plots.

To run the retained character method explicitly, use your historical taxonomy
and an independent output directory:

```python
from alphadiana.analysis.actions.llm_annotations import ActionAnnotations

with ActionAnnotations(
    protocol="custom-spans-v1",
    span_chars=4096,
    taxonomy=historical_taxonomy,
    model=judge_model,
    base_url=judge_base_url,
    api_key=judge_api_key,
    cache_dir="historical-character-cache",
) as method:
    result = method.measure([artifacts])
```

This retains the old rule for choosing boundaries and still uses a model for
labels. For the separate, fully deterministic historical method, explicitly call
`extract_action_event_rows(bundle, harness="directllm")` from
`alphadiana.analysis.actions.keyword_baseline`; `bundle` is a `RunBundle` loaded through
`alphadiana.results.reader.load_run_bundle`. Neither method replaces the
LLM default. Compare their outputs as different measurements.

All new action reports declare `calibration_inherited: false`. Sharing six label
names or classification rules does not transfer agreement measured on different
boundaries, models or data. The LLM segmentation needs its own inspected pilot;
the original fixed-target agreement study does not validate it.

Failure annotations assign overlapping labels to unsuccessful or unscored traces.
The default 17 categories cover task/interface, reasoning, tool interaction,
verification and operational failures; custom taxonomies use `taxonomy={code: definition}`.
Each positive label requires an exact quote from the supplied trace. Percentages
use successfully annotated eligible traces as the denominator and can sum above
100%. Missing traces are excluded and reported, never treated as negative labels.
Oversized traces fail explicitly at `max_trace_chars`; they are not silently truncated.
An exact quote checks evidence presence, not whether the model's interpretation is
correct. Model labels require human sampling before scientific conclusions.

Persistent caches bind the endpoint, model/version, full evidence, prompt,
generation settings and response format. They retain response ID/model and usage,
but not the API key. Keep caches private if task traces contain private material.
Malformed, unsupported or incomplete responses fail explicitly; cached answers
are revalidated. A model ID alone is not an immutable weight revision.

Providers default to JSON-object output. Pass `response_format` for a provider's
supported structured-output schema and `extra_body` for explicit provider options.
Use a single `label` string for action responses; failure responses require a
`labels` array of `{code, quote}` objects. Schema enforcement does not replace
validation or human review. The scoring judge requires an actual boolean
`correct` and a string `rationale`; strings such as `"false"` are rejected.

Rescored artifacts refer to their original sidecars. Keep both directories when
reopening a derived score file. Binary attachments are verified by content hash.

## Readable figure labels

Run IDs remain evidence identifiers. A presentation plugin can use shorter labels
without renaming artifacts or changing measured values:

```python
from copy import deepcopy
from alphadiana.analysis.pipeline import ExecutionMetrics
from alphadiana.analysis.measurements import EntropyAnalysis

class DisplayLabels:
    def __init__(self, plugin, labels):
        self.plugin, self.name = plugin, plugin.name
        self.labels = dict(labels)

    def measure(self, artifacts):
        result = deepcopy(self.plugin.measure(artifacts))
        for rows in result.tables.values():
            for row in rows:
                original = row.get("run")
                if original not in self.labels:
                    continue
                row["run"] = self.labels[original]
                series = row.get("series")
                if isinstance(series, str) and series.startswith(original + " / "):
                    row["series"] = self.labels[original] + series[len(original):]
        result.coverage["display_labels"] = self.labels
        return result

names = {saved.path.stem: "OpenClaw"}
plugins = [
    DisplayLabels(ExecutionMetrics(), names),
    DisplayLabels(EntropyAnalysis(), names),
]
report = ad.analyzer(saved, plugins=plugins)
report.export("figures/readable-labels")
```

The label map accompanies the report. For a multi-run comparison, supply all
artifact objects and one label per run. Run file stems and display labels must be distinct so different runs
cannot be merged visually.

## Measured resource usage

Efficiency analysis prefers persisted output-token usage. If the gateway omits
that summary, it can read the retained selected OpenClaw v3 task session offline.
It verifies that the session contains the task text, requires unique assistant
message IDs and complete native usage on every assistant message, and checks the
native input/output/cache totals before aggregation. It does not sum all sessions
in the runtime directory: startup probes and unrelated sessions are excluded.
Each recovered sample includes the source path/hash, request count and native
totals in `usage_provenance`. Original result records remain unchanged.

OpenCode usage can likewise be read from the retained event stream. The saved
prompt must contain the task, session identities must agree, and native step
starts/finishes must pair completely. Output counts retain the native meaning;
reasoning/cache fields are preserved as provenance, not added again to output.

Missing or unverified session data remains unavailable; partial sums are not
presented as total task cost. Native output counts and captured logprob counts
are separate measurements, and request attribution coverage applies only to
observed probability records. It does not assert complete output-token capture.

Additional usage schemas can be supplied as callable extractors through
`ExecutionMetrics(usage_extractors=[...])` and
`EntropyAnalysis(usage_extractors=[...])`. An extractor receives `(artifacts,
record)` and returns `MeasuredUsage(output_tokens, provenance)`, or `None` when
its schema does not apply. Supply an empty list to use only persisted usage.

Native SWE-Agent and Terminus adapters accept `capture_logprobs: true` and
`top_logprobs` for OpenAI-compatible providers that support probability output.
This observes requests around the upstream subprocess without replacing its tool
loop. Capture defaults to off. Missing provider probabilities remain missing;
observed top-K entropy is not full-distribution entropy. Terminus terminal-feedback
attribution requires exact matches to saved native command observations; unmatched
requests are not inferred from their user role alone.

SWE-Agent's native text-token estimate is retained in metadata. Measured efficiency
uses complete provider response usage collected with capture enabled; tool-call
output is included according to provider counters. Incomplete usage is reported as
missing, including when capture is disabled.

## Native OpenClaw images

Image-bearing OpenClaw tasks use its native `/v1/responses` endpoint. The pinned
2026.3.2 Chat Completions endpoint extracts text only and has a 1 MiB body limit.
The adapter preserves base64 image bytes and uses native OpenResponses image
source objects, retaining the completed native response alongside the canonical
projection. Failed/incomplete responses remain errors even if partial text was
streamed. Text-only requests retain their existing Chat Completions path.
Managed gateways enable Responses by default (`responses_enabled: false` can
disable it); external gateways must enable that endpoint to accept images.
