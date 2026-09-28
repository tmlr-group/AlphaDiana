"""Execute explicit YAML cells through public APIs and retain every check receipt.

This is a validation driver, not a new execution backend. A manifest contains
``model`` and ``cells`` with ``id`` and ``config`` paths. The underlying adapters
own all benchmark/harness-specific configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    failures = 0
    for cell in manifest["cells"]:
        if args.only and cell["id"] not in args.only:
            continue
        receipt = {"id": cell["id"], "config": cell["config"], "status": "failed"}
        try:
            config = ExperimentConfig.from_yaml(cell["config"])
            if (
                config.agent_config.get(cell.get("model_config_key", "model"))
                != manifest["model"]
            ):
                raise ValueError("Cell model differs from the pinned matrix model")
            if config.num_samples != 1 or config.task_retries != 0:
                raise ValueError("Acceptance cells require one sample and no retries")
            config_path = Path(cell["config"])
            receipt["config_sha256"] = hashlib.sha256(
                config_path.read_bytes()
            ).hexdigest()
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
