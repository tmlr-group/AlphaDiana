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

Install plotting support with `pip install 'alphadiana[analysis]'`. Execution and
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
composition/transitions; entropy/length and post-tool entropy; macro efficiency;
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
