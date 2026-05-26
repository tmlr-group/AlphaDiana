from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import audit_podman_terminal_bench2_readiness as audit_mod


TASK_IDS = [
    "tb2_db-wal-recovery",
    "tb2_overfull-hbox",
    "tb2_adaptive-rejection-sampler",
]


def _write_config(config_dir: Path, agents: list[str] | None = None) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    for agent in agents or ["terminal_bench2_opencode"]:
        data = {
            "run_id": "template",
            "agent": {"name": agent, "version": "1.0", "config": {"container_engine": "podman"}},
            "benchmark": {"name": "terminal_bench2", "config": {"task_ids": [task.removeprefix("tb2_") for task in TASK_IDS]}},
            "scorer": {"name": "terminal_bench2"},
            "metadata": {
                "run_suffix": agent,
                "container_engine": "podman",
                "selected_task_ids": TASK_IDS,
                "selected_task_names": [task.removeprefix("tb2_") for task in TASK_IDS],
            },
        }
        (config_dir / f"{agent}_pilot.yaml").write_text(
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
    agent_name: str = "terminal_bench2_opencode",
) -> dict:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "agent_name": agent_name,
        "benchmark_name": "terminal_bench2",
        "score_status": score_status,
        "score": score,
        "wall_time_sec": 1.5,
        "finish_reason": "",
        "raw_output": "assistant output",
        "reasoning_trajectory": [],
        "trajectory": [{"role": "assistant", "content": "assistant output"}],
        "response_json": {},
        "metadata": metadata if metadata is not None else {
            "container_engine": "podman",
            "reward": "0",
            "verifier_status": "ok",
            "verifier_reward_observed": True,
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
    (artifact_root / "artifact.txt").write_text("artifact\n", encoding="utf-8")
    files = record.get("artifact_manifest", {}).get("files", {})
    for relative_path in files.values():
        if isinstance(relative_path, str) and relative_path:
            artifact_file = results_dir / run_id / run_id / "artifacts" / relative_path
            artifact_file.parent.mkdir(parents=True, exist_ok=True)
            artifact_file.write_text("{}\n", encoding="utf-8")
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


def test_audit_passes_score_zero_and_skipped_duplicate_when_reward_observed(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        metadata = {
            "container_engine": "podman",
            "reward": "0",
            "verifier_status": "skipped_duplicate" if index == 1 else "ok",
            "verifier_reward_observed": True,
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
    assert result["expected_tasks"] == 3
    assert all(row["audit_status"] == "pass" for row in result["rows"])


def test_audit_uses_metadata_reward_when_top_level_reward_is_none(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for task_id in TASK_IDS:
        record = _record(run_id=run_id, task_id=task_id, score=0.0)
        record["reward"] = None
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=record,
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is True
    assert {row["reward"] for row in result["rows"]} == {"0"}


def test_audit_fails_verifier_failure_or_missing_reward(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        metadata = {
            "container_engine": "podman",
            "reward": "" if index == 2 else "0",
            "verifier_status": "failed" if index == 1 else "ok",
            "verifier_reward_observed": index != 2,
        }
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id, metadata=metadata, score=0.0),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    assert result["taxonomy_summary"]["verifier_failure"] == 2
    assert result["audit_failure_count"] == len(result["audit_failures"])
    assert any(":verifier_not_ok" in failure for failure in result["audit_failures"])
    assert any(":missing_reward" in failure for failure in result["audit_failures"])
    failed = [row for row in result["rows"] if row["audit_status"] == "fail"]
    assert {failure for row in failed for failure in row["audit_failures"]} >= {
        "verifier_not_ok",
        "missing_reward",
    }


def test_audit_fails_missing_podman_metadata(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for task_id in TASK_IDS:
        metadata = {
            "container_engine": "docker",
            "reward": "0",
            "verifier_status": "ok",
            "verifier_reward_observed": True,
        }
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id, metadata=metadata),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    assert result["taxonomy_summary"] == {"metadata_defect": 3}
    assert all("missing_metadata_container_engine" in row["audit_failures"] for row in result["rows"])


def test_audit_fails_empty_artifact_directory(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for task_id in TASK_IDS:
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id),
        )
    empty_artifact = results_dir / run_id / run_id / "artifacts" / TASK_IDS[0] / "artifact.txt"
    empty_artifact.unlink()

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    failed = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert "missing_artifact" in failed["audit_failures"]
    assert failed["artifact_file_count"] == 0


def test_audit_fails_valid_scored_provider_error_mask(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        record = _record(run_id=run_id, task_id=task_id)
        if index == 0:
            record["raw_output"] = (
                "Error: All providers/models failed. Attempts:\n"
                "provider=custom:http://172.17.0.1:8011/v1 model=Qwen/Qwen3.5-4B "
                "error=Custom API error (400 Bad Request): System message must be at the beginning."
            )
            record["metadata"]["stderr"] = record["raw_output"]
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=record,
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    failed = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert failed["failure_taxonomy"] == "provider_error"
    assert "provider_error_masked_as_valid_scored" in failed["audit_failures"]


def test_audit_ignores_benchmark_output_provider_error_text(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        record = _record(run_id=run_id, task_id=task_id)
        if index == 0:
            record["metadata"]["test_output"] = (
                "The benchmark application reported a provider error while validating "
                "the candidate service."
            )
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=record,
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is True
    row = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert row["failure_taxonomy"] == "clean"


def test_audit_allows_zero_provider_summary_counts_when_recorded(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        record = _record(run_id=run_id, task_id=task_id)
        if index == 0:
            record["metadata"].update({
                "provider_proxy_enabled": True,
                "provider_proxy_capture_request_summary": True,
                "provider_proxy_request_count": 0,
                "provider_proxy_response_count": 0,
                "provider_proxy_error_response_count": 0,
                "provider_proxy_empty_content_and_reasoning_response_count": 0,
                "provider_proxy_empty_content_reasoning_and_tool_response_count": 0,
            })
            record["artifact_manifest"] = {
                "local_artifact_root": task_id,
                "files": {
                    "provider_request_summary": f"{task_id}/provider_request_summary.jsonl",
                    "provider_response_summary": f"{task_id}/provider_response_summary.jsonl",
                },
            }
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=record,
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is True
    row = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert row["audit_failures"] == []


def test_audit_fails_valid_scored_empty_assistant_output(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        record = _record(run_id=run_id, task_id=task_id)
        if index == 0:
            record["raw_output"] = ""
            record["trajectory"] = [{"role": "assistant", "content": ""}]
            record["response_json"] = {"runtime_trace_records": 0}
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=record,
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    failed = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert failed["failure_taxonomy"] == "agent_empty_output"
    assert failed["assistant_evidence_present"] is False
    assert "missing_assistant_output" in failed["audit_failures"]
    assert "agent_empty_output_masked_as_valid_scored" in failed["audit_failures"]


def test_audit_allows_observed_valid_scored_empty_assistant_output(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        record = _record(run_id=run_id, task_id=task_id)
        if index == 0:
            record["raw_output"] = ""
            record["trajectory"] = [{"role": "assistant", "content": ""}]
            record["response_json"] = {"runtime_trace_records": 0}
            record["metadata"].update({
                "trajectory_status": "abnormal",
                "trajectory_failure_reason": "zeroclaw_empty_assistant_output",
                "failure_reason": "zeroclaw_empty_assistant_output",
                "zeroclaw_empty_assistant_output": True,
            })
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=record,
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is True
    observed = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert observed["failure_taxonomy"] == "agent_empty_output"
    assert observed["assistant_evidence_present"] is False
    assert observed["observed_abnormal_trajectory"] is True
    assert observed["audit_failures"] == []


def test_audit_fails_provider_blank_response_observed(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    for index, task_id in enumerate(TASK_IDS):
        record = _record(run_id=run_id, task_id=task_id)
        if index == 0:
            record["metadata"].update({
                "provider_proxy_enabled": True,
                "provider_proxy_capture_request_summary": True,
                "provider_proxy_request_count": 1,
                "provider_proxy_response_count": 1,
                "provider_proxy_empty_content_and_reasoning_response_count": 1,
                "provider_proxy_empty_content_reasoning_and_tool_response_count": 1,
            })
            record["artifact_manifest"] = {
                "local_artifact_root": task_id,
                "files": {
                    "provider_request_summary": f"{task_id}/provider_request_summary.jsonl",
                    "provider_response_summary": f"{task_id}/provider_response_summary.jsonl",
                },
            }
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=record,
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    failed = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert failed["provider_empty_content_and_reasoning_response_count"] == 1
    assert failed["provider_empty_content_reasoning_and_tool_response_count"] == 1
    assert "provider_empty_content_reasoning_and_tool_response_observed" in failed[
        "audit_failures"
    ]


def test_audit_fails_dangling_verifier_paths(tmp_path: Path) -> None:
    config_dir, results_dir, logs_dir, preflight, run_id = _prepare(tmp_path)
    missing_reward = tmp_path / "missing" / "reward.txt"
    missing_verifier = tmp_path / "missing" / "verifier"
    for index, task_id in enumerate(TASK_IDS):
        metadata = {
            "container_engine": "podman",
            "reward": "0",
            "verifier_status": "ok",
            "verifier_reward_observed": True,
            "runner": "opencode",
        }
        if index == 0:
            metadata["reward_path"] = str(missing_reward)
            metadata["verifier_output_path"] = str(missing_verifier)
        _write_task(
            results_dir,
            logs_dir,
            run_id=run_id,
            task_id=task_id,
            record=_record(run_id=run_id, task_id=task_id, metadata=metadata),
        )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is False
    failed = [row for row in result["rows"] if row["task_id"] == TASK_IDS[0]][0]
    assert failed["reward_path_exists"] is False
    assert failed["verifier_output_path_exists"] is False
    assert "missing_reward_path_artifact" in failed["audit_failures"]
    assert "missing_verifier_output_path_artifact" in failed["audit_failures"]


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


def test_audit_discovers_three_agent_matrix(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    preflight = tmp_path / "context" / "preflight-pilot.json"
    agents = [
        "terminal_bench2_openclaw",
        "terminal_bench2_opencode",
        "terminal_bench2_zeroclaw",
    ]
    _write_config(config_dir, agents=agents)
    _write_preflight(preflight)
    for agent in agents:
        run_id = f"pilot_{agent}"
        for task_id in TASK_IDS:
            _write_task(
                results_dir,
                logs_dir,
                run_id=run_id,
                task_id=task_id,
                record=_record(
                    run_id=run_id,
                    task_id=task_id,
                    agent_name=agent,
                ),
            )

    result = _run_audit(tmp_path, config_dir, results_dir, logs_dir, preflight)

    assert result["audit_passed"] is True
    assert result["expected_cells"] == 3
    assert result["expected_tasks"] == 9
    assert {row["agent"] for row in result["rows"]} == set(agents)
