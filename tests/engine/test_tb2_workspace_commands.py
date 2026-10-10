"""Generated workspace commands must work without an inherited source path.

The Podman executable is a constructed argv fixture; no container is started.
"""
import os
import subprocess

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.sandbox.terminal_bench2 import TerminalBench2ContainerMixin


@pytest.mark.parametrize("helper,args,expected", [
    ("exec", ["echo fixture"], ["exec", "owned", "bash", "-lc", "echo fixture"]),
    ("copy_from", ["/app/file", "local"], ["cp", "owned:/app/file", "local"]),
    ("copy_to", ["local", "/app/file"], ["cp", "local", "owned:/app/file"]),
    ("test", [], ["exec", "owned", "bash", "/tests/test.sh"]),
])
def test_podman_workspace_command_outside_checkout(tmp_path, helper, args, expected):
    workdir = tmp_path / "workspace with spaces"
    workdir.mkdir()
    fake = tmp_path / "bin"
    fake.mkdir()
    podman = fake / "podman"
    podman.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    podman.chmod(0o755)
    owner = TerminalBench2ContainerMixin()
    owner._container_engine = "podman"
    paths = owner._write_helper_scripts(workdir, "owned", BenchmarkTask("fixture", "Solve", "1"))
    env = dict(os.environ, PATH=str(fake) + os.pathsep + os.environ.get("PATH", ""))
    env.pop("PYTHONPATH", None)
    result = subprocess.run([str(paths[helper]), *args], cwd=workdir, env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected
