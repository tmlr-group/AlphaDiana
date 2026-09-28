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
