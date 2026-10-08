"""Execute explicit YAML cells through public APIs and retain every check receipt.

This is a validation driver, not a new execution backend. A manifest contains
``model`` and ``cells`` with ``id`` and ``config`` paths. The underlying adapters
own all benchmark/harness-specific configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import traceback
from dataclasses import asdict
from pathlib import Path

import alphadiana as ad
from alphadiana.engine.config.experiment_config import ExperimentConfig


def execute(config: ExperimentConfig):
    options = asdict(config)
    for key in (
        "agent_name",
        "agent_version",
        "agent_config",
        "benchmark_name",
        "benchmark_config",
        "sandbox_name",
        "sandbox_config",
        "scorer_name",
        "scorer_config",
    ):
        options.pop(key)
    env = (
        ad.environment(config.sandbox_name, **config.sandbox_config)
        if config.sandbox_name
        else None
    )
    return ad.runner(env=env, **options).compute(
        agent=ad.agent(
            config.agent_name, version=config.agent_version, **config.agent_config
        ),
        data=ad.dataset(config.benchmark_name, **config.benchmark_config),
        scorer=ad.scorer.create(config.scorer_name, **config.scorer_config),
    )


def task_fingerprint(task, fixture: dict) -> str:
    """Hash semantic task inputs, named metadata and optional local task assets."""
    payload = {
        "task_id": task.task_id,
        "problem": task.problem,
        "ground_truth": task.ground_truth,
        "attachments": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in sorted(task.attachments.items())
        },
        "metadata": {
            key: task.metadata[key] for key in fixture.get("metadata_fields", [])
        },
    }
    tree_field = fixture.get("tree_metadata_field")
    if tree_field:
        root = Path(task.metadata[tree_field])
        files = {}
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise ValueError(
                    f"Pinned task assets must not contain symlinks: {path}"
                )
            if path.is_file():
                files[path.relative_to(root).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        if not files:
            raise ValueError(f"Pinned task asset tree is empty: {root}")
        payload["asset_files"] = files
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_inputs(config, cell: dict, fixture: dict) -> dict:
    from alphadiana.benchmarks.registry import BenchmarkRegistry
    from alphadiana.engine.components import register_builtin_components

    register_builtin_components()
    tasks = BenchmarkRegistry.get(config.benchmark_name)().load_tasks(
        config.benchmark_config
    )
    if [task.task_id for task in tasks] != cell["task_ids"]:
        raise ValueError("Loaded tasks differ from the pinned task sequence")
    observed = {task.task_id: task_fingerprint(task, fixture) for task in tasks}
    if observed != fixture["task_fingerprints"]:
        raise ValueError("Benchmark input content differs from the pinned fixture")
    return observed


def source_state() -> dict:
    root = Path(__file__).resolve().parents[1]

    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    changed = subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=root)
    untracked = {}
    for name in git("ls-files", "--others", "--exclude-standard").splitlines():
        path = root / name
        if path.is_file():
            untracked[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    digest = hashlib.sha256(
        changed + json.dumps(untracked, sort_keys=True).encode()
    ).hexdigest()
    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
        "changes_sha256": digest,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    ids = [cell["id"] for cell in manifest["cells"]]
    if not ids or len(set(ids)) != len(ids):
        parser.error("Manifest requires nonempty, unique cell IDs")
    if manifest.get("expected_cells", len(ids)) != len(ids):
        parser.error("Manifest cell count differs from expected_cells")
    if args.only is not None and (not args.only or set(args.only) - set(ids)):
        parser.error("--only requires one or more known cell IDs")
    fixtures = {}
    if manifest.get("fixtures"):
        fixtures = json.loads((args.manifest.parent / manifest["fixtures"]).read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    failures = 0
    for cell in manifest["cells"]:
        if args.only and cell["id"] not in args.only:
            continue
        receipt = {"id": cell["id"], "config": cell["config"], "status": "failed"}
        try:
            config_path = Path(cell["config"])
            if not config_path.is_absolute():
                config_path = args.manifest.parent / config_path
            config = ExperimentConfig.from_yaml(config_path)
            receipt["source"] = source_state()
            if manifest.get("require_clean_source") and receipt["source"]["dirty"]:
                raise ValueError("Acceptance requires a clean source tree")
            if manifest.get("require_pins") and (
                not cell.get("task_ids") or cell.get("fixture") not in fixtures
            ):
                raise ValueError("Acceptance cell lacks pinned input evidence")
            if cell.get("fixture") in fixtures:
                receipt["input_fingerprints"] = validate_inputs(
                    config, cell, fixtures[cell["fixture"]]
                )
            if (
                config.agent_config.get(cell.get("model_config_key", "model"))
                != manifest["model"]
            ):
                raise ValueError("Cell model differs from the pinned matrix model")
            if config.num_samples != 1 or config.task_retries != 0:
                raise ValueError("Acceptance cells require one sample and no retries")
            receipt["config_sha256"] = hashlib.sha256(
                config_path.read_bytes()
            ).hexdigest()
            receipt["resolved_config_sha256"] = hashlib.sha256(
                json.dumps(asdict(config), sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
            if manifest.get("runtime_provenance"):
                provenance = manifest["runtime_provenance"]
                provenance_path = args.manifest.parent / provenance["path"]
                observed = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
                if observed != provenance["sha256"]:
                    raise ValueError("Runtime provenance file changed")
                receipt["runtime_provenance"] = {
                    "path": str(provenance_path.resolve()), "sha256": observed,
                }
            receipt["model"] = manifest["model"]
            artifacts = execute(config)
            rows = artifacts.records()
            expected = cell.get("expected_tasks", 3)
            valid = sum(r["score_status"] == "valid_scored" for r in rows)
            receipt.update(
                records=len(rows),
                valid_scored=valid,
                artifacts=str(artifacts.path),
                task_ids=[r["task_id"] for r in rows],
            )
            if len(rows) != expected or valid != expected:
                raise RuntimeError(
                    f"Expected {expected} valid records; observed {len(rows)} records/{valid} valid"
                )
            if len(set(receipt["task_ids"])) != expected:
                raise RuntimeError("Cell contains duplicate task identities")
            if "task_ids" in cell and receipt["task_ids"] != cell["task_ids"]:
                raise RuntimeError(
                    "Observed tasks differ from the pinned task sequence"
                )
            receipt["task_ids_pinned"] = "task_ids" in cell
            receipt["artifacts_sha256"] = hashlib.sha256(
                artifacts.path.read_bytes()
            ).hexdigest()
            report = ad.analyzer(artifacts).compute()
            receipt["analysis"] = report
            if source_state() != receipt["source"]:
                raise RuntimeError(
                    "Source state changed during the cell; rerun on a fixed revision"
                )
            receipt["status"] = "passed"
        except Exception:  # noqa: BLE001 - retain failed-cell evidence and continue the matrix
            receipt["failure"] = traceback.format_exc()
            failures += 1
        with (args.output / "receipts.jsonl").open("a") as stream:
            stream.write(json.dumps(receipt, allow_nan=False) + "\n")
        print(cell["id"], receipt["status"], receipt.get("failure", ""), flush=True)
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
