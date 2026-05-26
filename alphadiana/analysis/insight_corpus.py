"""Corpus inventory contracts for offline Phase 15 insight analysis."""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from alphadiana.analysis.action_events import normalized_records
from alphadiana.analysis.result_reader import RunBundle, load_run_bundle
from alphadiana.analysis.io.status import VALID_SCORE_STATUS, infer_score_status

CORPUS_STATUS_VALUES = ("unavailable", "manifest_only", "validated_records")
ENV_CORPUS_VARS = (
    "PHASE15_HLE_OPENCODE_LOGPROBS",
    "PHASE15_HLE_ZEROCLAW_LOGPROBS",
    "PHASE15_COLLAB_RESULTS_ROOT",
)
MODEL_LABEL_QWEN35_27B = "Qwen/Qwen3.5-27B"
HF_ALPHADIANA_PUBLIC_REF = "T-MARS/alphadiana-benchmark-results"
ERROR_SCORE_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}


@dataclass(frozen=True)
class CorpusSpec:
    """One sanitized persisted-result corpus source."""

    label: str
    benchmark: str
    harness: str
    model_label: str
    results_dir: Path
    run_id: str
    source_kind: str
    public_ref: str
    env_var: str | None


@dataclass(frozen=True)
class CorpusInventoryRow:
    """Denominator and availability row for one corpus."""

    corpus_label: str
    benchmark: str
    harness: str
    model_label: str
    source_kind: str
    public_ref: str
    status: str
    expected_samples: int
    task_files: int
    task_records: int
    selected_records: int
    valid_scored: int
    behavioral_correct: int
    behavioral_wrong: int
    error_records: int
    missing_samples: int
    status_counts: dict[str, int]
    unavailable_reason: str | None


DEFAULT_GPQA_CORPORA = (
    CorpusSpec(
        label="gpqa-openclaw-v2",
        benchmark="gpqa",
        harness="openclaw",
        model_label=MODEL_LABEL_QWEN35_27B,
        results_dir=Path("results"),
        run_id="full_gpqa_v2_openclaw_qwen35_27b_logprobs",
        source_kind="repo",
        public_ref="results/full_gpqa_v2_openclaw_qwen35_27b_logprobs",
        env_var=None,
    ),
    CorpusSpec(
        label="gpqa-opencode-v2",
        benchmark="gpqa",
        harness="opencode",
        model_label=MODEL_LABEL_QWEN35_27B,
        results_dir=Path("results"),
        run_id="full_gpqa_v2_opencode_qwen35_27b_logprobs",
        source_kind="repo",
        public_ref="results/full_gpqa_v2_opencode_qwen35_27b_logprobs",
        env_var=None,
    ),
    CorpusSpec(
        label="gpqa-zeroclaw-v2",
        benchmark="gpqa",
        harness="zeroclaw",
        model_label=MODEL_LABEL_QWEN35_27B,
        results_dir=Path("results"),
        run_id="full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
        source_kind="repo",
        public_ref="results/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
        env_var=None,
    ),
)

_ENV_SPEC_METADATA = {
    "PHASE15_HLE_OPENCODE_LOGPROBS": ("hle-opencode-env", "hle", "opencode"),
    "PHASE15_HLE_ZEROCLAW_LOGPROBS": ("hle-zeroclaw-env", "hle", "zeroclaw"),
    "PHASE15_COLLAB_RESULTS_ROOT": ("collab-results-env", "collaborator", "unknown"),
}
_LOCAL_PATH_RE = re.compile(r"(?<!\w)(?:/data\d*|/home|/tmp|/mnt|/scratch)(?:/[^\s,;:)\\]}>\"']*)*")
_SECRET_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{6,}|api[_-]?key\s*=\s*[^\s,;]+|api[_-]?key['\"]?\s*:\s*['\"][^'\"]+['\"]?)"
)


def sanitize_path_text(text: str) -> str:
    """Remove local-only paths and secret-like markers from output-facing text."""
    sanitized = str(text)
    for env_var in ENV_CORPUS_VARS:
        value = os.environ.get(env_var)
        if value:
            sanitized = sanitized.replace(value, env_var)
    sanitized = re.sub(r"/\S*datasets--T-MARS--alphadiana-benchmark-results\S*", HF_ALPHADIANA_PUBLIC_REF, sanitized)
    sanitized = re.sub(r"/\S*datasets--cais--hle\S*", "cais/hle local cache", sanitized)
    sanitized = _SECRET_RE.sub("[secret]", sanitized)
    return _LOCAL_PATH_RE.sub("[local-path]", sanitized)


def load_default_corpus_specs(results_dir: Path = Path("results")) -> list[CorpusSpec]:
    """Return repo-relative GPQA corpus specs with sanitized public refs."""
    return [
        CorpusSpec(
            label=spec.label,
            benchmark=spec.benchmark,
            harness=spec.harness,
            model_label=spec.model_label,
            results_dir=results_dir,
            run_id=spec.run_id,
            source_kind=spec.source_kind,
            public_ref=sanitize_path_text(str(results_dir / spec.run_id)),
            env_var=None,
        )
        for spec in DEFAULT_GPQA_CORPORA
    ]


def load_env_corpus_specs(env: Mapping[str, str] | None = None) -> list[CorpusSpec]:
    """Load operator-provided local corpus roots without exposing their paths."""
    source = os.environ if env is None else env
    specs: list[CorpusSpec] = []
    for env_var in ENV_CORPUS_VARS:
        value = str(source.get(env_var) or "").strip()
        if not value:
            continue
        label, benchmark, harness = _ENV_SPEC_METADATA[env_var]
        root = Path(value).expanduser()
        specs.append(
            CorpusSpec(
                label=label,
                benchmark=benchmark,
                harness=harness,
                model_label=MODEL_LABEL_QWEN35_27B,
                results_dir=root.parent,
                run_id=root.name,
                source_kind="env_local" if env_var != "PHASE15_COLLAB_RESULTS_ROOT" else "collaborator",
                public_ref=env_var,
                env_var=env_var,
            )
        )
    return specs


def load_phase15_corpus_specs(
    results_dir: Path = Path("results"),
    env: Mapping[str, str] | None = None,
    include_hf_synced: bool = True,
) -> list[CorpusSpec]:
    """Load repo, optional HF-synced, and env-provided Phase 15 corpus specs."""
    specs = load_default_corpus_specs(results_dir)
    if include_hf_synced:
        specs.extend(_load_hf_synced_specs(results_dir))
    specs.extend(load_env_corpus_specs(env))
    return specs


def load_selected_task_records(bundle: RunBundle) -> list[dict[str, Any]]:
    """Return scorer-aware selected records for denominator and behavior analysis."""
    return normalized_records(bundle)


def inventory_corpus_spec(spec: CorpusSpec) -> CorpusInventoryRow:
    """Build one availability and denominator row for a corpus spec."""
    run_dir = spec.results_dir / spec.run_id
    jsonl_path = spec.results_dir / f"{spec.run_id}.jsonl"
    if not run_dir.exists() and not jsonl_path.exists():
        return _empty_inventory_row(spec, status="unavailable", unavailable_reason="missing run artifacts")

    bundle = load_run_bundle(spec.results_dir, spec.run_id)
    selected_records = load_selected_task_records(bundle)
    task_record_count = sum(len(records) for records in bundle.task_records.values())
    expected_samples = _expected_sample_count(bundle.manifest, selected_records)
    if not selected_records:
        return CorpusInventoryRow(
            corpus_label=spec.label,
            benchmark=spec.benchmark,
            harness=spec.harness,
            model_label=spec.model_label,
            source_kind=spec.source_kind,
            public_ref=sanitize_path_text(spec.public_ref),
            status="manifest_only",
            expected_samples=expected_samples,
            task_files=len(bundle.task_records),
            task_records=task_record_count,
            selected_records=0,
            valid_scored=0,
            behavioral_correct=0,
            behavioral_wrong=0,
            error_records=0,
            missing_samples=expected_samples,
            status_counts={},
            unavailable_reason=None,
        )

    statuses = [infer_score_status(record) for record in selected_records]
    status_counts = dict(sorted(Counter(statuses).items()))
    valid_records = [
        record
        for record, status in zip(selected_records, statuses)
        if status == VALID_SCORE_STATUS
    ]
    behavioral_correct = sum(1 for record in valid_records if record.get("correct") is True)
    behavioral_wrong = sum(1 for record in valid_records if record.get("correct") is False)
    error_records = sum(1 for status in statuses if status in ERROR_SCORE_STATUSES)

    return CorpusInventoryRow(
        corpus_label=spec.label,
        benchmark=spec.benchmark,
        harness=spec.harness,
        model_label=spec.model_label,
        source_kind=spec.source_kind,
        public_ref=sanitize_path_text(spec.public_ref),
        status="validated_records",
        expected_samples=expected_samples,
        task_files=len(bundle.task_records),
        task_records=task_record_count,
        selected_records=len(selected_records),
        valid_scored=len(valid_records),
        behavioral_correct=behavioral_correct,
        behavioral_wrong=behavioral_wrong,
        error_records=error_records,
        missing_samples=max(expected_samples - len(selected_records), 0),
        status_counts=status_counts,
        unavailable_reason=None,
    )


def build_denominator_ledger(specs: Sequence[CorpusSpec]) -> list[dict[str, Any]]:
    """Return sorted plain-dict inventory rows for corpus denominator claims."""
    rows = [asdict(inventory_corpus_spec(spec)) for spec in specs]
    return sorted(rows, key=lambda row: str(row["corpus_label"]))


def write_denominator_ledger(output_dir: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Path]:
    """Write JSON and CSV denominator ledgers."""
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "corpus_inventory.json"
    csv_path = output_dir / "corpus_inventory.csv"
    serializable_rows = [dict(row) for row in rows]
    json_path.write_text(json.dumps(serializable_rows, indent=2, sort_keys=True), encoding="utf-8")

    fieldnames = _ledger_fieldnames(serializable_rows)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in serializable_rows:
            writer.writerow({
                key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })
    return {"json": json_path, "csv": csv_path}


def _load_hf_synced_specs(results_dir: Path) -> list[CorpusSpec]:
    hf_root = results_dir / "hf-alphadiana-benchmark-results"
    if not hf_root.exists():
        return []
    manifests = sorted(path for path in hf_root.rglob("run_manifest.json") if path.parent != hf_root)
    if not manifests:
        return [
            CorpusSpec(
                label="hf-alphadiana-benchmark-results",
                benchmark="unknown",
                harness="unknown",
                model_label=MODEL_LABEL_QWEN35_27B,
                results_dir=hf_root,
                run_id=hf_root.name,
                source_kind="hf_synced",
                public_ref=HF_ALPHADIANA_PUBLIC_REF,
                env_var=None,
            )
        ]
    specs: list[CorpusSpec] = []
    for manifest_path in manifests:
        run_dir = manifest_path.parent
        run_id = run_dir.name
        specs.append(
            CorpusSpec(
                label=f"hf-{_slug(run_id)}",
                benchmark=_infer_benchmark(run_id),
                harness=_infer_harness(run_id),
                model_label=MODEL_LABEL_QWEN35_27B,
                results_dir=run_dir.parent,
                run_id=run_id,
                source_kind="hf_synced",
                public_ref=HF_ALPHADIANA_PUBLIC_REF,
                env_var=None,
            )
        )
    return specs


def _empty_inventory_row(
    spec: CorpusSpec,
    *,
    status: str,
    unavailable_reason: str | None,
) -> CorpusInventoryRow:
    return CorpusInventoryRow(
        corpus_label=spec.label,
        benchmark=spec.benchmark,
        harness=spec.harness,
        model_label=spec.model_label,
        source_kind=spec.source_kind,
        public_ref=sanitize_path_text(spec.public_ref),
        status=status,
        expected_samples=0,
        task_files=0,
        task_records=0,
        selected_records=0,
        valid_scored=0,
        behavioral_correct=0,
        behavioral_wrong=0,
        error_records=0,
        missing_samples=0,
        status_counts={},
        unavailable_reason=unavailable_reason,
    )


def _expected_sample_count(manifest: Mapping[str, Any], selected_records: Sequence[Mapping[str, Any]]) -> int:
    if manifest.get("expected_sample_count") is not None:
        return int(manifest["expected_sample_count"])
    expected_task_count = manifest.get("expected_task_count")
    num_samples = manifest.get("num_samples")
    if expected_task_count is not None and num_samples is not None:
        return int(expected_task_count) * int(num_samples)
    return len(selected_records)


def _ledger_fieldnames(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    preferred = list(CorpusInventoryRow.__dataclass_fields__)
    extras = sorted({key for row in rows for key in row if key not in preferred})
    return preferred + extras


def _infer_benchmark(text: str) -> str:
    lowered = text.lower()
    if "gpqa" in lowered:
        return "gpqa"
    if "hle" in lowered:
        return "hle"
    return "unknown"


def _infer_harness(text: str) -> str:
    lowered = text.lower()
    for harness in ("openclaw", "opencode", "zeroclaw", "directllm"):
        if harness in lowered:
            return harness
    return "unknown"


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip()).strip("-").lower()
    return slug or "corpus"
