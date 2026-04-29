"""Corpus inventory contracts for offline Phase 15 insight analysis."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

CORPUS_STATUS_VALUES = ("unavailable", "manifest_only", "validated_records")
ENV_CORPUS_VARS = (
    "PHASE15_HLE_OPENCODE_LOGPROBS",
    "PHASE15_HLE_ZEROCLAW_LOGPROBS",
    "PHASE15_COLLAB_RESULTS_ROOT",
)
MODEL_LABEL_QWEN35_27B = "Qwen/Qwen3.5-27B"
HF_ALPHADIANA_PUBLIC_REF = "T-MARS/alphadiana-benchmark-results"


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
