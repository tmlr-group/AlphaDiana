"""Native GEARS perturbation scorer aligned with Table 3 / Figure 3 metrics."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from alphadiana.engine.components import register_scorer
from alphadiana.scorer.base import Scorer, ScoreResult


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@register_scorer("gears_perturb")
class GearsPerturbScorer(Scorer):
    """Score GEARS perturbation predictions against frozen private ground truth."""

    @property
    def name(self) -> str:
        return "gears_perturb"

    def setup(self, config: dict) -> None:
        self.config = dict(config)
        for key in (
            "private_manifest",
            "private_sha256",
            "registry_path",
            "registry_sha256",
        ):
            if not config.get(key):
                raise ValueError(f"Missing GEARS scorer config: {key}")

        private_path = Path(config["private_manifest"]).expanduser().resolve()
        if not private_path.is_file():
            raise FileNotFoundError(f"Private manifest not found: {private_path}")

        if sha256_file(private_path) != config["private_sha256"]:
            raise ValueError("Private manifest SHA-256 mismatch")

        private = json.loads(private_path.read_text(encoding="utf-8"))
        if not isinstance(private, dict) or private.get("schema") != "gears-perturb-private-v1":
            raise ValueError("Invalid GEARS private manifest schema")

        gt_raw = private.get("ground_truth_path")
        gt_sha = private.get("ground_truth_sha256")
        if not gt_raw or not gt_sha:
            raise ValueError("Private manifest missing ground_truth_path or ground_truth_sha256")

        gt_path = Path(gt_raw).expanduser()
        if not gt_path.is_absolute():
            gt_path = (private_path.parent / gt_path).resolve()
        else:
            gt_path = gt_path.resolve()

        if not gt_path.is_file():
            raise FileNotFoundError(f"Private ground truth NPZ not found: {gt_path}")

        if sha256_file(gt_path) != gt_sha:
            raise ValueError("Private ground truth NPZ SHA-256 mismatch")

        registry_path = Path(config["registry_path"]).expanduser().resolve()
        if not registry_path.is_file():
            raise FileNotFoundError(f"Registry file not found: {registry_path}")

        if sha256_file(registry_path) != config["registry_sha256"]:
            raise ValueError("Scorer registry SHA-256 mismatch")

        threshold = config.get("pass_threshold", 0.0)
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
        ):
            raise ValueError("Invalid pass_threshold")

        self.private_path = private_path
        self.private = private
        self.gt_path = gt_path
        self.registry_path = registry_path
        self.threshold = float(threshold)

    def score(self, task, response) -> ScoreResult:
        import numpy as np
        from scipy.stats import pearsonr

        if task.ground_truth is not None:
            raise ValueError("Private truth must not enter Agent tasks")

        if (
            self.private.get("public_manifest_sha256")
            != task.metadata.get("public_manifest_sha256")
        ):
            raise ValueError("Private/public manifest SHA-256 binding mismatch")

        if sha256_file(self.private_path) != self.config["private_sha256"]:
            raise ValueError("Private manifest SHA-256 mismatch at score time")

        if sha256_file(self.gt_path) != self.private["ground_truth_sha256"]:
            raise ValueError("Private ground truth NPZ SHA-256 mismatch at score time")

        if sha256_file(self.registry_path) != self.config["registry_sha256"]:
            raise ValueError("Registry SHA-256 mismatch at score time")

        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        if (
            not isinstance(registry, dict)
            or registry.get("schema") != "gears-perturb-registry-v1"
        ):
            raise ValueError("Invalid GEARS registry schema")

        if registry.get("public_manifest_sha256") != task.metadata.get("public_manifest_sha256"):
            raise ValueError("Registry public_manifest_sha256 mismatch")

        answer = response.answer
        if not isinstance(answer, dict):
            raise ValueError("Agent response answer must be a dict")

        for field in (
            "predictions_path",
            "predictions_sha256",
            "registry_sha256",
            "predictor_mode",
        ):
            if not answer.get(field):
                raise ValueError(f"Missing field in Agent response answer: {field}")

        if answer["registry_sha256"] != self.config["registry_sha256"]:
            raise ValueError("Response registry_sha256 does not match pinned scorer registry_sha256")

        if answer["predictor_mode"] != registry.get("predictor_mode"):
            raise ValueError("Response predictor_mode does not match registry predictor_mode")

        pred_path = Path(answer["predictions_path"]).expanduser().resolve()
        reg_pred_path = Path(registry["predictions_path"]).expanduser().resolve()
        if pred_path != reg_pred_path:
            raise ValueError("Response predictions_path does not match registry predictions_path")

        if not pred_path.is_file():
            raise FileNotFoundError(f"Predictions NPZ not found: {pred_path}")

        actual_pred_sha = sha256_file(pred_path)
        if (
            actual_pred_sha != registry.get("predictions_sha256")
            or actual_pred_sha != answer["predictions_sha256"]
        ):
            raise ValueError("Predictions NPZ SHA-256 mismatch against pinned registry")

        expected_genes = list(task.metadata["gene_names"])
        expected_perts = list(task.metadata["candidate_perts"])
        pert_subtypes = dict(task.metadata.get("pert_subtypes", {}))

        with np.load(self.gt_path, allow_pickle=False) as gt_npz:
            gt_genes = [str(x) for x in gt_npz["gene_names"].tolist()]
            gt_perts = [str(x) for x in gt_npz["pert_names"].tolist()]
            ctrl_mean = np.asarray(gt_npz["ctrl_mean"], dtype=np.float64)
            true_matrix = np.asarray(gt_npz["true_matrix"], dtype=np.float64)
            top20_de_idx = np.asarray(gt_npz["top20_de_idx"], dtype=np.int64)

        if gt_genes != expected_genes:
            raise ValueError("Ground truth gene_names mismatch")
        if gt_perts != expected_perts:
            raise ValueError("Ground truth pert_names mismatch")
        if (
            top20_de_idx.ndim != 2
            or top20_de_idx.shape[0] != len(expected_perts)
            or top20_de_idx.shape[1] < 1
            or top20_de_idx.shape[1] > len(expected_genes)
            or int(top20_de_idx.min()) < 0
            or int(top20_de_idx.max()) >= len(expected_genes)
        ):
            raise ValueError("Invalid top20_de_idx shape or out-of-range gene indices")

        with np.load(pred_path, allow_pickle=False) as pred_npz:
            pred_genes = [str(x) for x in pred_npz["gene_names"].tolist()]
            if "pert_names" in pred_npz and "pred_matrix" in pred_npz:
                pred_perts = [str(x) for x in pred_npz["pert_names"].tolist()]
                pred_matrix = np.asarray(pred_npz["pred_matrix"], dtype=np.float64)
            else:
                pred_perts = [p for p in expected_perts if p in pred_npz]
                if len(pred_perts) != len(expected_perts):
                    raise ValueError("Missing perturbations in predictions NPZ")
                pred_matrix = np.vstack(
                    [np.asarray(pred_npz[p], dtype=np.float64) for p in expected_perts]
                )

        if pred_genes != expected_genes:
            raise ValueError("Predicted gene_names order mismatch")
        if pred_perts != expected_perts:
            raise ValueError("Predicted pert_names order mismatch")
        if pred_matrix.shape != true_matrix.shape:
            raise ValueError(
                f"Predicted matrix shape {pred_matrix.shape} != truth {true_matrix.shape}"
            )
        if not np.isfinite(pred_matrix).all():
            raise ValueError("Predictions contain non-finite values (NaN or Inf)")

        p_delta_list: list[float] = []
        all_pearson_list: list[float] = []
        mae_list: list[float] = []
        mse_top20_list: list[float] = []
        by_subtype_rows: dict[str, list[dict[str, float]]] = {}

        for i, pert in enumerate(expected_perts):
            mu_pred = pred_matrix[i]
            mu_true = true_matrix[i]
            delta_pred = mu_pred - ctrl_mean
            delta_true = mu_true - ctrl_mean
            idx = top20_de_idx[i]

            dp_sub = delta_pred[idx]
            dt_sub = delta_true[idx]
            if np.std(dp_sub) < 1e-12 or np.std(dt_sub) < 1e-12:
                r_delta = 0.0
            else:
                r_val = float(pearsonr(dp_sub, dt_sub)[0])
                r_delta = r_val if math.isfinite(r_val) else 0.0

            if np.std(mu_pred) < 1e-12 or np.std(mu_true) < 1e-12:
                r_all = 0.0
            else:
                r_all_val = float(pearsonr(mu_pred, mu_true)[0])
                r_all = r_all_val if math.isfinite(r_all_val) else 0.0

            mae_val = float(np.mean(np.abs(mu_pred - mu_true)))
            mse_top20_val = float(np.mean((mu_pred[idx] - mu_true[idx]) ** 2))

            p_delta_list.append(r_delta)
            all_pearson_list.append(r_all)
            mae_list.append(mae_val)
            mse_top20_list.append(mse_top20_val)

            subtype = pert_subtypes.get(pert, "unknown")
            by_subtype_rows.setdefault(subtype, []).append(
                {
                    "p_delta_top20": r_delta,
                    "all_gene_pearson": r_all,
                    "mae": mae_val,
                    "mse_top20": mse_top20_val,
                }
            )

        p_delta_top20 = float(np.mean(p_delta_list))
        all_gene_pearson = float(np.mean(all_pearson_list))
        mae = float(np.mean(mae_list))
        mse_top20 = float(np.mean(mse_top20_list))

        subtype_summary = {}
        for subtype, items in sorted(by_subtype_rows.items()):
            subtype_summary[subtype] = {
                "count": len(items),
                "p_delta_top20": float(np.mean([x["p_delta_top20"] for x in items])),
                "all_gene_pearson": float(np.mean([x["all_gene_pearson"] for x in items])),
                "mae": float(np.mean([x["mae"] for x in items])),
                "mse_top20": float(np.mean([x["mse_top20"] for x in items])),
            }

        return ScoreResult(
            correct=bool(p_delta_top20 >= self.threshold),
            score=p_delta_top20,
            expected={"pass_threshold": self.threshold},
            predicted={
                "p_delta_top20": p_delta_top20,
                "all_gene_pearson": all_gene_pearson,
                "mae": mae,
                "mse_top20": mse_top20,
            },
            rationale=(
                f"GEARS Norman evaluation (mode={answer['predictor_mode']}): "
                f"p_delta_top20={p_delta_top20:.4f}, "
                f"all_gene_pearson={all_gene_pearson:.4f}, "
                f"mae={mae:.4f}, mse_top20={mse_top20:.4f}"
            ),
            metadata={
                "p_delta_top20": p_delta_top20,
                "all_gene_pearson": all_gene_pearson,
                "mae": mae,
                "mse_top20": mse_top20,
                "n_perts": len(expected_perts),
                "predictor_mode": answer["predictor_mode"],
                "subtype_metrics": subtype_summary,
                "registry_sha256": answer["registry_sha256"],
                "predictions_sha256": actual_pred_sha,
                "slurm_job_id": str(registry.get("slurm_job_id", "")),
            },
        )
