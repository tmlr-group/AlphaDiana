from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import audit_podman_scale_readiness as audit_mod


def _write_config(config_dir: Path, name: str, *, agent: str, benchmark: str, benchmark_name: str) -> None:
    data = {
        "run_id": f"template_{name}",
        "agent": {"name": agent, "version": "1.0.0", "config": {}},
        "benchmark": {"name": benchmark_name, "config": {"max_tasks": 1}},
        "sandbox": None,
        "scorer": {"name": "exact_match", "config": {}},
        "max_concurrent": 1,
        "num_samples": 1,
        "metadata": {
            "validation_campaign": "podman-scale-readiness-pilot",
            "container_engine": "podman",
        },
    }
    path = config_dir / f"{agent}_{benchmark}_pilot.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_row(
    results_dir: Path,
    logs_dir: Path,
    *,
    run_id: str,
    task_id: str,
    agent: str = "openclaw",
    benchmark: str = "aime",
    metadata: dict | None = None,
    score_status: str = "valid_scored",
    score: float | None = 1.0,
    finish_reason: str = "",
    error: dict | None = None,
) -> None:
    task_dir = results_dir / run_id / run_id / "tasks"
    artifact_dir = results_dir / run_id / run_id / "artifacts" / task_id / "agent"
    task_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "normalized_trace.json").write_text("{}", encoding="utf-8")
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{run_id}.log").write_text("log\n", encoding="utf-8")
    record = {
        "run_id": run_id,
        "task_id": task_id,
        "agent_name": agent,
        "benchmark_name": benchmark,
        "score_status": score_status,
        "score": score,
        "wall_time_sec": 1.25,
        "metadata": metadata if metadata is not None else {"container_engine": "podman"},
        "sandbox_metadata": {"container_engine": "podman"},
        "finish_reason": finish_reason,
        "error": error,
        "artifact_manifest": {"local_artifact_root": task_id},
    }
    (task_dir / f"{task_id}.json").write_text(json.dumps([record]), encoding="utf-8")


def test_audit_classifies_success_and_failure_rows(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    results_dir = tmp_path / "results"
    logs_dir = tmp_path / "logs"
    config_dir.mkdir()

    _write_config(config_dir, "success", agent="openclaw", benchmark="aime", benchmark_name="aime")
    _write_config(config_dir, "timeout", agent="openclaw", benchmark="gpqa", benchmark_name="gpqa_diamond")
    _write_config(config_dir, "empty", agent="opencode", benchmark="imo", benchmark_name="imo_answerbench")
    _write_config(config_dir, "runtime", agent="zeroclaw", benchmark="hle", benchmark_name="hle")
    _write_config(config_dir, "metadata", agent="opencode", benchmark="aime", benchmark_name="aime")
    _write_config(config_dir, "missing", agent="zeroclaw", benchmark="imo", benchmark_name="imo_answerbench")

    prefix = "pilot"
    _write_row(results_dir, logs_dir, run_id=f"{prefix}_openclaw_aime", task_id="ok")
    _write_row(
        results_dir,
        logs_dir,
        run_id=f"{prefix}_openclaw_gpqa",
        task_id="timeout",
        finish_reason="timeout",
        score=0.0,
        metadata={"container_engine": "podman", "failure_reason": "timeout"},
    )
    _write_row(
        results_dir,
        logs_dir,
        run_id=f"{prefix}_opencode_imo",
        task_id="empty",
        agent="opencode",
        benchmark="imo_answerbench",
        score_status="agent_empty_output",
        score=0.0,
        finish_reason="agent_empty_output",
        metadata={
            "container_engine": "podman",
            "failure_reason": "opencode_empty_assistant_output",
            "request_timeout": 600,
        },
        error={"error_type": "empty_response", "error": "OpenClaw returned no assistant content"},
    )
    _write_row(
        results_dir,
        logs_dir,
        run_id=f"{prefix}_zeroclaw_hle",
        task_id="runtime",
        agent="zeroclaw",
        benchmark="hle",
        score_status="runtime_error",
        score=None,
        error={"error_type": "PodmanError", "error": "podman failed"},
        metadata={"container_engine": "podman"},
    )
    _write_row(
        results_dir,
        logs_dir,
        run_id=f"{prefix}_opencode_aime",
        task_id="badmeta",
        agent="opencode",
        benchmark="aime",
        metadata={},
    )
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"{prefix}_zeroclaw_imo.log").write_text("no result\n", encoding="utf-8")

    result = audit_mod.audit(
        run_prefix=prefix,
        config_dir=config_dir,
        results_dir=results_dir,
        logs_dir=logs_dir,
        root=tmp_path,
    )

    rows = {row["task_id"]: row for row in result["rows"]}
    assert rows["ok"]["audit_status"] == "pass"
    assert rows["ok"]["failure_taxonomy"] == "clean"
    assert rows["ok"]["podman_related"] == "no"
    assert rows["timeout"]["failure_taxonomy"] == "provider_timeout"
    assert rows["empty"]["failure_taxonomy"] == "agent_empty_output"
    assert rows["runtime"]["failure_taxonomy"] == "podman_runtime"
    assert rows["badmeta"]["failure_taxonomy"] == "metadata_defect"
    assert rows["badmeta"]["audit_status"] == "fail"

    missing_rows = [row for row in result["rows"] if row["result_status"] == "no_task_json"]
    assert len(missing_rows) == 1
    assert missing_rows[0]["log_path"] == f"logs/{prefix}_zeroclaw_imo.log"
    assert result["audit_passed"] is False
    assert result["taxonomy_summary"]["clean"] == 1
    assert result["taxonomy_summary"]["metadata_defect"] == 1
    assert result["taxonomy_summary"]["no_task_json"] == 1


def test_classify_failure_prefers_podman_reachability_over_timeout_metadata() -> None:
    record = {
        "score_status": "runtime_error",
        "metadata": {"container_engine": "podman", "request_timeout": 600},
        "error": {
            "error_type": "RuntimeError",
            "error": (
                "ZeroClaw Podman bridge request failed: provider=custom:"
                "http://localhost:8011/v1 error=<urlopen error [Errno 111] "
                "Connection refused>"
            ),
        },
    }

    assert audit_mod.classify_failure(record) == "podman_runtime"


def test_classify_failure_marks_tool_choice_rejections_as_provider_errors() -> None:
    record = {
        "score_status": "runtime_error",
        "metadata": {
            "container_engine": "podman",
            "transport": "zeroclaw_podman_bridge",
            "failure_reason": "RuntimeError",
        },
        "error": {
            "error_type": "RuntimeError",
            "error": (
                "ZeroClaw Podman bridge request failed: 500 {'error': {'message': "
                "'Error: All providers/models failed. Attempts: provider=custom:"
                "http://localhost:8011/v1 model=Qwen/Qwen3.5-4B: Custom API error "
                "(400 Bad Request): tool choice requires --enable-auto-tool-choice'}}"
            ),
        },
    }

    taxonomy = audit_mod.classify_failure(record)
    assert taxonomy == "provider_error"
    assert audit_mod.podman_related(taxonomy) == "no"


def test_classify_failure_marks_bridge_no_assistant_output_as_agent_empty_output() -> None:
    record = {
        "score_status": "runtime_error",
        "metadata": {
            "container_engine": "podman",
            "transport": "zeroclaw_podman_bridge",
            "failure_reason": "RuntimeError",
        },
        "error": {
            "error_type": "RuntimeError",
            "error": (
                "ZeroClaw Podman bridge request failed: 500 {'error': {'message': "
                "'ZeroClaw produced no assistant output; stdout contained only "
                "runtime/provider logs.', 'type': 'RuntimeError'}}"
            ),
        },
    }

    taxonomy = audit_mod.classify_failure(record)
    assert taxonomy == "agent_empty_output"
    assert audit_mod.podman_related(taxonomy) == "no"


def test_classify_log_failure_for_missing_task_json(tmp_path: Path) -> None:
    hle_log = tmp_path / "hle.log"
    hle_log.write_text("DatasetNotFoundError: gated dataset on the Hub\n", encoding="utf-8")
    podman_log = tmp_path / "zeroclaw.log"
    podman_log.write_text(
        "ZeroClaw Podman bridge request failed for http://localhost:8011/v1: "
        "Connection refused\n",
        encoding="utf-8",
    )

    assert audit_mod.classify_log_failure(hle_log) == (
        "benchmark_evaluator",
        "benchmark_evaluator",
    )
    assert audit_mod.classify_log_failure(podman_log) == (
        "podman_runtime",
        "podman_runtime",
    )


def test_render_markdown_includes_failure_taxonomy(tmp_path: Path) -> None:
    result = {
        "run_prefix": "pilot",
        "audit_passed": False,
        "expected_cells": 1,
        "expected_tasks": 1,
        "audit_failure_count": 1,
        "taxonomy_summary": {"metadata_defect": 1},
        "rows": [
            {
                "config": "config.yaml",
                "task_id": "t1",
                "agent": "openclaw",
                "benchmark": "aime",
                "result_status": "runtime_error",
                "score": None,
                "runtime": None,
                "error_type": "RuntimeError",
                "failure_taxonomy": "metadata_defect",
                "podman_related": "yes",
                "audit_status": "fail",
                "artifact_path": "",
                "log_path": "logs/pilot.log",
            }
        ],
    }
    markdown = audit_mod.render_markdown(result)
    assert "## Failure Taxonomy" in markdown
    assert "`metadata_defect`" in markdown
