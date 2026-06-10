"""QJL scorer that consumes only host-owned official evaluation artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer

logger = logging.getLogger(__name__)


def _load_official_score(response: Any) -> dict[str, Any]:
    metadata = getattr(response, "metadata", {})
    if metadata.get("official_eval_host_owned") is not True:
        raise ValueError("response is not marked as host-owned official evaluation")
    raw_artifacts_dir = str(
        metadata.get("artifacts_dir", "")
    ).strip()
    if not raw_artifacts_dir:
        raise FileNotFoundError("missing host artifacts_dir")
    artifacts_dir = Path(raw_artifacts_dir)
    manifest = json.loads((artifacts_dir / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("host_owned") is not True:
        raise ValueError("invalid or untrusted QJL manifest")
    if manifest.get("execution_id") != metadata.get("execution_id"):
        raise ValueError("QJL manifest execution_id mismatch")
    if not manifest.get("artifact_validation", {}).get("complete"):
        raise ValueError("QJL evaluation suitcase is incomplete")
    score_path = artifacts_dir / "score.json"
    expected_hash = manifest.get("artifact_hashes", {}).get("score.json")
    actual_hash = hashlib.sha256(score_path.read_bytes()).hexdigest()
    if not expected_hash or actual_hash != expected_hash:
        raise ValueError("QJL score.json hash mismatch")
    payload = json.loads(score_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "moltbench.score.v3":
        raise ValueError("invalid or untrusted QJL score.json")
    return payload


@register_scorer("moltbench_qjl")
class MoltBenchQJLScorer(Scorer):
    """Expose the official C/G/S/M/K vector through AlphaDiana ScoreResult."""

    @property
    def name(self) -> str:
        return "moltbench_qjl"

    def setup(self, config: dict) -> None:
        del config

    def score(self, task: Any, response: Any) -> ScoreResult:
        try:
            score = _load_official_score(response)
        except Exception as exc:
            logger.warning("QJL official score unavailable: %s", exc)
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(getattr(task, "ground_truth", {})),
                predicted=None,
                rationale=f"Missing valid host-owned official score: {exc}",
                metadata={"official_score_missing": True},
            )

        correct = bool(score.get("correct"))
        gain = float(score.get("score", 0.0)) if correct else 0.0
        official = dict(score.get("official_result", {}))
        metrics = dict(official.get("metrics", {}))
        return ScoreResult(
            correct=correct,
            score=gain,
            expected=str(getattr(task, "ground_truth", {})),
            predicted=(
                f"C={correct}, G={score.get('G', {}).get('value', 0.0):.4f}, "
                f"S={score.get('S', {}).get('value', 0.0):.4f}"
            ),
            rationale=(
                "Scored from host-owned official_eval.json after a clean rebuild; "
                "agent-written results were ignored."
            ),
            metadata={
                "score_vector": score,
                "official_metrics": metrics,
                "artifacts_dir": getattr(response, "metadata", {}).get("artifacts_dir", ""),
            },
        )
