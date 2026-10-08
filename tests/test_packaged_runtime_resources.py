"""Runtime configs resolve from an unrelated working directory after install."""

from pathlib import Path

import pytest

from alphadiana.harness.openclaw.container_runtime import (
    OpenClawContainerRuntimeManager,
)
from alphadiana.harness.openclaw.runtime import OpenClawRuntimeManager
from alphadiana.harness.zeroclaw.runtime import ZeroClawRuntimeManager


@pytest.mark.parametrize(
    "owner,method,relative",
    [
        (
            OpenClawRuntimeManager,
            "_resolve_config_path",
            "alphadiana/harness/openclaw/deploy/openclaw.json",
        ),
        (
            OpenClawContainerRuntimeManager,
            "_resolve_config_path",
            "alphadiana/harness/openclaw/deploy/openclaw_swe_bench.runtime.json",
        ),
        (
            ZeroClawRuntimeManager,
            "_resolve_bridge_template_path",
            "alphadiana/harness/zeroclaw/deploy/zeroclaw_bridge.py",
        ),
    ],
)
def test_packaged_default_and_explicit_override(
    owner, method, relative, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    instance = object.__new__(owner)
    resolve = getattr(instance, method)
    bundled = resolve(relative)
    assert bundled.is_file() and not bundled.is_relative_to(tmp_path)
    custom = Path(relative)
    custom.parent.mkdir(parents=True)
    custom.write_text("caller override")
    assert resolve(relative) == custom.resolve()
