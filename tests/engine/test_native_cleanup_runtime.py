"""Opt-in real rootless Podman cleanup, independent of the Pi/DeepSeek image.

Set ALPHADIANA_LIFECYCLE_IMAGE to an installed immutable image with /bin/sleep.
This checks orphan-container cleanup, not model execution or Pi tool calling.
"""

import os
import re
import uuid

import pytest

from alphadiana.engine.container_runtime.podman_cli import PodmanCLI
from alphadiana.harness._internal.podman_agent import cleanup_execution

IMAGE = os.environ.get("ALPHADIANA_LIFECYCLE_IMAGE")


@pytest.mark.skipif(
    not IMAGE, reason="Set ALPHADIANA_LIFECYCLE_IMAGE to an installed image ID"
)
def test_real_orphan_cleanup_preserves_other_executions():
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE)
    runtime = PodmanCLI()
    assert runtime.rootless()
    assert runtime.image_exists(IMAGE)
    execution, sibling = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        for label in (execution, sibling):
            container = runtime.create(
                IMAGE,
                command=["sleep", "120"],
                network="none",
                extra_args=[
                    "--pull=never",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--label",
                    f"alphadiana.execution={label}",
                ],
            )
            runtime.start(container)
        assert len(runtime.list_ids(labels={"alphadiana.execution": execution})) == 1
        cleanup_execution(execution, runtime)
        assert runtime.list_ids(labels={"alphadiana.execution": execution}) == []
        assert len(runtime.list_ids(labels={"alphadiana.execution": sibling})) == 1
        cleanup_execution(execution, runtime)  # retry after cleanup is idempotent
    finally:
        cleanup_execution(execution, runtime)
        cleanup_execution(sibling, runtime)


@pytest.mark.parametrize('remaining', ['', 'owned-container\n'])
@pytest.mark.parametrize('stop_timeout', [False, True])
def test_docker_cleanup_confirms_removal_before_deleting_workdir(tmp_path, monkeypatch, remaining, stop_timeout):
    import gc
    import tempfile
    import subprocess
    from pathlib import Path
    from types import SimpleNamespace
    from alphadiana.engine.sandbox import terminal_bench2 as sandbox

    calls = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1] == 'stop':
            if stop_timeout:raise subprocess.TimeoutExpired(command, 30)
            return SimpleNamespace(returncode=1, stdout='', stderr='constructed stop failure')
        if command[1] == 'rm':
            return SimpleNamespace(returncode=bool(remaining), stdout='', stderr='constructed remove failure' if remaining else '')
        return SimpleNamespace(returncode=0, stdout=remaining, stderr='')
    monkeypatch.setattr(sandbox.subprocess, 'run', run)
    temporary = tempfile.TemporaryDirectory(dir=tmp_path)
    workdir = Path(temporary.name); (workdir / 'marker').write_text('retained evidence')
    runtime = sandbox.TerminalBench2RuntimeContext(
        task=SimpleNamespace(task_id='task'), docker_image='fixture', container_id='owned-container',
        logs_dir=tmp_path/'logs', workdir=workdir, helper_paths={'marker':workdir/'marker'}, _tempdir=temporary)
    del temporary  # The runtime is the sole directory owner, as in production.
    owner = sandbox.TerminalBench2ContainerMixin()
    if remaining:
        with pytest.raises(RuntimeError, match='constructed remove failure'):
            owner._cleanup_runtime(runtime)
        retained = runtime.workdir
        assert retained.is_relative_to(runtime.logs_dir)
        del runtime
        gc.collect()
        assert (retained / 'marker').read_text() == 'retained evidence'
    else:
        owner._cleanup_runtime(runtime)
        assert not workdir.exists()
    assert ['docker', 'rm', '--force', 'owned-container'] in calls


@pytest.mark.parametrize('removal_finishes', [True, False])
def test_docker_cleanup_waits_for_observed_removal_in_progress(monkeypatch, removal_finishes):
    """Replay Docker 29's captured auto-remove response without retrying deletion."""
    from types import SimpleNamespace
    from alphadiana.engine.sandbox import terminal_bench2 as sandbox
    calls = []; probes = []
    def run(command, **kwargs):
        calls.append(command)
        if command[1] == 'rm':
            return SimpleNamespace(returncode=1, stdout='', stderr='Error response from daemon: removal of container owned is already in progress')
        if command[1] == 'ps':
            probes.append(True)
            return SimpleNamespace(returncode=0, stdout='' if removal_finishes and len(probes)>1 else 'owned\n', stderr='')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(sandbox.subprocess, 'run', run)
    monkeypatch.setattr(sandbox.time, 'sleep', lambda _: None)
    times=iter([0,1,2,3] if removal_finishes else [0,1,2,121]);monkeypatch.setattr(sandbox.time, 'monotonic', lambda: next(times))
    owner=sandbox.TerminalBench2ContainerMixin()
    if removal_finishes:owner._stop_container('owned','task')
    else:
        with pytest.raises(RuntimeError, match='remains'):owner._stop_container('owned','task')
    assert len([c for c in calls if c[1]=='rm'])==1
    assert len(probes)==(2 if removal_finishes else 1)


def test_docker_removal_deadline_prevents_a_late_probe(monkeypatch):
    from types import SimpleNamespace
    from alphadiana.engine.sandbox import terminal_bench2 as sandbox
    clock=[0.0];probes=[]
    def run(command, **kwargs):
        if command[1]=='rm':return SimpleNamespace(returncode=1, stderr='removal is already in progress')
        if command[1]=='ps':
            probes.append(kwargs['timeout'])
            clock[0]=119.9 if len(probes)==1 else 140.1
            return SimpleNamespace(stdout='owned' if len(probes)==1 else '')
        return SimpleNamespace(returncode=0, stderr='')
    monkeypatch.setattr(sandbox.subprocess,'run',run)
    monkeypatch.setattr(sandbox.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(sandbox.time,'sleep',lambda duration:clock.__setitem__(0,clock[0]+duration))
    with pytest.raises(RuntimeError,match='remains'):
        sandbox.TerminalBench2ContainerMixin()._stop_container('owned','task')
    assert len(probes)==1 and clock[0]<=120.0
