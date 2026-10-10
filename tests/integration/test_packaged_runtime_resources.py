"""Runtime configs resolve from an unrelated working directory after install."""

import shutil
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


@pytest.mark.parametrize("bundle", ["advanced-maths", "anthropic-bundle"])
def test_skill_bundle_loads_outside_source_directory(bundle, tmp_path, monkeypatch):
    from alphadiana.harness.base import resolve_runtime_skill_folder

    monkeypatch.chdir(tmp_path)
    path = Path(resolve_runtime_skill_folder(bundle))
    assert path.is_dir() and not path.is_relative_to(tmp_path)
    assert list(path.rglob("SKILL.md")), bundle
    resources = [p for p in path.rglob("*") if p.is_file()]
    assert any(p.name != "SKILL.md" and p.suffix != ".py" for p in resources)
    destination = tmp_path / "agent-skills"
    shutil.copytree(path, destination)
    assert all(
        (destination / p.relative_to(path)).read_bytes() == p.read_bytes()
        for p in resources
    )
