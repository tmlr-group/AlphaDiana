"""Native AlphaDiana GEARS perturbation benchmark."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.engine.components import register_benchmark


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


@register_benchmark("gears_perturb")
class GearsPerturbBenchmark(Benchmark):
    """Create public GEARS perturbation tasks without exposing private ground truth."""

    name = "gears_perturb"

    def load_tasks(self, config: dict) -> list[BenchmarkTask]:
        raw_path = config.get("public_manifest")
        if not raw_path:
            raise ValueError("public_manifest is required")

        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Public manifest not found: {path}")

        manifest_sha = sha256_file(path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("schema") != "gears-perturb-public-v1":
            raise ValueError("Invalid GEARS public manifest schema")

        gene_names = manifest.get("gene_names")
        n_genes = manifest.get("n_genes")
        candidates = manifest.get("candidate_perts")
        pert_subtypes = manifest.get("pert_subtypes", {})

        if (
            not isinstance(gene_names, list)
            or not gene_names
            or any(not isinstance(g, str) or not g for g in gene_names)
        ):
            raise ValueError("gene_names must be a non-empty list of strings")

        if not isinstance(n_genes, int) or n_genes != len(gene_names):
            raise ValueError("n_genes does not match gene_names length")

        if (
            not isinstance(candidates, list)
            or not candidates
            or any(not isinstance(p, str) or not p for p in candidates)
            or len(candidates) != len(set(candidates))
        ):
            raise ValueError("candidate_perts must be a non-empty list of unique strings")

        if not isinstance(pert_subtypes, dict):
            raise ValueError("pert_subtypes must be a mapping")

        dataset_name = str(manifest.get("dataset_name", "norman"))
        split = str(manifest.get("split", "simulation"))
        seed = int(manifest.get("seed", 42))

        task = BenchmarkTask(
            task_id=str(
                config.get(
                    "task_id",
                    f"gears_perturb:{dataset_name}_{split}_seed{seed}",
                )
            ),
            problem=(
                f"Predict post-perturbation gene expression across {n_genes} genes "
                f"for {len(candidates)} perturbations ({dataset_name}, split={split}, seed={seed})."
            ),
            ground_truth=None,
            metadata={
                "public_manifest": str(path),
                "public_manifest_sha256": manifest_sha,
                "dataset_name": dataset_name,
                "split": split,
                "seed": seed,
                "n_genes": n_genes,
                "gene_names": list(gene_names),
                "candidate_perts": list(candidates),
                "pert_subtypes": {str(k): str(v) for k, v in pert_subtypes.items()},
            },
        )
        return [task]

    def default_scorer(self) -> str:
        return "gears_perturb"
