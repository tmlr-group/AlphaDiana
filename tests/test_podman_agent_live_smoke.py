from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from alphadiana.container_runtime import PodmanCLI
from alphadiana.config.experiment_config import ExperimentConfig


SMOKE_CONFIGS = [
    pytest.param("configs/examples/openclaw_aime_podman_smoke.yaml", id="openclaw"),
    pytest.param("configs/examples/zeroclaw_aime_podman_smoke.yaml", id="zeroclaw"),
    pytest.param("configs/examples/opencode_aime_podman_smoke.yaml", id="opencode"),
]


def _skip_unless_enabled() -> None:
    if os.environ.get("ALPHADIANA_RUN_PODMAN_AGENT_SMOKE") != "1":
        pytest.skip("set ALPHADIANA_RUN_PODMAN_AGENT_SMOKE=1 to run live Podman agent smokes")
    if shutil.which("podman") is None:
        pytest.skip("podman binary is not available")
    missing = [
        name
        for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME")
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip(f"missing provider env for live smoke: {', '.join(missing)}")


def _task_records(run_id: str) -> list[dict]:
    tasks_dir = Path("results") / run_id / "tasks"
    records: list[dict] = []
    for path in sorted(tasks_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            assert data, f"empty task sample list: {path}"
            records.append(data[0])
        elif isinstance(data, dict):
            records.append(data)
    return records


def _assert_container_removed(container_id: str) -> None:
    runtime = PodmanCLI()
    for _ in range(10):
        result = runtime.inspect(container_id, timeout=15, check=False)
        if result.returncode != 0:
            return
        time.sleep(0.5)
    pytest.fail(f"Podman container was still inspectable after run cleanup: {container_id}")


@pytest.mark.parametrize("config_path", SMOKE_CONFIGS)
def test_live_podman_standard_reasoning_smoke(config_path: str) -> None:
    _skip_unless_enabled()
    config = ExperimentConfig.from_yaml(config_path)
    log_path = Path("logs") / f"{config.run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    completed = subprocess.run(
        [sys.executable, "-m", "alphadiana.cli", "run", config_path, "--redo-all"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=1800,
    )
    log_path.write_text(completed.stdout, encoding="utf-8", errors="replace")
    assert completed.returncode == 0, completed.stdout[-4000:]

    records = _task_records(config.run_id)
    assert records, f"no task JSON records found for run_id={config.run_id}"
    record = records[0]
    assert record.get("score_status") == "valid_scored"
    assert record.get("predicted") is not None
    metadata = record.get("metadata") or {}
    sandbox_metadata = record.get("sandbox_metadata") or {}
    merged_metadata = {**sandbox_metadata, **metadata}
    assert merged_metadata.get("container_engine") == "podman"
    container_id = (
        merged_metadata.get("container_id")
        or merged_metadata.get("runtime_container_id")
        or merged_metadata.get("controller_container_id")
    )
    assert container_id
    assert merged_metadata.get("runtime_image") or merged_metadata.get("image") or merged_metadata.get("controller_image")
    adapter_name = (
        merged_metadata.get("adapter_name")
        or merged_metadata.get("runtime_adapter")
        or merged_metadata.get("transport")
    )
    assert adapter_name
    assert "published_api_base" in merged_metadata or record.get("gateway_url") or metadata.get("api_base")
    if adapter_name != "opencode-podman":
        assert merged_metadata.get("published_api_base") or record.get("gateway_url")
    workspace_files = record.get("workspace_file_contents") or {}
    artifact_manifest = record.get("artifact_manifest") or {}
    artifact_files = artifact_manifest.get("files") or {}
    assert workspace_files or record.get("gateway_log_excerpt") or artifact_manifest
    if adapter_name == "opencode-podman":
        assert merged_metadata.get("container_cleanup_status") == "removed"
        assert merged_metadata.get("container_logs_collected") is True
        assert (
            "opencode_podman_container.log" in workspace_files
            or "opencode_podman_container.log" in artifact_files
            or artifact_files.get("podman_container_log")
        )
    _assert_container_removed(str(container_id))
