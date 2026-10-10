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


DOCKER_PAUSE_NOTICE = 'Flag --pause has been deprecated, and enabled by default. Use --no-pause to disable pausing during commit.'
SNAPSHOT_IMAGE = 'sha256:' + 'a' * 64


def test_capture_snapshot_excludes_docker29_stdout_notice(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from alphadiana.benchmarks.terminal_bench2 import snapshot
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test.sh').write_text('echo official')
    runtime = SimpleNamespace(container_engine='docker', container_id='owned',
                              task=SimpleNamespace(task_id='task', metadata={'task_dir': str(tmp_path)}))
    monkeypatch.setattr(snapshot.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=DOCKER_PAUSE_NOTICE+'\n'+SNAPSHOT_IMAGE+'\n'))
    assert capture_snapshot(None, runtime)['image_id'] == SNAPSHOT_IMAGE


def test_replay_reads_saved_docker_notice_without_editing_snapshot(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from alphadiana.benchmarks.terminal_bench2 import replay
    tests=tmp_path/'tests'; tests.mkdir(); (tests/'test.sh').write_text('echo official')
    saved={'schema_version':1,'task_id':'task','engine':'docker',
           'captured_before_verification':True,'verifier_sha256':verifier_digest(str(tmp_path)),
           'image_id':DOCKER_PAUSE_NOTICE+'\n'+SNAPSHOT_IMAGE}
    original=dict(saved)
    task=BenchmarkTask('task','instruction','1',{'task_dir':str(tmp_path),'verifier_snapshot':saved})
    def inspect(command, **kwargs):
        assert command==['docker','image','inspect',SNAPSHOT_IMAGE]
        return SimpleNamespace(stdout=json.dumps([{'Id':SNAPSHOT_IMAGE}]))
    monkeypatch.setattr(replay.subprocess,'run',inspect)
    owner=VerifierReplay()
    def prepare(task, **kw):
        assert task.metadata['docker_image']==SNAPSHOT_IMAGE
        return SimpleNamespace(logs_dir=tmp_path/'logs')
    monkeypatch.setattr(owner,'_prepare_runtime',prepare)
    monkeypatch.setattr(owner,'_run_verifier_and_read_reward',lambda *a, **kw: SimpleNamespace(status='ok',reward='1'))
    monkeypatch.setattr(owner,'_cleanup_runtime',lambda *a: None)
    result, snapshot=owner.run(task,output_dir=tmp_path/'replay')
    assert result.reward=='1' and snapshot==original and saved==original


@pytest.mark.parametrize('output', ['', 'other notice\n'+SNAPSHOT_IMAGE, SNAPSHOT_IMAGE+'\n'+SNAPSHOT_IMAGE])
def test_snapshot_capture_rejects_ambiguous_image_output(tmp_path, monkeypatch, output):
    from types import SimpleNamespace
    from alphadiana.benchmarks.terminal_bench2 import snapshot
    tests=tmp_path/'tests';tests.mkdir();(tests/'test.sh').write_text('echo official')
    runtime=SimpleNamespace(container_engine='docker',container_id='owned',
                            task=SimpleNamespace(task_id='task',metadata={'task_dir':str(tmp_path)}))
    monkeypatch.setattr(snapshot.subprocess,'run',lambda *a, **kw: SimpleNamespace(stdout=output))
    with pytest.raises((ValueError, RuntimeError),match='image identity'):
        capture_snapshot(None,runtime)
