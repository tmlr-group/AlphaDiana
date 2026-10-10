"""Container snapshots must not depend on complete exec stdout delivery."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from alphadiana.engine.container_runtime.agent_runtime import (
    PodmanAgentRuntime,
    PodmanAgentSpec,
)
from alphadiana.harness.openclaw.runtime import OpenClawPodmanRuntimeManager


class FileTransport:
    def __init__(self, content, *, mode="complete"):
        self.content = content
        self.mode = mode
        self.copies = []

    def exec(self, container, command, **kwargs):
        assert command == ["stat", "-c", "%s", "--", "/session.jsonl"]
        return SimpleNamespace(stdout=f"{len(self.content)}\n", returncode=0)

    def cp(self, source, destination, **kwargs):
        self.copies.append(Path(destination))
        assert source == "owned-container:/session.jsonl"
        target = Path(destination)
        if self.mode == "symlink":
            target.symlink_to("/etc/passwd")
        elif self.mode == "directory":
            target.mkdir()
        else:
            target.write_bytes(self.content[:10] if self.mode == "short" else self.content)


def runtime_with_file(content, *, mode="complete"):
    transport = FileTransport(content, mode=mode)
    runtime = PodmanAgentRuntime(runtime=transport)
    runtime._container_id = "owned-container"
    runtime._spec = PodmanAgentSpec("fixture", "sha256:fixture", "true", None)
    return runtime, transport


def test_large_snapshot_uses_file_transport_and_cleans_temporary_copy():
    content = ('{"image":"' + "x" * 2_000_000 + '"}\n').encode()
    runtime, transport = runtime_with_file(content)
    assert runtime.read_text("/session.jsonl", max_bytes=len(content)) == content.decode()
    assert len(transport.copies) == 1
    assert not transport.copies[0].parent.exists()


@pytest.mark.parametrize("mode", ["short", "symlink", "directory"])
def test_incomplete_or_nonregular_copy_is_rejected(mode):
    runtime, transport = runtime_with_file(b"x" * 100, mode=mode)
    with pytest.raises(ValueError, match="incomplete|regular file"):
        runtime.read_text("/session.jsonl", max_bytes=100)
    assert not transport.copies[0].parent.exists()


def test_oversized_file_is_rejected_before_copying():
    runtime, transport = runtime_with_file(b"x" * 100)
    with pytest.raises(ValueError, match="byte limit"):
        runtime.read_text("/session.jsonl", max_bytes=99)
    assert not transport.copies


def test_openclaw_snapshot_uses_verified_copy_instead_of_exec_cat(monkeypatch):
    runtime, transport = runtime_with_file(b'{"complete":true}\n')
    manager = OpenClawPodmanRuntimeManager({})
    monkeypatch.setattr(manager, "_podman_exec_stdout", lambda *a, **k: "18\n")
    assert manager._podman_read_text_capped(
        runtime, "/session.jsonl", size_limit=100,
    ) == '{"complete":true}\n'
    assert len(transport.copies) == 1


def test_openclaw_does_not_publish_partial_copy(monkeypatch, caplog):
    runtime, _ = runtime_with_file(b"x" * 100, mode="short")
    manager = OpenClawPodmanRuntimeManager({})
    monkeypatch.setattr(manager, "_podman_exec_stdout", lambda *a, **k: "100\n")
    assert manager._podman_read_text_capped(
        runtime, "/session.jsonl", size_limit=100,
    ) == ""
    assert "Could not capture complete OpenClaw file" in caplog.text
