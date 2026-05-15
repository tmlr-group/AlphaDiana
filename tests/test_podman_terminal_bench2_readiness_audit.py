from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import audit_podman_terminal_bench2_readiness as audit_mod


TASK_IDS = [
    "tb2_db-wal-recovery",
    "tb2_fix-git",
    "tb2_overfull-hbox",
    "tb2_adaptive-rejection-sampler",
    "tb2_break-filter-js-from-html",
]


def _write_config(config_dir: Path) -> None:
    data = {
        "run_id": "template",
        "agent": {"name": "terminal_bench2_opencode", "version": "1.0", "config": {"container_engine": "podman"}},
        "benchmark": {"name": "terminal_bench2", "config": {"task_ids": [task.removeprefix("tb2_") for task in TASK_IDS]}},
        "scorer": {"name": "terminal_bench2"},
        "metadata": {
            "run_suffix": "terminal_bench2_opencode",
            "container_engine": "podman",
            "selected_task_ids": TASK_IDS,
            "selected_task_names": [task.removeprefix("tb2_") for task in TASK_IDS],
        },
    }
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "terminal_bench2_opencode_pilot.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False),
        encoding="utf-8",
    )


def _write_preflight(path: Path, *, ok: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "failure_taxonomy": "clean" if ok else "provider_unreachable_from_podman",
        "failures": [] if ok else ["provider_unreachable_from_podman"],
        "selected_task_ids": TASK_IDS,
        "runtime_image_present": True,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _record(
    *,
    run_id: str,
    task_id: str,
    metadata: dict | None = None,
    score_status: str = "valid_scored",
    score: float | None = 0.0,
    error: dict | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "agent_name": "terminal_bench2_opencode",
        "benchmark_name": "terminal_bench2",
        "score_status": score_status,
        "score": score,
        "wall_time_sec": 1.5,
        "finish_reason": "",
        "metadata": metadata if metadata is not None else {
            "container_engine": "podman",
            "reward": "0",
            "verifier_status": "ok",
            "runner": "opencode",
        },
        "error": error,
        "artifact_manifest": {"local_artifact_root": task_id},
    }


def _write_task(results_dir: Path, logs_dir: Path, *, run_id: str, task_id: str, record: dict) -> None:
    task_dir = results_dir / run_id / run_id / "tasks"
    artifact_root = results_dir / run_id / run_id / "artifacts" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{run_id}.log").write_text("pilot log\n", encoding="utf-8")
    (task_dir / f"{task_id}.json").write_text(json.dumps([record]), encoding="utf-8")


def _prepare(tmp_path: Path, *, preflight_ok: bool = True) -> tuple[Path, Path, Path, Path, str]:
    config_dir = tmp_path / "configs"
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    preflight = tmp_path / "context" / "preflight-pilot.json"
    run_id = "pilot_terminal_bench2_opencode"
    _write_config(config_dir)
    _write_preflight(preflight, ok=preflight_ok)
    return config_dir, results_dir, logs_dir, preflight, run_id


def _run_audit(tmp_path: Path, config_dir: Path, results_dir: Path, logs_dir: Path, preflight: Path) -> dict:
    return audit_mod.audit(
        run_prefix="pilot",
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        root=tmp_path,
        preflight_status_file=preflight,
    )


def test_audit_passes_score_zero_and_verifier_failure_when_rows_are_auditable(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        metadata = {
            "container_engine": "podman",
            "reward": "0",
            "verifier_status": "failed" if index == 1 else "ok",
            "runner": "opencode",
        }
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id, metadata=metadata, score=0.0),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is True
    assert result["expected_tasks"] == 5
    assert result["taxonomy_summary"]["verifier_failure"] == 1
    assert all(row["audit_status"] == "pass" for row in result["rows"])


def test_audit_fails_missing_podman_metadata(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for task_id in TASK_IDS:
        metadata = {"container_engine": "docker", "reward": "0", "verifier_status": "ok"}
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id, metadata=metadata),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    assert result["taxonomy_summary"] == {"metadata_defect": 5}
    assert all("missing_metadata_container_engine" in row["audit_failures"] for row in result["rows"])


def test_audit_fails_missing_task_json_with_explicit_no_task_row(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for task_id in TASK_IDS[:-1]:
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id),
        )
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{run_id}.log").write_text("pilot log\n", encoding="utf-8")

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    missing = [row for row in result["rows"] if row["result_status"] == "no_task_json"]
    assert result["audit_passed"] is False
    assert len(missing) == 1
    assert missing[0]["task_id"] == TASK_IDS[-1]
    assert missing[0]["log_path"].endswith(f"{run_id}.log")
    assert missing[0]["failure_taxonomy"] == "no_task_json"


def test_audit_fails_preflight_failure_even_with_complete_rows(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path, preflight_ok=False)
    for task_id in TASK_IDS:
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["preflight"]["ok"] is False
    assert result["audit_passed"] is False
    assert result["audit_failures"] == ["preflight_failed"]
