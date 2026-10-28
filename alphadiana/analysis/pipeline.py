"""Compose measurement plugins over saved evidence and manage judge lifetimes.

Measurement plugins return tables and declarative figures. Renderers never read
harness internals or recompute statistics; every figure has its plotted data.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from typing import Any

from alphadiana.analysis.measurements import (
    AnalysisPlugin,
    ExecutionMetrics,
    Figure,
    Measurement,
)
from alphadiana.results.artifacts import Artifacts


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
            from alphadiana.analysis.measurements import EntropyAnalysis, RequestEntropy

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
                from alphadiana.analysis.actions.llm_annotations import (
                    ActionAnnotations,
                )
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
        from alphadiana.analysis.export import export_report

        return export_report(self.compute(), Path(output_dir), formats=formats)
