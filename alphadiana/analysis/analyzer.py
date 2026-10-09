"""Extensible measurements and publication exports over durable run evidence.

Measurement plugins return tables and declarative figures. Renderers never read
harness internals or recompute statistics; every figure has its plotted data.
"""

from __future__ import annotations

import hashlib
import os
from contextlib import ExitStack
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from alphadiana.analysis.io.status import VALID_SCORE_STATUS
from alphadiana.analysis.resource_usage import (
    DEFAULT_USAGE_EXTRACTORS,
    UsageExtractor,
    measured_usage,
)
from alphadiana.analysis.reliability import compute_reliability_summary
from alphadiana.artifacts import Artifacts


@dataclass(frozen=True)
class Figure:
    name: str
    table: str
    kind: str
    x: str
    y: str
    title: str
    xlabel: str
    ylabel: str
    group: str | None = None
    xscale: str = "linear"
    value: str = "count"
    value_label: str = "Transitions"
    color_limits: tuple[float, float] | None = None
    lower: str | None = None
    upper: str | None = None
    ellipse: bool = False


@dataclass
class Measurement:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    figures: list[Figure] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)


class AnalysisPlugin(Protocol):
    """A custom measurement needs no edits to Runner or built-in analyzers."""

    name: str

    def measure(self, artifacts: Sequence[Artifacts]) -> Measurement: ...


def _number(value: Any) -> float | None:
    import math

    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class ExecutionMetrics:
    name = "execution"

    def __init__(
        self, *, usage_extractors: Sequence[UsageExtractor] = DEFAULT_USAGE_EXTRACTORS
    ):
        self.usage_extractors = tuple(usage_extractors)

    def measure(self, artifacts: Sequence[Artifacts]) -> Measurement:
        summary, samples, reliability = [], [], []
        coverage = {}
        for source in artifacts:
            records = source.records()
            manifest = source.manifest()
            expected = manifest.get("expected_sample_count", len(records))
            if expected < len(records):
                raise ValueError(
                    f"Manifest sample count smaller than records: {source.path}"
                )
            label = source.path.stem
            correct = sum(
                r.get("score_status") == VALID_SCORE_STATUS and r.get("correct") is True
                for r in records
            )
            scored = sum(r.get("score_status") == VALID_SCORE_STATUS for r in records)
            tokens, times = [], []
            for row in records:
                usage = measured_usage(source, row, self.usage_extractors)
                token_count = usage.output_tokens
                elapsed = _number(row.get("wall_time_sec"))
                entropy_stats = row.get("token_entropy_stats") or {}
                entropy = (
                    _number(entropy_stats.get("mean"))
                    if entropy_stats.get("n_tokens", 0)
                    else None
                )
                if token_count is not None:
                    tokens.append(token_count)
                if elapsed is not None:
                    times.append(elapsed)
                samples.append(
                    {
                        "run": label,
                        "task_id": row["task_id"],
                        "sample_index": row.get("sample_index", 0),
                        "correct": row.get("correct"),
                        "score_status": row.get("score_status"),
                        "output_tokens": token_count,
                        "usage_provenance": usage.provenance,
                        "seconds": elapsed,
                        "entropy_nats": entropy,
                    }
                )
            rates = compute_reliability_summary(records, manifest)
            for key, title in (("pass_at_k", "Pass"), ("avg_at_k", "Avg")):
                reliability.append(
                    {
                        "run": label,
                        "metric": f"{title}@{rates['num_samples']}",
                        "value": rates[key] if expected else None,
                        "requested": expected,
                        "observed": len(records),
                    }
                )
            summary.append(
                {
                    "run": label,
                    "benchmark": manifest.get(
                        "benchmark_name",
                        records[0].get("benchmark_name", "") if records else "",
                    ),
                    "agent": manifest.get(
                        "agent_name",
                        records[0].get("agent_name", "") if records else "",
                    ),
                    "requested": expected,
                    "observed": len(records),
                    "scored": scored,
                    "correct": correct,
                    "accuracy": correct / expected if expected else None,
                    "num_samples": rates["num_samples"],
                    "pass_at_k": rates["pass_at_k"] if expected else None,
                    "avg_at_k": rates["avg_at_k"] if expected else None,
                    "mean_output_tokens": sum(tokens) / len(tokens) if tokens else None,
                    "mean_seconds": sum(times) / len(times) if times else None,
                    "tokens_per_correct": sum(tokens) / correct
                    if correct and len(tokens) == len(records)
                    else None,
                    "seconds_per_correct": sum(times) / correct
                    if correct and len(times) == len(records)
                    else None,
                }
            )
            coverage[label] = {
                "requested": expected,
                "observed": len(records),
                "scored": scored,
                "missing_samples": expected - len(records),
                "tokens_observed": len(tokens),
                "time_observed": len(times),
                "action_labeling": "not run; enable judge=True for LLM segmentation and labels",
            }
        figures = [
            Figure(
                "reliability",
                "reliability",
                "bar",
                "run",
                "value",
                "Task reliability",
                "Run",
                "Fraction",
                group="metric",
            ),
            Figure(
                "mean_tokens",
                "performance",
                "barh",
                "run",
                "mean_output_tokens",
                "Mean output tokens per attempt",
                "Run",
                "Output tokens",
            ),
            Figure(
                "mean_time",
                "performance",
                "barh",
                "run",
                "mean_seconds",
                "Mean elapsed time per attempt",
                "Run",
                "Seconds",
            ),
            Figure(
                "accuracy",
                "performance",
                "barh",
                "run",
                "accuracy",
                "Task accuracy",
                "Run",
                "Correct / requested",
            ),
            Figure(
                "efficiency",
                "performance",
                "barh",
                "run",
                "tokens_per_correct",
                "Output cost per correct outcome",
                "Run",
                "Output tokens / correct",
            ),
            Figure(
                "time_efficiency",
                "performance",
                "barh",
                "run",
                "seconds_per_correct",
                "Elapsed cost per correct outcome",
                "Run",
                "Seconds / correct",
            ),
            Figure(
                "entropy_tokens",
                "samples",
                "scatter",
                "output_tokens",
                "entropy_nats",
                "Generation entropy and output length",
                "Output tokens",
                "Top-K entropy (nats)",
                "run",
            ),
        ]
        return Measurement(
            {
                "performance": summary,
                "reliability": reliability,
                "samples": samples,

            },
            figures,
            coverage,
        )


class TableAnalysis:
    """Validated measurement tables, e.g. matched interventions or annotations.

    Explicit source metadata is mandatory. This supports existing paper analysis
    without baking its model names, taxonomy, conditions or frozen values into code.
    """

    def __init__(
        self,
        name: str,
        tables: Mapping[str, list[dict]],
        figures: Sequence[Figure],
        *,
        provenance: Mapping[str, Any],
    ):
        if not name or not provenance:
            raise ValueError("Table analysis requires a name and source provenance")
        self.name, self.tables, self.figures, self.provenance = (
            name,
            dict(tables),
            list(figures),
            dict(provenance),
        )

    def measure(self, artifacts: Sequence[Artifacts]) -> Measurement:
        return Measurement(self.tables, self.figures, {"source": self.provenance})


class Analyzer:
    def __init__(
        self,
        artifacts: Artifacts | str | Path | Sequence[Artifacts | str | Path],
        *,
        plugins: Sequence[AnalysisPlugin] | None = None,
        judge: bool = False,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        cache_dir: str | Path | None = None,
    ):
        enabled = judge or any(
            value is not None for value in (model, base_url, api_key)
        )
        if plugins is not None and enabled:
            raise ValueError(
                "Configure judges through plugins or Analyzer options, not both"
            )
        if cache_dir is not None and not enabled:
            raise ValueError(
                "cache_dir requires judge=True or explicit judge configuration"
            )
        self._judge_options = None
        if enabled:
            from alphadiana.scorer.judge_config import JudgeConfig

            provider = JudgeConfig.resolve(
                model=model, base_url=base_url, api_key=api_key, analysis=True
            )
            self._judge_options = dict(
                model=provider.model,
                base_url=provider.base_url,
                api_key=provider.api_key,
            )
            self._judge_cache = (
                Path(cache_dir)
                if cache_dir is not None
                else (
                    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
                    / "alphadiana"
                    / "annotations"
                )
            )
        sources = (
            [artifacts]
            if isinstance(artifacts, (Artifacts, str, Path))
            else list(artifacts)
        )
        self.artifacts = [
            source if isinstance(source, Artifacts) else Artifacts(Path(source))
            for source in sources
        ]
        if len({a.path for a in self.artifacts}) != len(self.artifacts):
            raise ValueError(
                "Duplicate artifact sources would double count observations"
            )
        if len({a.path.stem for a in self.artifacts}) != len(self.artifacts):
            raise ValueError("Artifact run names must be unique across a comparison")
        if plugins is None:
            from alphadiana.analysis.entropy_analysis import EntropyAnalysis
            from alphadiana.analysis.process_entropy import RequestEntropy

            self.plugins = [ExecutionMetrics(), RequestEntropy(), EntropyAnalysis()]
        else:
            self.plugins = list(plugins)
        if len({p.name for p in self.plugins}) != len(self.plugins):
            raise ValueError("Analysis plugin names must be unique")

    def compute(self) -> dict[str, Any]:
        results = {}
        with ExitStack() as stack:
            plugins = list(self.plugins)
            if self._judge_options is not None:
                from alphadiana.analysis.action_annotations import ActionAnnotations
                from alphadiana.analysis.failure_annotations import FailureAnnotations

                for factory, directory in (
                    (ActionAnnotations, "actions"),
                    (FailureAnnotations, "failures"),
                ):
                    plugin = factory(
                        cache_dir=self._judge_cache / directory, **self._judge_options
                    )
                    plugins.append(stack.enter_context(plugin))
            for plugin in plugins:
                result = plugin.measure(self.artifacts)
                for figure in result.figures:
                    if figure.table not in result.tables:
                        raise ValueError(
                            f"Figure {figure.name} refers to missing table {figure.table}"
                        )
                results[plugin.name] = asdict(result)
        return {
            "schema_version": 2,
            "sources": [
                {
                    "path": str(a.path),
                    "sha256": hashlib.sha256(a.path.read_bytes()).hexdigest(),
                }
                for a in self.artifacts
            ],
            "analyses": results,
        }

    def export(
        self, output_dir: str | Path, *, formats: Sequence[str] = ("pdf", "svg", "png")
    ) -> dict[str, Any]:
        from alphadiana.analysis.figures import export_report

        return export_report(self.compute(), Path(output_dir), formats=formats)
