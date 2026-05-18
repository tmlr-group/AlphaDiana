from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import audit_podman_swe_verified_readiness as audit_mod


TASK_IDS = [
    "swe_astropy__astropy-12907",
    "swe_astropy__astropy-13033",
]


def _write_config(config_dir: Path, taskset_dir: Path, *, agents: list[str] | None = None) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    taskset_dir.mkdir(parents=True, exist_ok=True)
    taskset_path = taskset_dir / "smoke.json"
    taskset_path.write_text(
        json.dumps({
            "tier": "smoke",
            "task_count": len(TASK_IDS),
            "selected_task_ids": TASK_IDS,
            "task_ids": [task.removeprefix("swe_") for task in TASK_IDS],
        }),
        encoding="utf-8",
    )
    for agent in agents or ["opencode"]:
        data = {
            "run_id": "template",
            "agent": {"name": agent, "version": "1.0", "config": {}},
            "benchmark": {
                "name": "swe_bench",
                "config": {
                    "taskset_path": str(taskset_path),
                    "max_tasks": len(TASK_IDS),
                },
            },
            "sandbox": {"name": "swebench_container", "config": {"container_engine": "podman"}},
            "scorer": {"name": "swe_bench", "config": {"container_engine": "podman"}},
            "metadata": {
                "tier": "smoke",
                "run_suffix": f"{agent}_smoke",
                "container_engine": "podman",
                "taskset_path": str(taskset_path),
            },
        }
        (config_dir / f"{agent}_smoke.yaml").write_text(
            yaml.safe_dump(data, sort_keys=False),
            encoding="utf-8",
        )


def _write_preflight(path: Path, *, ok: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ok": ok, "failures": [] if ok else ["provider_failure"]}), encoding="utf-8")


def _record(
    *,
    run_id: str,
    task_id: str,
    metadata: dict | None = None,
    sandbox_metadata: dict | None = None,
    score_status: str = "valid_scored",
    score: float | None = 0.0,
    error: dict | None = None,
    reasoning_trajectory: list | None = None,
    finish_reason: str = "",
) -> dict:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "agent_name": "opencode",
        "benchmark_name": "swe_bench",
        "score_status": score_status,
        "score": score,
        "correct": False if score is not None else None,
        "finish_reason": finish_reason,
        "predicted": "diff --git a/x b/x",
        "raw_output": "diff --git a/x b/x",
        "reasoning_trajectory": reasoning_trajectory or [],
        "trajectory": [{"role": "assistant", "content": "diff --git a/x b/x"}],
        "response_json": {},
        "metadata": metadata if metadata is not None else {"swe_bench_eval": {"container_engine": "podman"}},
        "sandbox_metadata": sandbox_metadata if sandbox_metadata is not None else {"container_engine": "podman"},
        "score_metadata": {"resolved": False},
        "error": error,
        "artifact_manifest": {
            "local_artifact_root": task_id,
            "files": {
                "sandbox_metadata": f"{task_id}/sandbox/sandbox_meta.json",
                "workspace_files": {
                    "/swebench_eval/demo/report.json": f"{task_id}/workspace/swebench_eval/demo/report.json",
                    "/swebench_eval/demo/run_instance.log": f"{task_id}/workspace/swebench_eval/demo/run_instance.log",
                    "/swebench_eval/demo/test_output.txt": f"{task_id}/workspace/swebench_eval/demo/test_output.txt",
                },
            },
        },
    }


def _write_task(results_dir: Path, logs_dir: Path, *, run_id: str, task_id: str, record: dict) -> None:
    run_root = results_dir / run_id / run_id
    task_dir = run_root / "tasks"
    artifact_root = run_root / "artifacts" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (artifact_root / "sandbox").mkdir(parents=True, exist_ok=True)
    (artifact_root / "workspace" / "swebench_eval" / "demo").mkdir(parents=True, exist_ok=True)
    (artifact_root / "sandbox" / "sandbox_meta.json").write_text("{}", encoding="utf-8")
    (artifact_root / "workspace" / "swebench_eval" / "demo" / "report.json").write_text("{}", encoding="utf-8")
    (artifact_root / "workspace" / "swebench_eval" / "demo" / "run_instance.log").write_text("log\n", encoding="utf-8")
    (artifact_root / "workspace" / "swebench_eval" / "demo" / "test_output.txt").write_text("tests\n", encoding="utf-8")
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{run_id}.log").write_text("run log\n", encoding="utf-8")
    (task_dir / f"{task_id}.json").write_text(json.dumps([record]), encoding="utf-8")


def _write_lifecycle(results_dir: Path, *, run_id: str, task_id: str, stages: list[str]) -> None:
    lifecycle_dir = results_dir / run_id / run_id / "lifecycle"
    lifecycle_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps({
            "run_id": run_id,
            "task_id": task_id,
            "sample_index": 0,
            "stage": stage,
            "timestamp": "2026-05-19T00:00:00+00:00",
            "metadata": {},
        })
        for stage in stages
    ]
    (lifecycle_dir / f"{task_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare(tmp_path: Path, *, preflight_ok: bool = True) -> tuple[Path, Path, Path, Path, str]:
    config_dir = tmp_path / "configs"
    taskset_dir = tmp_path / "tasksets"
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    preflight = tmp_path / "context" / "preflight.json"
    _write_config(config_dir, taskset_dir)
    _write_preflight(preflight, ok=preflight_ok)
    return config_dir, results_dir, logs_dir, preflight, "run_opencode_smoke"


def _run_audit(tmp_path: Path, config_dir: Path, results_dir: Path, logs_dir: Path, preflight: Path) -> dict:
    return audit_mod.audit(
        run_prefix="run",
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        root=tmp_path,
        output_dir=tmp_path / "context",
        preflight_status_file=preflight,
        tiers={"smoke"},
    )


def test_swe_audit_passes_score_zero_with_list_task_json(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for task_id in TASK_IDS:
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id, score=0.0),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is True
    assert result["expected_tasks"] == 2
    assert {row["score"] for row in result["rows"]} == {0.0}
    assert all(row["failure_category"] == "clean" for row in result["rows"])
    assert {row["expected_task_id"] for row in result["rows"]} == set(TASK_IDS)
    assert all(row["gating_failure"] is False for row in result["rows"])


def test_swe_audit_fails_missing_task_json_explicitly(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    _write_task(
        results_dir,
        logs_dir,
        run_id=run_id,
        task_id=TASK_IDS[0],
        record=_record(run_id=run_id, task_id=TASK_IDS[0]),
    )
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{run_id}.log").write_text("run log\n", encoding="utf-8")

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    missing = [row for row in result["rows"] if row["result_status"] == "no_task_json"]
    assert result["audit_passed"] is False
    assert len(missing) == 1
    assert missing[0]["expected_task_id"] == TASK_IDS[1]
    assert missing[0]["failure_category"] == "no_task_json"
    assert missing[0]["gating_failure"] is True
    assert "no_task_json" in missing[0]["audit_failures"]


def test_swe_audit_keeps_missing_json_row_visible_with_lifecycle(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{run_id}.log").write_text(
        "OpenClaw attempt 1/1 response headers received: status=200 content_type=text/event-stream\n"
        "OpenClaw attempt 1/1 streaming response established; waiting for SSE chunks\n",
        encoding="utf-8",
    )
    _write_lifecycle(
        results_dir,
        run_id=run_id,
        task_id=TASK_IDS[0],
        stages=["selected", "launched", "sandbox_started"],
    )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    row = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert result["audit_passed"] is False
    assert row["result_status"] == "no_task_json"
    assert row["failure_category"] == "no_task_json"
    assert row["lifecycle_exists"] is True
    assert row["last_lifecycle_stage"] == "sandbox_started"
    assert row["lifecycle_stage_flags"]["provider_connected"] is True
    assert row["lifecycle_stage_flags"]["task_json_written"] is False
    assert "no_task_json" in row["audit_failures"]


def test_swe_audit_fails_missing_podman_metadata_and_artifacts(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for task_id in TASK_IDS:
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(
                run_id=run_id,
                task_id=task_id,
                metadata={},
                sandbox_metadata={"container_engine": "docker"},
            ),
        )
    (results_dir / run_id / run_id / "artifacts" / TASK_IDS[0] / "sandbox" / "sandbox_meta.json").unlink()

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    assert result["taxonomy_summary"]["metadata_missing"] == 2
    assert any("missing_sandbox_metadata_artifact" in row["audit_failures"] for row in result["rows"])


def test_swe_audit_classifies_provider_empty_response_as_gating(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        metadata = {"swe_bench_eval": {"container_engine": "podman"}}
        if index == 0:
            metadata.update({
                "provider_proxy_empty_content_and_reasoning_response_count": 1,
                "provider_proxy_empty_content_reasoning_and_tool_response_count": 1,
            })
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id, metadata=metadata),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    failed = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert result["audit_passed"] is False
    assert failed["failure_category"] == "provider_empty_response"
    assert "provider_empty_response_observed" in failed["audit_failures"]


def test_swe_audit_reasoning_only_length_is_not_provider_empty(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        record = _record(run_id=run_id, task_id=task_id)
        if index == 0:
            record["predicted"] = ""
            record["raw_output"] = ""
            record["trajectory"] = [{"role": "assistant", "content": "", "thinking": "reasoning"}]
            record["reasoning_trajectory"] = [{"content": "reasoning"}]
            record["finish_reason"] = "length"
        _write_task(results_dir, logs_dir, run_id=run_id, task_id=task_id, record=record)

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    row = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert result["audit_passed"] is True
    assert row["failure_category"] == "reasoning_only_length"
    assert row["assistant_evidence_present"] is True
