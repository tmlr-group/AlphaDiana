"""CPU unit tests for the native GEARS perturbation benchmark, agent, and scorer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml
from click.testing import CliRunner

from alphadiana.benchmarks.gears_perturb.benchmark import GearsPerturbBenchmark
from alphadiana.benchmarks.gears_perturb.scorer import GearsPerturbScorer
from alphadiana.cli.commands import main
from alphadiana.engine.components import (
    AgentRegistry,
    BenchmarkRegistry,
    ScorerRegistry,
    register_builtin_components,
)
from alphadiana.harness.gears.agent import GearsAgent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_synthetic_fixture(tmp_path: Path) -> dict:
    """Create a 10-gene x 3-perturbation synthetic fixture on CPU."""
    rng = np.random.default_rng(42)
    gene_names = [f"ENSG000000000{i:02d}" for i in range(10)]
    perts = ["FEV", "CBFA2T3", "FEV+CBFA2T3"]
    subtypes = {
        "FEV": "unseen_single",
        "CBFA2T3": "seen_single",
        "FEV+CBFA2T3": "combo_seen1",
    }

    # Control baseline with strong gene-to-gene baseline variation
    ctrl_mean = np.linspace(1.0, 5.0, 10, dtype=np.float64)
    true_deltas = np.array(
        [
            [0.8, -0.6, 0.5, -0.4, 0.3, -0.2, 0.1, -0.1, 0.05, -0.05],
            [-0.7, 0.5, -0.4, 0.6, -0.3, 0.2, -0.15, 0.1, -0.05, 0.05],
            [1.1, -0.9, 0.7, -0.5, 0.4, -0.3, 0.2, -0.2, 0.1, -0.1],
        ],
        dtype=np.float64,
    )
    true_matrix = ctrl_mean[None, :] + true_deltas

    # Top DE indices (all 10 genes sorted by |delta| descending)
    top_de_idx = np.argsort(-np.abs(true_deltas), axis=1).astype(np.int64)

    public_manifest_path = tmp_path / "public_manifest.json"
    public_manifest = {
        "schema": "gears-perturb-public-v1",
        "dataset_name": "norman",
        "split": "simulation",
        "seed": 42,
        "gene_names": gene_names,
        "n_genes": len(gene_names),
        "candidate_perts": perts,
        "pert_subtypes": subtypes,
    }
    public_manifest_path.write_text(json.dumps(public_manifest, indent=2), encoding="utf-8")
    pub_sha = _sha256(public_manifest_path)

    gt_npz_path = tmp_path / "private_ground_truth.npz"
    np.savez(
        gt_npz_path,
        gene_names=np.array(gene_names),
        pert_names=np.array(perts),
        ctrl_mean=ctrl_mean,
        true_matrix=true_matrix,
        top20_de_idx=top_de_idx,
    )
    gt_sha = _sha256(gt_npz_path)

    private_manifest_path = tmp_path / "private_manifest.json"
    private_manifest = {
        "schema": "gears-perturb-private-v1",
        "dataset_name": "norman",
        "split": "simulation",
        "seed": 42,
        "public_manifest_sha256": pub_sha,
        "ground_truth_path": str(gt_npz_path),
        "ground_truth_sha256": gt_sha,
    }
    private_manifest_path.write_text(json.dumps(private_manifest, indent=2), encoding="utf-8")
    priv_sha = _sha256(private_manifest_path)

    # 1. GEARS predictions (close to true_matrix)
    gears_pred = true_matrix + rng.normal(0.0, 0.03, size=true_matrix.shape)
    # 2. cell_mean predictions (exact control mean for every pert)
    cell_mean_pred = np.tile(ctrl_mean, (len(perts), 1))
    # 3. train_mean predictions (mean of training perturbations)
    train_mean_vec = ctrl_mean + np.array([0.1, -0.05, 0.08, -0.02, 0.04, -0.03, 0.01, -0.01, 0.0, 0.0])
    train_mean_pred = np.tile(train_mean_vec, (len(perts), 1))

    modes = {
        "gears": gears_pred,
        "cell_mean": cell_mean_pred,
        "train_mean": train_mean_pred,
    }

    registries = {}
    for mode, matrix in modes.items():
        pred_path = tmp_path / f"predictions_{mode}.npz"
        np.savez(
            pred_path,
            gene_names=np.array(gene_names),
            pert_names=np.array(perts),
            pred_matrix=matrix,
            **{p: matrix[idx] for idx, p in enumerate(perts)},
        )
        pred_sha = _sha256(pred_path)
        reg_path = tmp_path / f"registry_{mode}.json"
        reg_data = {
            "schema": "gears-perturb-registry-v1",
            "run_id": f"gears_norman_seed42_{mode}",
            "predictor_mode": mode,
            "public_manifest_sha256": pub_sha,
            "checkpoint_sha256": "a" * 64 if mode == "gears" else f"baseline:{mode}",
            "predictions_path": str(pred_path),
            "predictions_sha256": pred_sha,
            "slurm_job_id": "300001",
        }
        reg_path.write_text(json.dumps(reg_data, indent=2), encoding="utf-8")
        registries[mode] = {
            "registry_path": str(reg_path),
            "registry_sha256": _sha256(reg_path),
            "predictions_path": str(pred_path),
            "predictions_sha256": pred_sha,
        }

    return {
        "public_manifest": str(public_manifest_path),
        "public_sha256": pub_sha,
        "private_manifest": str(private_manifest_path),
        "private_sha256": priv_sha,
        "gt_npz": str(gt_npz_path),
        "registries": registries,
    }


def test_component_registration():
    register_builtin_components()
    assert BenchmarkRegistry.get("gears_perturb") is GearsPerturbBenchmark
    assert AgentRegistry.get("gears") is GearsAgent
    assert ScorerRegistry.get("gears_perturb") is GearsPerturbScorer


def test_gears_end_to_end_and_cell_mean_zero_delta(tmp_path: Path):
    fx = _build_synthetic_fixture(tmp_path)

    benchmark = GearsPerturbBenchmark()
    tasks = benchmark.load_tasks({"public_manifest": fx["public_manifest"]})
    assert len(tasks) == 1
    task = tasks[0]
    assert task.ground_truth is None
    assert task.metadata["n_genes"] == 10
    assert len(task.metadata["candidate_perts"]) == 3

    results = {}
    for mode in ("gears", "cell_mean", "train_mean"):
        reg_info = fx["registries"][mode]
        agent = GearsAgent()
        agent.setup(
            {
                "public_manifest": fx["public_manifest"],
                "registry_path": reg_info["registry_path"],
                "registry_sha256": reg_info["registry_sha256"],
                "predictor_mode": mode,
            }
        )
        response = agent.solve(task)
        assert response.answer["predictor_mode"] == mode
        assert response.metadata["artifact_count"] == 3

        scorer = GearsPerturbScorer()
        scorer.setup(
            {
                "private_manifest": fx["private_manifest"],
                "private_sha256": fx["private_sha256"],
                "registry_path": reg_info["registry_path"],
                "registry_sha256": reg_info["registry_sha256"],
                "pass_threshold": 0.0,
            }
        )
        score_res = scorer.score(task, response)
        results[mode] = score_res

    # Verify cell_mean has exact 0.0 p_delta_top20 while high all_gene_pearson
    assert results["cell_mean"].score == 0.0
    assert results["cell_mean"].metadata["p_delta_top20"] == 0.0
    assert results["cell_mean"].metadata["all_gene_pearson"] > 0.90

    # Verify GEARS strongly outperforms both baselines on p_delta_top20 and mse_top20
    assert results["gears"].score > 0.95
    assert results["gears"].score > results["train_mean"].score
    assert results["gears"].metadata["mse_top20"] < results["cell_mean"].metadata["mse_top20"]
    assert "unseen_single" in results["gears"].metadata["subtype_metrics"]
    assert "combo_seen1" in results["gears"].metadata["subtype_metrics"]


def test_private_truth_leak_and_sha256_tamper_rejected(tmp_path: Path):
    fx = _build_synthetic_fixture(tmp_path)
    reg_info = fx["registries"]["gears"]

    benchmark = GearsPerturbBenchmark()
    task = benchmark.load_tasks({"public_manifest": fx["public_manifest"]})[0]

    agent = GearsAgent()
    agent.setup(
        {
            "public_manifest": fx["public_manifest"],
            "registry_path": reg_info["registry_path"],
            "registry_sha256": reg_info["registry_sha256"],
            "predictor_mode": "gears",
        }
    )

    # 1. Private truth leak into task must be blocked by Agent and Scorer
    task.ground_truth = {"leaked": True}
    with pytest.raises(ValueError, match="Private truth must not enter Agent tasks"):
        agent.solve(task)
    task.ground_truth = None

    response = agent.solve(task)

    # 2. Registry SHA-256 tamper must be blocked
    with pytest.raises(ValueError, match="Registry SHA-256 mismatch"):
        bad_agent = GearsAgent()
        bad_agent.setup(
            {
                "public_manifest": fx["public_manifest"],
                "registry_path": reg_info["registry_path"],
                "registry_sha256": "0" * 64,
                "predictor_mode": "gears",
            }
        )
        bad_agent.solve(task)

    # 3. Predictions file tamper after Agent solve must be blocked by Scorer
    scorer = GearsPerturbScorer()
    scorer.setup(
        {
            "private_manifest": fx["private_manifest"],
            "private_sha256": fx["private_sha256"],
            "registry_path": reg_info["registry_path"],
            "registry_sha256": reg_info["registry_sha256"],
        }
    )
    pred_file = Path(reg_info["predictions_path"])
    original_bytes = pred_file.read_bytes()
    try:
        pred_file.write_bytes(original_bytes + b"corrupt")
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            scorer.score(task, response)
    finally:
        pred_file.write_bytes(original_bytes)


def test_cli_compute_gears_cpu(tmp_path: Path):
    fx = _build_synthetic_fixture(tmp_path)
    reg_info = fx["registries"]["gears"]
    cfg = {
        "run_id": "gears_cpu_unit_test",
        "output_dir": str(tmp_path / "results"),
        "max_concurrent": 1,
        "num_samples": 1,
        "task_retries": 0,
        "agent": {
            "name": "gears",
            "version": "0.1.0",
            "config": {
                "public_manifest": fx["public_manifest"],
                "registry_path": reg_info["registry_path"],
                "registry_sha256": reg_info["registry_sha256"],
                "predictor_mode": "gears",
            },
        },
        "benchmark": {
            "name": "gears_perturb",
            "config": {
                "public_manifest": fx["public_manifest"],
                "task_id": "gears_perturb:norman_seed42_unit",
            },
        },
        "scorer": {
            "name": "gears_perturb",
            "config": {
                "private_manifest": fx["private_manifest"],
                "private_sha256": fx["private_sha256"],
                "registry_path": reg_info["registry_path"],
                "registry_sha256": reg_info["registry_sha256"],
                "pass_threshold": 0.5,
            },
        },
    }
    yaml_path = tmp_path / "gears_unit.yaml"
    yaml_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    res = CliRunner().invoke(main, ["compute", str(yaml_path)])
    assert res.exit_code == 0, res.output
    jsonl_path = tmp_path / "results" / "gears_cpu_unit_test.jsonl"
    assert jsonl_path.is_file()
    row = json.loads(jsonl_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["score_status"] == "valid_scored"
    assert row["score"] > 0.95


def test_config_validator_gears(tmp_path: Path):
    fx = _build_synthetic_fixture(tmp_path)
    reg_info = fx["registries"]["gears"]
    valid_cfg = {
        "run_id": "gears_validate_test",
        "output_dir": str(tmp_path / "results"),
        "max_concurrent": 1,
        "num_samples": 1,
        "task_retries": 0,
        "agent": {
            "name": "gears",
            "version": "0.1.0",
            "config": {
                "public_manifest": fx["public_manifest"],
                "registry_path": reg_info["registry_path"],
                "registry_sha256": reg_info["registry_sha256"],
                "predictor_mode": "gears",
            },
        },
        "benchmark": {
            "name": "gears_perturb",
            "config": {
                "public_manifest": fx["public_manifest"],
            },
        },
        "scorer": {
            "name": "gears_perturb",
            "config": {
                "private_manifest": fx["private_manifest"],
                "private_sha256": fx["private_sha256"],
                "registry_path": reg_info["registry_path"],
                "registry_sha256": reg_info["registry_sha256"],
            },
        },
    }
    valid_yaml = tmp_path / "valid.yaml"
    valid_yaml.write_text(yaml.safe_dump(valid_cfg, sort_keys=False), encoding="utf-8")
    res_ok = CliRunner().invoke(main, ["validate", str(valid_yaml)])
    assert res_ok.exit_code == 0, res_ok.output
    assert "Config is valid." in res_ok.output

    invalid_cfg = dict(valid_cfg)
    invalid_cfg["agent"] = {
        "name": "gears",
        "version": "0.1.0",
        "config": {
            "public_manifest": fx["public_manifest"],
            "registry_path": reg_info["registry_path"],
            "registry_sha256": reg_info["registry_sha256"],
            "predictor_mode": "invalid_mode",
        },
    }
    invalid_yaml = tmp_path / "invalid.yaml"
    invalid_yaml.write_text(yaml.safe_dump(invalid_cfg, sort_keys=False), encoding="utf-8")
    res_bad = CliRunner().invoke(main, ["validate", str(invalid_yaml)])
    assert res_bad.exit_code != 0
    assert "predictor_mode" in res_bad.output
