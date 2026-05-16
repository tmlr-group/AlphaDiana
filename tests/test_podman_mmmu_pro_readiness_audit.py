from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import audit_podman_mmmu_pro_readiness as audit_mod


TINY_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/"
    "l6U9JwAAAABJRU5ErkJggg=="
)


def _as_task_ids(task_id: str | list[str]) -> list[str]:
    return task_id if isinstance(task_id, list) else [task_id]


def _write_config(config_dir: Path, *, agent: str, task_id: str | list[str]) -> None:
    task_ids = _as_task_ids(task_id)
    data = {
        "run_id": f"template_{agent}_mmmu_pro",
        "agent": {"name": agent, "version": "1.0.0", "config": {}},
        "benchmark": {
            "name": "mmmu_pro",
            "config": {
                "dataset": "MMMU/MMMU_Pro",
                "data_config": "vision",
                "split": "test",
                "dataset_indices": list(range(len(task_ids))),
            },
        },
        "sandbox": None,
        "scorer": {"name": "exact_match", "config": {}},
        "max_concurrent": 1,
        "num_samples": 1,
        "metadata": {
            "validation_campaign": "podman-mmmu-pro-readiness-pilot",
            "container_engine": "podman",
            "supports_multimodal": True,
            "selected_dataset_indices": list(range(len(task_ids))),
            "selected_task_ids": task_ids,
        },
    }
    path = config_dir / f"{agent}_mmmu_pro_pilot.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_preflight(path: Path, *, ok: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": ok,
        "base_url": "http://127.0.0.1:8011/v1",
        "models_reachable": ok,
        "chat_reachable": ok,
        "response_non_empty": ok,
        "model": "Qwen/Qwen3.5-VL",
        "api_base_host": "127.0.0.1",
        "api_base_scheme": "http",
        "container_engine": "podman",
        "network_mode": "host",
        "podman_runtime_ok": ok,
        "thinking_mode": True,
        "max_tokens": 8192,
        "remote_image_url": {
            "ok": ok,
            "http_status": 200 if ok else 400,
            "content_chars": 2 if ok else 0,
            "reasoning_chars": 12 if ok else 0,
        },
        "data_url": {
            "ok": ok,
            "http_status": 200 if ok else 400,
            "content_chars": 2 if ok else 0,
            "reasoning_chars": 12 if ok else 0,
        },
        "error_type": "" if ok else "HTTPError",
        "error": "" if ok else "automated preflight did not pass",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _record(
    *,
    run_id: str,
    task_id: str,
    agent: str,
    data_config: str = "vision",
    request_messages: list[dict] | None = None,
    metadata: dict | None = None,
    score_status: str = "valid_scored",
    score: float | None = 1.0,
    error: dict | None = None,
    artifact_manifest: dict | None = None,
) -> dict:
    return {
        "run_id": run_id,
        "task_id": task_id,
        "agent_name": agent,
        "benchmark_name": "mmmu_pro",
        "score_status": score_status,
        "score": score,
        "wall_time_sec": 1.25,
        "metadata": metadata if metadata is not None else {"container_engine": "podman"},
        "sandbox_metadata": {"container_engine": "podman"},
        "task_metadata": {"data_config": data_config, "source": "MMMU/MMMU_Pro"},
        "finish_reason": "",
        "error": error,
        "request_messages": request_messages or [],
        "artifact_manifest": artifact_manifest or {"local_artifact_root": task_id},
    }


def _write_task_record(results_dir: Path, logs_dir: Path, *, run_id: str, task_id: str, record: dict) -> Path:
    task_dir = results_dir / run_id / run_id / "tasks"
    artifact_root = results_dir / run_id / run_id / "artifacts" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    (artifact_root / "agent").mkdir(parents=True, exist_ok=True)
    (artifact_root / "agent" / "normalized_trace.json").write_text("{}", encoding="utf-8")
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{run_id}.log").write_text("log\n", encoding="utf-8")
    path = task_dir / f"{task_id}.json"
    path.write_text(json.dumps([record]), encoding="utf-8")
    return artifact_root


def _proof_record(agent: str, run_id: str, task_id: str) -> dict:
    if agent == "openclaw":
        return _record(
            run_id=run_id,
            task_id=task_id,
            agent=agent,
            request_messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Use this image."},
                    {"type": "image_url", "image_url": {"url": TINY_DATA_URL}},
                ],
            }],
        )
    if agent == "opencode":
        return _record(
            run_id=run_id,
            task_id=task_id,
            agent=agent,
            artifact_manifest={
                "local_artifact_root": task_id,
                "files": {"attachment_manifest": f"{task_id}/workspace/attachment_manifest.json"},
            },
        )
    return _record(run_id=run_id, task_id=task_id, agent=agent)


def _write_image_proof_artifacts(agent: str, artifact_root: Path) -> None:
    (artifact_root / "workspace").mkdir(exist_ok=True)
    if agent == "zeroclaw":
        (artifact_root / "workspace" / "task.txt").write_text(
            "Problem\n\n[IMAGE:/sandbox/work/attachments/image_1.png]\n",
            encoding="utf-8",
        )
        (artifact_root / "workspace" / "attachment_manifest.json").write_text(
            json.dumps({"attachments": [{"path": "attachments/image_1.png", "mime": "image/png"}]}),
            encoding="utf-8",
        )
        (artifact_root / "workspace" / "status.json").write_text(
            json.dumps({
                "vision_proxy_image_count": 1,
                "vision_proxy_request_count": 1,
                "vision_proxy_injection_count": 1,
            }),
            encoding="utf-8",
        )
    if agent == "opencode":
        (artifact_root / "workspace" / "attachments").mkdir(parents=True, exist_ok=True)
        (artifact_root / "workspace" / "attachment_manifest.json").write_text(
            json.dumps({
                "attachments": [{
                    "filename": "image_1.png",
                    "path": "attachments/image_1.png",
                    "base64_artifact": "attachments/image_1.png.base64",
                }]
            }),
            encoding="utf-8",
        )
        (artifact_root / "workspace" / "attachments" / "image_1.png.base64").write_text(
            "aW1hZ2U=",
            encoding="utf-8",
        )


def _prepare_matrix(tmp_path: Path, task_ids: dict[str, str | list[str]]) -> tuple[Path, Path, Path, Path]:
    config_dir = tmp_path / "configs"
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    preflight = tmp_path / "context" / "preflight-pilot.json"
    config_dir.mkdir()
    for agent, task_id in task_ids.items():
        _write_config(config_dir, agent=agent, task_id=task_id)
    _write_preflight(preflight)
    return config_dir, results_dir, logs_dir, preflight


def _run_audit(
    tmp_path: Path,
    *,
    config_dir: Path,
    results_dir: Path,
    logs_dir: Path,
    preflight: Path,
) -> dict:
    return audit_mod.audit(
        run_prefix="pilot",
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        root=tmp_path,
        preflight_status_file=preflight,
    )


def test_audit_accepts_all_three_agent_image_proofs(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": ["mmmu_pro_openclaw_vision_1", "mmmu_pro_openclaw_vision_2", "mmmu_pro_openclaw_vision_3"],
        "zeroclaw": ["mmmu_pro_zeroclaw_vision_1", "mmmu_pro_zeroclaw_vision_2", "mmmu_pro_zeroclaw_vision_3"],
        "opencode": ["mmmu_pro_opencode_vision_1", "mmmu_pro_opencode_vision_2", "mmmu_pro_opencode_vision_3"],
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)

    for agent, ids in task_ids.items():
        run_id = f"pilot_{agent}_mmmu_pro"
        for task_id in ids:
            artifact_root = _write_task_record(
                results_dir,
                logs_dir,
                run_id=run_id,
                task_id=task_id,
                record=_proof_record(agent, run_id, task_id),
            )
            _write_image_proof_artifacts(agent, artifact_root)

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    rows = {row["agent"]: row for row in result["rows"]}
    assert result["audit_passed"] is True
    assert result["expected_tasks"] == 9
    assert len(result["rows"]) == 9
    assert rows["openclaw"]["image_proof_kind"] == "openclaw_image_url"
    assert rows["opencode"]["image_proof_kind"] == "opencode_attachment_manifest"
    assert rows["zeroclaw"]["image_proof_kind"] == "zeroclaw_vision_proxy_injection"
    assert result["taxonomy_summary"] == {"clean": 9}


def test_audit_fails_missing_image_proof_text_only_and_missing_task(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": "mmmu_pro_missing_image",
        "zeroclaw": "mmmu_pro_missing_task",
        "opencode": "mmmu_pro_text_only",
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)

    openclaw_run = "pilot_openclaw_mmmu_pro"
    _write_task_record(
        results_dir,
        logs_dir,
        run_id=openclaw_run,
        task_id=task_ids["openclaw"],
        record=_record(run_id=openclaw_run, task_id=task_ids["openclaw"], agent="openclaw"),
    )

    opencode_run = "pilot_opencode_mmmu_pro"
    root = _write_task_record(
        results_dir,
        logs_dir,
        run_id=opencode_run,
        task_id=task_ids["opencode"],
        record=_record(
            run_id=opencode_run,
            task_id=task_ids["opencode"],
            agent="opencode",
            data_config="standard (4 options)",
        ),
    )
    (root / "workspace").mkdir()
    (root / "workspace" / "attachment_manifest.json").write_text(
        json.dumps({"attachments": [{"path": "attachments/image_1.png"}]}),
        encoding="utf-8",
    )

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    rows = {row["task_id"]: row for row in result["rows"]}
    assert result["audit_passed"] is False
    assert rows[task_ids["openclaw"]]["failure_taxonomy"] == "image_payload_missing"
    assert rows[task_ids["openclaw"]]["audit_failures"] == ["missing_image_payload_proof"]
    assert rows[task_ids["opencode"]]["failure_taxonomy"] == "text_only_fallback"
    assert "text_only_mmmu_pro_row" in rows[task_ids["opencode"]]["audit_failures"]
    assert rows[task_ids["zeroclaw"]]["failure_taxonomy"] == "no_task_json"


def test_audit_classifies_provider_image_rejection_and_corrupt_payload(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": "mmmu_pro_image_rejected",
        "zeroclaw": "mmmu_pro_ok_marker",
        "opencode": "mmmu_pro_corrupt_payload",
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)

    openclaw_run = "pilot_openclaw_mmmu_pro"
    _write_task_record(
        results_dir,
        logs_dir,
        run_id=openclaw_run,
        task_id=task_ids["openclaw"],
        record=_record(
            run_id=openclaw_run,
            task_id=task_ids["openclaw"],
            agent="openclaw",
            request_messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "Use this image."},
                    {"type": "image_url", "image_url": {"url": TINY_DATA_URL}},
                ],
            }],
            score_status="provider_error",
            score=0.0,
            error={"error_type": "BadRequestError", "error": "400 model does not support image input"},
        ),
    )

    opencode_run = "pilot_opencode_mmmu_pro"
    opencode_root = _write_task_record(
        results_dir,
        logs_dir,
        run_id=opencode_run,
        task_id=task_ids["opencode"],
        record=_record(run_id=opencode_run, task_id=task_ids["opencode"], agent="opencode"),
    )
    (opencode_root / "workspace" / "attachments").mkdir(parents=True)
    (opencode_root / "workspace" / "attachments" / "image_1.png.base64").write_text(
        "not-base64!",
        encoding="utf-8",
    )

    zeroclaw_run = "pilot_zeroclaw_mmmu_pro"
    zeroclaw_root = _write_task_record(
        results_dir,
        logs_dir,
        run_id=zeroclaw_run,
        task_id=task_ids["zeroclaw"],
        record=_record(run_id=zeroclaw_run, task_id=task_ids["zeroclaw"], agent="zeroclaw"),
    )
    (zeroclaw_root / "workspace").mkdir()
    (zeroclaw_root / "workspace" / "task.txt").write_text("[IMAGE:/tmp/image.png]\n", encoding="utf-8")

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    rows = {row["task_id"]: row for row in result["rows"]}
    assert rows[task_ids["openclaw"]]["failure_taxonomy"] == "provider_image_rejected"
    assert "provider_rejected_image_input" in rows[task_ids["openclaw"]]["audit_failures"]
    assert rows[task_ids["opencode"]]["failure_taxonomy"] == "image_payload_corrupt"
    assert "corrupted_multimodal_payload" in rows[task_ids["opencode"]]["audit_failures"]


def test_audit_rejects_zeroclaw_image_lost_before_provider_request(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": ["mmmu_pro_openclaw_1", "mmmu_pro_openclaw_2", "mmmu_pro_openclaw_3"],
        "zeroclaw": ["mmmu_pro_zeroclaw_1", "mmmu_pro_zeroclaw_2", "mmmu_pro_zeroclaw_3"],
        "opencode": ["mmmu_pro_opencode_1", "mmmu_pro_opencode_2", "mmmu_pro_opencode_3"],
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)

    for agent, ids in task_ids.items():
        run_id = f"pilot_{agent}_mmmu_pro"
        for task_id in ids:
            artifact_root = _write_task_record(
                results_dir,
                logs_dir,
                run_id=run_id,
                task_id=task_id,
                record=_proof_record(agent, run_id, task_id),
            )
            _write_image_proof_artifacts(agent, artifact_root)
            if agent == "zeroclaw" and task_id.endswith("_1"):
                (artifact_root / "workspace" / "status.json").write_text(
                    json.dumps({
                        "vision_proxy_image_count": 1,
                        "vision_proxy_request_count": 0,
                        "vision_proxy_injection_count": 0,
                    }),
                    encoding="utf-8",
                )

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    bad_row = next(row for row in result["rows"] if row["task_id"] == "mmmu_pro_zeroclaw_1")
    assert result["audit_passed"] is False
    assert bad_row["failure_taxonomy"] == "image_lost_before_provider_request"
    assert "image_lost_before_provider_request" in bad_row["audit_failures"]


def test_audit_does_not_treat_answer_dates_as_image_rejection(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": ["mmmu_pro_openclaw_1", "mmmu_pro_openclaw_2", "mmmu_pro_openclaw_3"],
        "zeroclaw": ["mmmu_pro_zeroclaw_1", "mmmu_pro_zeroclaw_2", "mmmu_pro_zeroclaw_3"],
        "opencode": ["mmmu_pro_opencode_1", "mmmu_pro_opencode_2", "mmmu_pro_opencode_3"],
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)

    for agent, ids in task_ids.items():
        run_id = f"pilot_{agent}_mmmu_pro"
        for task_id in ids:
            record = _proof_record(agent, run_id, task_id)
            if agent == "opencode" and task_id.endswith("_1"):
                record["rationale"] = "The answer mentions 200-400 C.E. and is still a clean wrong answer."
                record["metadata"] = {
                    "container_engine": "podman",
                    "image": "localhost/alphadiana-opencode-podman:latest",
                    "answer_source_text": "A chart spans 200-400 C.E.",
                }
                record["score"] = 0.0
            artifact_root = _write_task_record(
                results_dir,
                logs_dir,
                run_id=run_id,
                task_id=task_id,
                record=record,
            )
            _write_image_proof_artifacts(agent, artifact_root)

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    row = next(row for row in result["rows"] if row["task_id"] == "mmmu_pro_opencode_1")
    assert result["audit_passed"] is True
    assert row["failure_taxonomy"] == "clean"
    assert row["audit_status"] == "pass"


def test_empty_answer_with_image_proof_can_pass_audit_gate(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": ["mmmu_pro_empty_answer_1", "mmmu_pro_empty_answer_2", "mmmu_pro_empty_answer_3"],
        "zeroclaw": ["mmmu_pro_marker_1", "mmmu_pro_marker_2", "mmmu_pro_marker_3"],
        "opencode": ["mmmu_pro_attachment_1", "mmmu_pro_attachment_2", "mmmu_pro_attachment_3"],
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)

    for agent, ids in task_ids.items():
        run_id = f"pilot_{agent}_mmmu_pro"
        for task_id in ids:
            record = _proof_record(agent, run_id, task_id)
            record["score_status"] = "agent_empty_output" if agent == "openclaw" else "valid_scored"
            record["score"] = 0.0
            record["reasoning"] = "thinking trace is present"
            if agent == "openclaw":
                record["error"] = {"error_type": "empty_response", "error": "no assistant output"}
            root = _write_task_record(
                results_dir,
                logs_dir,
                run_id=run_id,
                task_id=task_id,
                record=record,
            )
            _write_image_proof_artifacts(agent, root)

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    rows = {row["task_id"]: row for row in result["rows"]}
    assert result["audit_passed"] is True
    first_openclaw = task_ids["openclaw"][0]
    assert rows[first_openclaw]["failure_taxonomy"] == "agent_empty_output"
    assert rows[first_openclaw]["audit_status"] == "pass"


def test_preflight_failure_blocks_audit(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": "mmmu_pro_openclaw",
        "zeroclaw": "mmmu_pro_zeroclaw",
        "opencode": "mmmu_pro_opencode",
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)
    _write_preflight(preflight, ok=False)

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    assert result["audit_passed"] is False
    assert result["preflight"]["ok"] is False
    assert result["taxonomy_summary"]["vlm_preflight_failed"] == 1


def test_audit_requires_podman_container_engine_metadata(tmp_path: Path) -> None:
    task_ids = {
        "openclaw": ["mmmu_pro_openclaw_1", "mmmu_pro_openclaw_2", "mmmu_pro_openclaw_3"],
        "zeroclaw": ["mmmu_pro_zeroclaw_1", "mmmu_pro_zeroclaw_2", "mmmu_pro_zeroclaw_3"],
        "opencode": ["mmmu_pro_opencode_1", "mmmu_pro_opencode_2", "mmmu_pro_opencode_3"],
    }
    config_dir, results_dir, logs_dir, preflight = _prepare_matrix(tmp_path, task_ids)

    for agent, ids in task_ids.items():
        run_id = f"pilot_{agent}_mmmu_pro"
        for task_id in ids:
            record = _proof_record(agent, run_id, task_id)
            if agent == "openclaw" and task_id.endswith("_1"):
                record["metadata"] = {"container_engine": "docker"}
            artifact_root = _write_task_record(
                results_dir,
                logs_dir,
                run_id=run_id,
                task_id=task_id,
                record=record,
            )
            _write_image_proof_artifacts(agent, artifact_root)

    result = _run_audit(
        tmp_path,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        preflight=preflight,
    )

    bad_row = next(row for row in result["rows"] if row["task_id"] == "mmmu_pro_openclaw_1")
    assert result["audit_passed"] is False
    assert bad_row["failure_taxonomy"] == "metadata_defect"
    assert "missing_metadata_container_engine" in bad_row["audit_failures"]


def test_render_markdown_includes_image_proof_and_taxonomy() -> None:
    markdown = audit_mod.render_markdown({
        "run_prefix": "pilot",
        "audit_passed": False,
        "expected_cells": 1,
        "expected_tasks": 1,
        "audit_failure_count": 1,
        "preflight": {"ok": False, "model": "demo", "api_base_host": "localhost", "error_type": "HTTPError"},
        "taxonomy_summary": {"image_payload_missing": 1},
        "rows": [{
            "config": "config.yaml",
            "task_id": "t1",
            "agent": "openclaw",
            "benchmark": "mmmu_pro",
            "data_config": "vision",
            "result_status": "valid_scored",
            "score": 0,
            "error_type": "-",
            "image_proof_status": "missing",
            "image_proof_kind": "",
            "failure_taxonomy": "image_payload_missing",
            "audit_status": "fail",
            "artifact_path": "",
            "log_path": "logs/pilot.log",
        }],
    })

    assert "Image Proof" in markdown
    assert "## Failure Taxonomy" in markdown
    assert "`image_payload_missing`" in markdown
    assert "## VLM Preflight" in markdown
