"""Native AlphaDiana GEARS agent."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.components import register_agent
from alphadiana.harness.base import Agent, AgentResponse

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig

VALID_PREDICTOR_MODES = ("gears", "cell_mean", "train_mean")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@register_agent("gears")
class GearsAgent(Agent):
    """Verified-replay GEARS adapter supporting gears, cell_mean, and train_mean modes."""

    name = "gears"
    version = "0.1.0"
    owns_task_runtime = True

    @classmethod
    def validate_experiment_config(cls, config: "ExperimentConfig") -> list[str]:
        errors: list[str] = []
        agent_cfg = config.agent_config if isinstance(config.agent_config, dict) else {}
        for key in ("public_manifest", "registry_path", "registry_sha256", "predictor_mode"):
            val = agent_cfg.get(key)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"agent 'gears' requires non-empty '{key}' in agent.config")

        mode = str(agent_cfg.get("predictor_mode", "") or "").strip()
        if mode and mode not in VALID_PREDICTOR_MODES:
            errors.append(
                f"agent 'gears' predictor_mode must be one of {VALID_PREDICTOR_MODES}; got '{mode}'"
            )

        if config.benchmark_name != "gears_perturb":
            errors.append(
                f"agent 'gears' requires benchmark 'gears_perturb'; got '{config.benchmark_name}'"
            )

        if config.scorer_name != "gears_perturb":
            errors.append(
                f"agent 'gears' requires scorer 'gears_perturb'; got '{config.scorer_name}'"
            )

        scorer_cfg = config.scorer_config if isinstance(config.scorer_config, dict) else {}
        for key in ("private_manifest", "private_sha256", "registry_path", "registry_sha256"):
            val = scorer_cfg.get(key)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"scorer 'gears_perturb' requires non-empty '{key}' in scorer.config")

        return errors

    def setup(self, config: dict) -> None:
        self.config = dict(config)
        for key in (
            "public_manifest",
            "registry_path",
            "registry_sha256",
            "predictor_mode",
        ):
            if not config.get(key):
                raise ValueError(f"Missing GEARS agent config: {key}")

        mode = str(config["predictor_mode"]).strip()
        if mode not in VALID_PREDICTOR_MODES:
            raise ValueError(
                f"Unsupported predictor_mode '{mode}'. Expected one of {VALID_PREDICTOR_MODES}"
            )

        self.predictor_mode = mode
        self.manifest_path = Path(config["public_manifest"]).expanduser().resolve()
        self.registry_path = Path(config["registry_path"]).expanduser().resolve()
        self.registry_sha256 = str(config["registry_sha256"]).strip()

    def solve(self, task: BenchmarkTask, sandbox=None) -> AgentResponse:
        import numpy as np

        if sandbox is not None:
            raise ValueError("GEARS agent does not use AlphaDiana Sandbox")

        if task.ground_truth is not None:
            raise ValueError("Private truth must not enter Agent tasks")

        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"Public manifest not found: {self.manifest_path}")

        actual_manifest_sha = sha256_file(self.manifest_path)
        if (
            Path(task.metadata["public_manifest"]).expanduser().resolve() != self.manifest_path
            or task.metadata.get("public_manifest_sha256") != actual_manifest_sha
        ):
            raise ValueError("Public manifest SHA-256 or path mismatch")

        if not self.registry_path.is_file():
            raise FileNotFoundError(f"Registry file not found: {self.registry_path}")

        actual_reg_sha = sha256_file(self.registry_path)
        if actual_reg_sha != self.registry_sha256:
            raise ValueError("Registry SHA-256 mismatch")

        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if (
            not isinstance(registry, dict)
            or registry.get("schema") != "gears-perturb-registry-v1"
        ):
            raise ValueError("Invalid GEARS registry schema")

        if registry.get("predictor_mode") != self.predictor_mode:
            raise ValueError("Registry predictor_mode mismatch")

        if registry.get("public_manifest_sha256") != actual_manifest_sha:
            raise ValueError("Registry public_manifest_sha256 mismatch")

        pred_path = Path(registry["predictions_path"]).expanduser().resolve()
        if not pred_path.is_file():
            raise FileNotFoundError(f"Predictions file not found: {pred_path}")

        actual_pred_sha = sha256_file(pred_path)
        if actual_pred_sha != registry.get("predictions_sha256"):
            raise ValueError("Predictions NPZ SHA-256 mismatch")

        expected_genes = list(task.metadata["gene_names"])
        expected_perts = list(task.metadata["candidate_perts"])

        with np.load(pred_path, allow_pickle=False) as pred_npz:
            pred_genes = [str(x) for x in pred_npz["gene_names"].tolist()]
            if "pert_names" in pred_npz and "pred_matrix" in pred_npz:
                pred_perts = [str(x) for x in pred_npz["pert_names"].tolist()]
                pred_matrix = np.asarray(pred_npz["pred_matrix"], dtype=np.float64)
            else:
                pred_perts = [p for p in expected_perts if p in pred_npz]
                if len(pred_perts) != len(expected_perts):
                    raise ValueError("Predictions NPZ missing candidate perturbations")
                pred_matrix = np.vstack(
                    [np.asarray(pred_npz[p], dtype=np.float64) for p in expected_perts]
                )

        if pred_genes != expected_genes:
            raise ValueError("Predictions gene_names order mismatch")

        if pred_perts != expected_perts:
            raise ValueError("Predictions pert_names order mismatch")

        if pred_matrix.shape != (len(expected_perts), len(expected_genes)):
            raise ValueError("Predictions matrix shape mismatch")

        if not np.isfinite(pred_matrix).all():
            raise ValueError("Predictions matrix contains NaN or Inf")

        return AgentResponse(
            answer={
                "predictions_path": str(pred_path),
                "predictions_sha256": actual_pred_sha,
                "registry_sha256": actual_reg_sha,
                "predictor_mode": self.predictor_mode,
            },
            metadata={
                "artifact_count": len(expected_perts),
                "slurm_job_id": str(registry.get("slurm_job_id", "")),
                "predictor_mode": self.predictor_mode,
                "checkpoint_sha256": str(registry.get("checkpoint_sha256", "")),
                "registry_sha256": actual_reg_sha,
                "predictions_sha256": actual_pred_sha,
            },
        )
