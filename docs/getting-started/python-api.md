# Python API and analysis

AlphaDiana's Python API uses the same component registries, configuration validator,
Runner, session lifecycle, scoring, and result store as the CLI. It does not replace
native harness control loops.

```python
import os
import alphadiana as ad

api_base = os.environ["OPENAI_BASE_URL"]
api_key = os.environ["OPENAI_API_KEY"]

agent = ad.agent(
    "direct_llm", version="1.0", model=os.environ["OPENAI_MODEL"],
    api_base=api_base, api_key=api_key,
    max_tokens=8192, capture_logprobs=True, top_logprobs=20,
)
data = ad.dataset("MathArena/aime_2026", benchmark="aime", split="train", max_tasks=3)
scorer = ad.scorer.create("numeric", tolerance=1e-6)
runner = ad.runner(output_dir="results", run_id="example", num_samples=1)

artifacts = runner.compute(agent=agent, data=data, scorer=scorer)
report = ad.analyzer(artifacts)
metrics = report.compute()
report.export("figures/example", formats=("pdf", "svg", "png"))
```

Set the endpoint variables for an existing OpenAI-compatible model service.
Install the example dependencies with `pip install -e '.[analysis,benchmarks,agents]'`. Execution and
structured analysis do not import Matplotlib. Figure exports contain `report.json`,
source hashes, coverage, each figure's exact plotted data, and `export.json` listing
outputs and inapplicable plots. Export to a new directory to preserve prior evidence.
The default renderer uses a compact gray/blue style with horizontal
performance bars, light grids and thin axes. It uses installed Arial, then Arimo,
then DejaVu Sans; no proprietary font or machine-specific font path is required.
PDF/SVG are vector exports; PNG is a review preview. All formats retain the same
measured data, and line styles/markers distinguish overlapping series.

The [API acceptance configurations](../../configs/acceptance/api/README.md) provide
28 explicit benchmark/harness combinations. Execute a configuration through the
existing CLI or validation driver; reopen and analyze its artifacts separately.
Model service setup and container installation remain explicit prerequisites.

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

## Reopen and rescore

```python
from alphadiana.artifacts import Artifacts

saved = Artifacts("results/example.jsonl")
all_attempts = saved.records(latest=False)
rescored = saved.rescore(ad.scorer.create("numeric", tolerance=1e-6))
```

The latest attempt for each task/sample is used for analysis; failures are not
hidden by preferring older successes. Rescoring writes a new JSONL, preserves the
original file, and records the source hash and original score. It never runs the
agent. Malformed JSONL is rejected rather than silently losing observations.

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

The archive adapter reads the supplied frozen numerical inputs without running
legacy scripts, changing archived files, or embedding result constants in code:

```python
from alphadiana.analysis.paper_archive import PaperArchive

archive = PaperArchive("/path/to/paper-figures")
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
source provenance. Built-in action extraction currently identifies its heuristic
labels; they must not be represented as human or model-verified annotations.


## Built-in measurements

Default analysis is offline and reads saved artifacts. It includes:

| Measurement | Tables and figures | Required evidence |
|---|---|---|
| Performance and reliability | Accuracy, empirical Pass@k and Avg@k | Scores, expected tasks and configured repeat count |
| Resource usage | Mean output tokens and elapsed seconds per observed attempt; cost per correct answer | Provider/native usage counters and elapsed time |
| Process entropy | Outcome-conditioned post-tool entropy with standard-error bands; log10 output length versus mean entropy | Observed top-K probabilities; request attribution for post-tool curves; measured usage for length |
| Logged action events | Heuristic event composition and transitions | Canonical traces; these are not model-verified functional labels |

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
Configure an existing OpenAI-compatible service before constructing a judge:

```bash
export OPENAI_BASE_URL="https://your-provider.example/v1"
export OPENAI_API_KEY="your-api-key"
export OPENAI_MODEL="your-model-id"
# Optional reproducibility pin; otherwise the model ID is recorded as the version.
export OPENAI_MODEL_VERSION="your-provider-model-revision"
```

There is no default endpoint, model or placeholder credential. Missing required
configuration raises before a request is sent. `OPENAI_MODEL_NAME` is a model
alias; legacy `JUDGE_MODEL` and `JUDGE_API_BASE` remain lower-priority fallbacks.
Explicit constructor/scorer configuration overrides environment values. Configuring
a provider does not enable annotations automatically. The `llm_judge` scorer reads
the same environment when selected with `ad.scorer.create("llm_judge")`.

```python
from alphadiana.analysis.analyzer import ExecutionMetrics
from alphadiana.analysis.entropy_analysis import EntropyAnalysis
from alphadiana.analysis.action_annotations import ActionAnnotations
from alphadiana.analysis.failure_annotations import FailureAnnotations

execution = ExecutionMetrics()
entropy = EntropyAnalysis(bin_ratio=2, min_observations=5)
with ActionAnnotations(cache_dir="analysis-cache/actions") as actions:
    with FailureAnnotations(cache_dir="analysis-cache/failures") as failures:
        plugins = [execution, entropy, actions, failures]
        report = ad.analyzer(saved, plugins=plugins)
        report.export("figures/annotated-run")
```

Action annotations classify fixed character spans within assistant steps into
Understanding, Planning, Reasoning, Tool Use, Verification and Finalization.
Prompt and tool-result rows are excluded; neighboring text supplies context.
Composition percentages and within-sample transition matrices are grouped by
correct/incorrect/unscored outcome. Span size and context policy affect counts
and must accompany comparisons. Supply `taxonomy={label: definition}` to extend it.

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
from alphadiana.analysis.analyzer import ExecutionMetrics
from alphadiana.analysis.entropy_analysis import EntropyAnalysis

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
