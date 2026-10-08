"""Verifier replay checks, including opt-in real rootless Podman restoration."""

import os
import subprocess

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.terminal_bench2.harness.common import (
    TerminalBench2ContainerMixin,
)
from alphadiana.benchmarks.terminal_bench2.replay import (
    VerifierReplay,
    capture_snapshot,
    verifier_digest,
)


def test_verifier_digest_detects_changes(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    script = tests / "test.sh"
    script.write_text("echo first")
    original = verifier_digest(str(tmp_path))
    script.write_text("echo changed")
    assert verifier_digest(str(tmp_path)) != original


@pytest.mark.skipif(
    os.environ.get("ALPHADIANA_TEST_REPLAY") != "1",
    reason="requires real rootless Podman",
)
def test_real_restoration_without_solver(tmp_path):
    tests = tmp_path / "task/tests"
    tests.mkdir(parents=True)
    (tests / "test.sh").write_text(
        "#!/bin/bash\nmkdir -p /logs/verifier\nif test -f /solver-marker; then echo 1; else echo 0; fi > /logs/verifier/reward.txt\n"
    )
    task = BenchmarkTask(
        "snapshot-fixture",
        "fixture",
        "1",
        {
            "task_dir": str(tests.parent),
            "docker_image": "docker.io/library/ubuntu:22.04",
        },
    )
    owner = TerminalBench2ContainerMixin()
    owner._setup_container_config(
        {"container_engine": "podman", "logs_base_dir": str(tmp_path / "original")}
    )
    runtime = owner._prepare_runtime(task)
    snapshot = None
    try:
        owner._podman.exec(runtime.container_id, ["touch", "/solver-marker"])
        snapshot = capture_snapshot(owner, runtime)
    finally:
        owner._cleanup_runtime(runtime)
    try:
        task.metadata["verifier_snapshot"] = snapshot
        result, _ = VerifierReplay().run(task, output_dir=tmp_path / "replay")
        assert result.reward == "1"
        # Changing the verifier must fail before starting another container.
        (tests / "test.sh").write_text("echo 1")
        with pytest.raises(ValueError, match="changed"):
            VerifierReplay().run(task, output_dir=tmp_path / "drift")
    finally:
        if snapshot:
            subprocess.run(
                ["podman", "rmi", snapshot["image_id"]], check=True, capture_output=True
            )


@pytest.mark.skipif(
    not os.environ.get("ALPHADIANA_HARBOR_PYTHON")
    or not os.environ.get("ALPHADIANA_HARBOR_TEST_IMAGE"),
    reason="requires an installed Harbor Python and local Docker fixture image",
)
def test_native_harbor_hook_captures_pre_verifier_state(tmp_path):
    import json
    from pathlib import Path

    image = os.environ["ALPHADIANA_HARBOR_TEST_IMAGE"]
    task_dir = tmp_path / "task"
    for directory in ("tests", "solution", "environment"):
        (task_dir / directory).mkdir(parents=True)
    (task_dir / "instruction.md").write_text("Create a solver marker.\n")
    (task_dir / "environment/Dockerfile").write_text(f"FROM {image}\n")
    (task_dir / "task.toml").write_text(
        'version = "1.0"\n[agent]\ntimeout_sec = 30\n[verifier]\ntimeout_sec = 30\n'
        f'[environment]\ndocker_image = "{image}"\ncpus = 1\nmemory = "1G"\n'
    )
    (task_dir / "solution/solve.sh").write_text("#!/bin/bash\ntouch /solver-marker\n")
    (task_dir / "tests/test.sh").write_text(
        "#!/bin/bash\nmkdir -p /logs/verifier\ntouch /verifier-marker\n"
        "if test -f /solver-marker; then echo 1; else echo 0; fi > /logs/verifier/reward.txt\n"
    )
    job = {
        "job_name": "capture",
        "jobs_dir": str(tmp_path / "jobs"),
        "n_attempts": 1,
        "n_concurrent_trials": 1,
        "retry": {"max_retries": 0},
        "tasks": [{"path": str(task_dir)}],
        "agents": [{"name": "oracle"}],
        "environment": {"type": "docker", "delete": False},
    }
    config = tmp_path / "job.json"
    config.write_text(json.dumps(job))
    snapshot_path = tmp_path / "snapshot.json"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    snapshot = None
    try:
        subprocess.run(
            [
                os.environ["ALPHADIANA_HARBOR_PYTHON"],
                "-m",
                "alphadiana.benchmarks.terminal_bench2.harness.harbor_job",
                str(config),
                str(snapshot_path),
                "harbor-fixture",
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        snapshot = json.loads(snapshot_path.read_text())
        subprocess.run(
            ["docker", "image", "inspect", image], check=True, capture_output=True
        )
        remaining = subprocess.check_output(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project="
                + snapshot["native_trial_id"].lower(),
            ],
            text=True,
        )
        assert not remaining.strip()
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                snapshot["image_id"],
                "sh",
                "-c",
                "test -f /solver-marker && test ! -e /verifier-marker",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
        task = BenchmarkTask(
            "harbor-fixture",
            "fixture",
            "1",
            {
                "task_dir": str(task_dir),
                "docker_image": image,
                "verifier_snapshot": snapshot,
            },
        )
        result, _ = VerifierReplay().run(task, output_dir=tmp_path / "replay")
        assert result.reward == "1"
    finally:
        if snapshot:
            subprocess.run(
                ["docker", "rmi", snapshot["image_id"]], check=True, capture_output=True
            )


def test_snapshot_capture_keeps_existing_import_contract():
    from alphadiana.benchmarks.terminal_bench2 import replay, snapshot

    assert replay.capture_snapshot is snapshot.capture_snapshot
    assert replay.verifier_digest is snapshot.verifier_digest
