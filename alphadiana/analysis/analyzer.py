"""Extensible measurements and publication exports over durable run evidence.

Measurement plugins return tables and declarative figures. Renderers never read
harness internals or recompute statistics; every figure has its plotted data.
"""

from __future__ import annotations

import hashlib
import itertools
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from alphadiana.analysis.action_events import extract_action_event_rows
from alphadiana.analysis.io.status import VALID_SCORE_STATUS
from alphadiana.analysis.result_reader import RunBundle
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

    def measure(self, artifacts: Sequence[Artifacts]) -> Measurement:
        summary, samples, actions, transitions = [], [], [], []
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
                usage = row.get("token_usage") or {}
                token_count = _number(
                    usage.get("completion_tokens", usage.get("output_tokens"))
                )
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
                        "seconds": elapsed,
                        "entropy_nats": entropy,
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
            bundle = RunBundle(
                source.path.parent,
                label,
                source.path,
                source.path.parent / label,
                manifest,
                records,
                {},
            )
            harness = manifest.get(
                "agent_name", records[0].get("agent_name", "") if records else ""
            )
            event_rows = extract_action_event_rows(bundle, harness=harness)
            # Existing action extractor exposes strict versus heuristic provenance.
            for event in event_rows:
                event["run"] = label
            actions.extend(event_rows)
            counts = Counter()
            sequences: dict[tuple, list[str]] = {}
            for event in event_rows:
                sequences.setdefault(
                    (event["task_id"], event["sample_index"]), []
                ).append(event["canonical_action"])
            for sequence in sequences.values():
                counts.update(itertools.pairwise(sequence))
            transitions.extend(
                {"run": label, "source": a, "target": b, "count": count}
                for (a, b), count in counts.items()
            )
            coverage[label] = {
                "requested": expected,
                "observed": len(records),
                "scored": scored,
                "missing_samples": expected - len(records),
                "tokens_observed": len(tokens),
                "time_observed": len(times),
                "action_events": len(event_rows),
                "action_labeling": "existing deterministic extractor; heuristic labels are marked",
            }
        composition = Counter((row["run"], row["canonical_action"]) for row in actions)
        figures = [
            Figure(
                "accuracy",
                "performance",
                "bar",
                "run",
                "accuracy",
                "Task accuracy",
                "Run",
                "Correct / requested",
            ),
            Figure(
                "efficiency",
                "performance",
                "bar",
                "run",
                "tokens_per_correct",
                "Output cost per correct outcome",
                "Run",
                "Output tokens / correct",
            ),
            Figure(
                "time_efficiency",
                "performance",
                "bar",
                "run",
                "seconds_per_correct",
                "Elapsed cost per correct outcome",
                "Run",
                "Seconds / correct",
            ),
            Figure(
                "action_composition",
                "action_composition",
                "bar",
                "action",
                "count",
                "Action composition",
                "Action",
                "Events",
                "run",
            ),
            Figure(
                "action_transitions",
                "action_transitions",
                "heatmap",
                "target",
                "source",
                "Consecutive action transitions",
                "Next action",
                "Previous action",
                "run",
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
                "samples": samples,
                "actions": actions,
                "action_composition": [
                    {"run": r, "action": a, "count": c}
                    for (r, a), c in composition.items()
                ],
                "action_transitions": transitions,
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
    ):
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
        if plugins is None:
            from alphadiana.analysis.process_entropy import RequestEntropy

            self.plugins = [ExecutionMetrics(), RequestEntropy()]
        else:
            self.plugins = list(plugins)
        if len({p.name for p in self.plugins}) != len(self.plugins):
            raise ValueError("Analysis plugin names must be unique")

    def compute(self) -> dict[str, Any]:
        results = {}
        for plugin in self.plugins:
            result = plugin.measure(self.artifacts)
            for figure in result.figures:
                if figure.table not in result.tables:
                    raise ValueError(
                        f"Figure {figure.name} refers to missing table {figure.table}"
                    )
            results[plugin.name] = asdict(result)
        return {
            "schema_version": 1,
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
