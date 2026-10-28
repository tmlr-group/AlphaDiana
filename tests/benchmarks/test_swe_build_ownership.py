"""Task projection and Docker/Podman build boundaries shared by SWE consumers."""

import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.swe_bench.tasks import build_swebench_instance
from alphadiana.engine.container_runtime import swebench_build as build


def test_task_projection_preserves_official_fields_and_test_lists():
    task = BenchmarkTask(
        "swe_owner__repo-42",
        "Fix this",
        "reference patch\n",
        {
            "repo": "owner/repo",
            "base_commit": "abc",
            "test_patch": "tests\n",
            "FAIL_TO_PASS": ["test_fix"],
            "PASS_TO_PASS": '["test_old"]',
        },
    )
    instance = build_swebench_instance(task)
    assert instance["instance_id"] == "owner__repo-42"
    assert instance["patch"] == "reference patch\n"
    assert instance["test_patch"] == "tests\n"
    assert json.loads(instance["FAIL_TO_PASS"]) == ["test_fix"]
    assert json.loads(instance["PASS_TO_PASS"]) == ["test_old"]


@pytest.mark.parametrize("podman", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_build_hooks_preserve_engine_options_and_report_failures(
    tmp_path, monkeypatch, podman, fail
):
    class BuildError(Exception):
        def __init__(self, message, log):
            super().__init__(message)

    class BuildImageError(Exception):
        def __init__(self, image, message, logger):
            super().__init__(f"{image}: {message}")

    runtime = ModuleType("swebench.harness.docker_build")
    runtime.setup_logger = Mock(return_value=Mock())
    runtime.close_logger = Mock()
    runtime.ansi_escape = lambda text: text
    runtime.docker = SimpleNamespace(errors=SimpleNamespace(BuildError=BuildError))
    runtime.BuildImageError = BuildImageError
    package, harness = ModuleType("swebench"), ModuleType("swebench.harness")
    package.harness, harness.docker_build = harness, runtime
    for name, module in [
        ("swebench", package),
        ("swebench.harness", harness),
        ("swebench.harness.docker_build", runtime),
    ]:
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("ALPHADIANA_SWEBENCH_BUILD_ATTEMPTS", "2")
    monkeypatch.setenv("ALPHADIANA_SWEBENCH_BUILD_RETRY_SLEEP_SEC", "0")
    monkeypatch.delenv("ALPHADIANA_SWEBENCH_PODMAN_BUILD", raising=False)
    for key in build._PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("https_proxy", "http://fixture:8080")
    client = Mock()
    client.api.build.side_effect = lambda **kwargs: iter(
        [{"errorDetail": {"message": "captured build failure"}}]
        if fail
        else [{"stream": "Successfully built fixture\n"}]
    )
    build.ensure_swebench_build_network_mode("host", podman_mode=podman)
    kwargs = dict(
        image_name="sweb.env.fixture",
        setup_scripts={"setup.sh": "echo ok"},
        dockerfile="FROM --platform=linux/amd64 sweb.base.fixture\nCOPY setup.sh /setup.sh\n",
        platform="linux/amd64",
        client=client,
        build_dir=tmp_path,
    )
    if fail:
        with pytest.raises(BuildImageError, match="captured build failure"):
            runtime.build_image(**kwargs)
    else:
        runtime.build_image(**kwargs)
    assert client.api.build.call_count == (2 if fail and podman else 1)
    options = client.api.build.call_args.kwargs
    assert options["network_mode"] == "host"
    assert options["tag"] == (
        "localhost/sweb.env.fixture" if podman else "sweb.env.fixture"
    )
    if podman:
        assert "platform" not in options
        assert options["buildargs"] == {"https_proxy": "http://fixture:8080"}
        assert "--platform=" not in (tmp_path / "Dockerfile").read_text()
    else:
        assert options["platform"] == "linux/amd64"
        assert "buildargs" not in options
    runtime.close_logger.assert_called_once()
