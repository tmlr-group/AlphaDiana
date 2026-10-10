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
