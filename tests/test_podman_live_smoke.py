from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from alphadiana.container_runtime.podman_cli import PodmanCLI


pytestmark = pytest.mark.skipif(
    os.environ.get("ALPHADIANA_RUN_PODMAN_SMOKE") != "1",
    reason="live Podman smoke skipped unless ALPHADIANA_RUN_PODMAN_SMOKE=1",
)


def test_live_podman_runtime_smoke(tmp_path: Path) -> None:
    if shutil.which("podman") is None:
        pytest.skip("podman binary not found")

    image = os.environ.get("ALPHADIANA_PODMAN_SMOKE_IMAGE", "alpine:3.20")
    name = f"alphadiana-smoke-{uuid.uuid4().hex[:12]}"
    podman = PodmanCLI(default_timeout=120)
    container_id = ""

    try:
        result = podman.run(image, name=name, detach=True, command=["sleep", "60"])
        container_id = result.stdout.strip()
        assert container_id

        exec_result = podman.exec(container_id, ["sh", "-lc", "printf smoke-ok"])
        assert exec_result.stdout == "smoke-ok"

        src = tmp_path / "input.txt"
        dst = tmp_path / "output.txt"
        src.write_text("copy-ok", encoding="utf-8")
        podman.cp(str(src), f"{container_id}:/tmp/input.txt")
        podman.cp(f"{container_id}:/tmp/input.txt", str(dst))
        assert dst.read_text(encoding="utf-8") == "copy-ok"

        logs = podman.logs(container_id)
        assert logs.returncode == 0
    finally:
        if container_id:
            podman.stop(container_id, check=False)
            podman.rm(container_id, force=True, volumes=True, check=False)
