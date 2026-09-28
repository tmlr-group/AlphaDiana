"""Reopenable run evidence and append-only, provenance-linked rescoring."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.analysis.io.status import (
    infer_score_status,
    normalize_legacy_timeout_zero_record,
)
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.base import AgentResponse


@dataclass(frozen=True)
class Artifacts:
    """A result JSONL and its adjacent, existing ResultStore evidence directory."""

    path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser().resolve())
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        if self.path.suffix != ".jsonl":
            raise ValueError("Artifacts must point to a result .jsonl file")

    @property
    def store(self) -> ResultStore:
        return ResultStore(str(self.path.parent), self.path.stem)

    def records(self, *, latest: bool = True) -> list[dict[str, Any]]:
        """Latest means last persisted attempt, never preferentially successful."""
        records = []
        with self.path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict) or "task_id" not in record:
                        raise ValueError("record must be an object with task_id")
                    record = normalize_legacy_timeout_zero_record(record)
                    record["score_status"] = infer_score_status(record)
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        f"Invalid artifact record {self.path}:{line_number}: {exc}"
                    ) from exc
                records.append(record)
        if not latest:
            return records
        selected = {}
        for record in records:
            key = (
                record.get("run_id", self.path.stem),
                record["task_id"],
                record.get("sample_index", 0),
            )
            selected[key] = record
        return list(selected.values())

    def artifact_root(self, record: dict[str, Any]) -> Path:
        """Locate retained sidecars, including rescored runs in another directory."""
        return Path(record.get("artifact_origin", self.store.artifacts_dir)).resolve()

    def file(self, record: dict[str, Any], key: str) -> Path | None:
        """Resolve a declared sidecar using the ResultStore reference namespace.

        Logprob files are relative to the run directory; other manifest files
        are relative to its artifacts directory. Missing declarations return None;
        declared but missing files raise so damaged evidence cannot look complete.
        """
        reference = (record.get("artifact_manifest") or {}).get("files", {}).get(key)
        if reference is None:
            return None
        if not isinstance(reference, str):
            raise TypeError(f"Artifact {key!r} is not a file reference")
        root = self.artifact_root(record)
        if key in {"logprobs_float", "logprobs_int16"}:
            root = root.parent
        relative = Path(reference)
        path = (root / relative).resolve()
        if relative.is_absolute() or not path.is_relative_to(root):
            raise ValueError(f"Artifact reference escapes its directory: {key}")
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def task(self, record: dict[str, Any]) -> BenchmarkTask:
        attachments = {}
        root = self.artifact_root(record)
        for name, reference in record.get("task_attachments", {}).items():
            path = (root / reference["path"]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Attachment reference escapes its artifact directory")
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != reference["sha256"]:
                raise ValueError(f"Attachment content changed: {name}")
            attachments[name] = content
        return BenchmarkTask(
            record["task_id"],
            record["problem"],
            record.get("ground_truth"),
            record.get("task_metadata", {}),
            attachments,
        )

    def manifest(self) -> dict[str, Any]:
        return self.store.load_manifest()

    def rescore(
        self,
        scorer,
        *,
        output: str | Path | None = None,
        allow_environment: bool = False,
    ) -> Artifacts:
        """Create a separate score history without rerunning the solver.

        Environment restoration belongs to the scorer's ``rescore`` contract.
        It must be explicitly opted into; cached rewards are never fresh verifier
        evidence. Existing source files and original judgments are not changed.
        """
        from alphadiana.api import ScorerSpec
        from alphadiana.engine.components import register_builtin_components
        from alphadiana.scorer.base import Scorer
        from alphadiana.scorer.registry import ScorerRegistry

        register_builtin_components()
        if isinstance(scorer, ScorerSpec):
            implementation = ScorerRegistry.get(scorer.name)()
            implementation.setup(scorer.config)
        elif isinstance(scorer, Scorer):
            implementation = scorer
        else:
            raise TypeError("scorer must be a ScorerSpec or Scorer implementation")
        if implementation.requires_environment and not allow_environment:
            raise ValueError(
                "This scorer restores an environment; set allow_environment=True"
            )
        destination = (
            Path(output)
            if output
            else self.path.with_name(
                f"{self.path.stem}-rescore-{uuid4().hex[:12]}.jsonl"
            )
        )
        destination = destination.expanduser().resolve()
        if destination.suffix != ".jsonl":
            raise ValueError("Rescore output must end in .jsonl")
        if destination.exists():
            raise FileExistsError(destination)
        source_digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        results = []
        for record in self.records():
            task = self.task(record)
            response_data = {
                f.name: record[f.name]
                for f in fields(AgentResponse)
                if f.name in record and f.name != "answer"
            }
            response = AgentResponse(answer=record.get("predicted"), **response_data)
            judgment = implementation.rescore(task, response, artifacts=self)
            derived = dict(record)
            derived["artifact_origin"] = str(self.artifact_root(record))
            derived.update(
                correct=judgment.correct,
                score=judgment.score,
                predicted=judgment.predicted,
                rationale=judgment.rationale,
                score_metadata=judgment.metadata,
                scorer_name=implementation.name,
            )
            derived["rescoring"] = {
                "source": str(self.path),
                "source_sha256": source_digest,
                "source_timestamp": record.get("timestamp"),
                "original_score": record.get("score"),
                "original_scorer": record.get("scorer_name"),
                "environment_allowed": allow_environment,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            # A new score does not erase execution failures in source metadata.
            derived.pop("score_status", None)
            derived["score_status"] = infer_score_status(derived)
            results.append(derived)
        if not results:
            raise ValueError("Cannot rescore an empty run")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids clobbering concurrent rescoring or original runs.
        with destination.open("x", encoding="utf-8") as stream:
            for result in results:
                stream.write(
                    json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n"
                )
        derived_artifacts = Artifacts(destination)
        manifest = self.manifest()
        manifest.update(
            scorer_name=implementation.name,
            rescored_from=str(self.path),
            source_sha256=source_digest,
        )
        derived_artifacts.store.save_manifest(manifest)
        return derived_artifacts
