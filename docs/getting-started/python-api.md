# Python composition and paper figures

AlphaDiana's Python API uses the same component registries, configuration validator,
Runner, session lifecycle, scoring, and result store as the CLI. It does not replace
native harness control loops.

```python
import alphadiana as ad

artifacts = ad.runner(output_dir="results", run_id="example", num_samples=1).compute(
    agent=ad.agent("direct_llm", version="1.0", model="Qwen/Qwen3.5-27B",
                   api_base="http://localhost:18960/v1", api_key="EMPTY",
                   max_tokens=8192, capture_logprobs=True, top_logprobs=20),
    data=ad.dataset("MathArena/aime_2026", benchmark="aime", split="train", max_tasks=3),
    scorer=ad.scorer.create("numeric", tolerance=1e-6),
)
report = ad.analyzer(artifacts)
data = report.compute()
report.export("figures/example", formats=("pdf", "svg", "png"))
```

From the cloned checkout, install plotting support with `pip install -e '.[analysis]'`. Execution and
structured analysis do not import Matplotlib. Figure exports contain `report.json`,
source hashes, coverage, each figure's exact plotted data, and `export.json` listing
outputs and inapplicable plots. Export to a new directory to preserve prior evidence.

## Explicit composition

Factories return typed component specifications. Options are copied into the
existing `ExperimentConfig`; specify run-wide options on `runner`, component
options on their factories. Supplying conflicting configuration raises an error.
Choose environments explicitly, for example `ad.environment("podman", image=...)`,
and pass them as `ad.runner(env=...)` where the selected harness supports an
external environment. Native runtimes that own their containers retain that policy.

The functional figure uses shorthand. The implementation makes three boundaries
explicit, which should also be reflected in the manuscript example:

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

## Historical paper concepts

The archive adapter reads the supplied frozen numerical inputs without running
legacy scripts, changing archived files, or embedding result constants in code:

```python
from alphadiana.analysis.paper_archive import PaperArchive

archive = PaperArchive("/path/to/paper-figures")
paper = ad.analyzer([], plugins=[archive])
paper.export("figures/paper-concepts")
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


## Request attribution and model annotations

Default analysis includes request-conditioned top-K entropy when the capture proxy
recorded `call_id`, `call_token_index` and `follows_tool_result`. Bins average each
call first, then average calls with observations in that bin. Tables include token
and call counts, source hashes and missingness. This is a descriptive comparison,
not a causal estimate. Old sidecars without attribution do not become post-tool
observations; Direct has no tool feedback. Historical archive token-position bins
also do not establish offsets after a tool response.

Model annotation is explicit and separate from the execution model:

```python
from alphadiana.analysis.analyzer import ExecutionMetrics
from alphadiana.analysis.process_entropy import RequestEntropy
from alphadiana.analysis.action_annotations import ActionAnnotations

labels = ActionAnnotations(
    model="Qwen/Qwen3.5-27B", model_version="your-pinned-model-revision",
    base_url="http://localhost:18960/v1", cache_dir="analysis-cache",
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
)
try:
    ad.analyzer(saved, plugins=[ExecutionMetrics(), RequestEntropy(), labels]).export(
        "figures/annotated-run"
    )
finally:
    labels.client.close()
```

The default taxonomy reuses the six functional categories from the action-alignment
work: Understanding, Planning, Reasoning, Tool Use, Verification and Finalization.
A caller can supply another `taxonomy={label: definition}`. Annotations cover fixed
character spans within assistant steps, excluding prompt and tool-result rows;
neighboring text is context. The span size and context policy affect counts and
must accompany comparisons. These are model annotations, not human-reviewed
labels. Cache entries bind the complete request, model/version, prompt, response
ID and usage; malformed responses fail rather than becoming heuristic labels.

Rescored artifacts refer to their original sidecars. Keep both directories when
reopening or analyzing a derived score file. Binary task attachments are stored by
content hash and verified before rescoring.

## Readable figure labels

Run IDs remain evidence identifiers. A presentation plugin can use shorter labels
without renaming artifacts or changing measured values:

```python
from copy import deepcopy
from alphadiana.analysis.analyzer import ExecutionMetrics
from alphadiana.analysis.process_entropy import RequestEntropy

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
ad.analyzer(saved, plugins=[
    DisplayLabels(ExecutionMetrics(), names),
    DisplayLabels(RequestEntropy(), names),
]).export("figures/readable-labels")
```

The label map accompanies the report. For a multi-run comparison, supply all
artifact objects and one label per run. Keep labels distinct so different runs
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
`ExecutionMetrics(usage_extractors=[...])`. An extractor receives `(artifacts,
record)` and returns `MeasuredUsage(output_tokens, provenance)`, or `None` when
its schema does not apply. Supply an empty list to use only persisted usage.

Native SWE-Agent and Terminus adapters accept `capture_logprobs: true` and
`top_logprobs` for OpenAI-compatible providers that support probability output.
This observes requests around the upstream subprocess without replacing its tool
loop. Capture defaults to off. Missing provider probabilities remain missing;
observed top-K entropy is not full-distribution entropy. Terminus terminal-feedback
attribution requires exact matches to saved native command observations; unmatched
requests are not inferred from their user role alone.
