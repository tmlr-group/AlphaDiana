"""Contract tests for shared owners outside saved-run analysis."""

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from alphadiana.engine.config.experiment_config import resolve_agent_setting
from alphadiana.engine.container_runtime.swebench_build import (
    swebench_runtime_environment,
)
from scripts.paper_analysis.output import ARCHIVE_ROOT, prepare_output_dir


@pytest.mark.parametrize(
    "value,env,expected",
    [
        (" explicit ", "environment", "explicit"),
        (None, "environment", "fallback"),
        ("EMPTY", "environment", "environment"),
        ("  ", "environment", "environment"),
        ("EMPTY", "", "EMPTY"),
        (None, "", "fallback"),
        (123, "", "fallback"),
    ],
)
def test_setting_precedence_preserves_empty_placeholder_policy(
    monkeypatch, value, env, expected
):
    monkeypatch.setenv("DIANA_TEST_SETTING", env)
    assert (
        resolve_agent_setting(
            {"setting": value}, "setting", "DIANA_TEST_SETTING", default="fallback"
        )
        == expected
    )


def test_swe_environment_restores_nested_and_exceptional_context(monkeypatch):
    import os

    import alphadiana.engine.container_runtime.swebench_build as build

    monkeypatch.setattr(build, "podman_socket_env", lambda path: {"DOCKER_HOST": path})
    monkeypatch.setenv("DOCKER_HOST", "original")
    monkeypatch.delenv("ALPHADIANA_SWEBENCH_PODMAN_BUILD", raising=False)
    monkeypatch.delenv("DOCKER_API_VERSION", raising=False)
    with swebench_runtime_environment("docker", "unused", "unused"):
        assert os.environ["DOCKER_HOST"] == "original"
    with swebench_runtime_environment("podman", "outer", "1.40"):
        assert os.environ["DOCKER_HOST"] == "outer"
        with pytest.raises(RuntimeError):
            with swebench_runtime_environment("podman", "inner", "1.41"):
                assert os.environ["DOCKER_HOST"] == "inner"
                raise RuntimeError("constructed failure")
        assert os.environ["DOCKER_HOST"] == "outer"
        assert os.environ["DOCKER_API_VERSION"] == "1.40"
    assert os.environ["DOCKER_HOST"] == "original"
    assert "DOCKER_API_VERSION" not in os.environ
    assert "ALPHADIANA_SWEBENCH_PODMAN_BUILD" not in os.environ


@pytest.mark.parametrize("backend_result", [True, False, None, "exception"])
def test_math_backend_preserves_false_and_unavailable(monkeypatch, backend_result):
    from alphadiana.benchmarks.imo import verify as imo
    from alphadiana.scorer.math_verify_scorer import math_verify_equal

    package = ModuleType("math_verify")
    parser = ModuleType("math_verify.parser")
    parser.LatexExtractionConfig = parser.ExprExtractionConfig = lambda: object()
    package.parse = lambda *a, **kw: [] if backend_result is None else [1]

    def verify(*args):
        if backend_result == "exception":
            raise ValueError("constructed parse failure")
        return backend_result

    package.verify = verify
    monkeypatch.setitem(sys.modules, "math_verify", package)
    monkeypatch.setitem(sys.modules, "math_verify.parser", parser)
    assert imo.math_verify_equal is math_verify_equal
    expected = backend_result if isinstance(backend_result, bool) else None
    assert math_verify_equal("1", "1") is expected
    if backend_result is False:
        assert imo._single_match("1", "1") == (False, "math_verify")
    elif expected is None:
        assert imo._single_match("1", "1") == (True, "normalized_string")
        assert imo._single_match("x", "2") == (False, "blocked_free_var")


def test_output_boundary_preserves_archive_and_refuses_reuse(tmp_path):
    with pytest.raises(ValueError, match="archive"):
        prepare_output_dir(ARCHIVE_ROOT / "data")
    with pytest.raises(ValueError, match="archive"):
        prepare_output_dir(ARCHIVE_ROOT / "figures" / "new")
    link = tmp_path / "archive-link"
    link.symlink_to(ARCHIVE_ROOT / "data", target_is_directory=True)
    with pytest.raises(ValueError, match="archive"):
        prepare_output_dir(link)
    source = tmp_path / "input"
    source.mkdir()
    (source / "values.csv").write_text("name,value\nitem,1\n")
    output = prepare_output_dir(tmp_path / "fresh", data_dir=source)
    receipt = json.loads((output / "input-provenance.json").read_text())
    assert receipt["input_csv_sha256"]["values.csv"]
    with pytest.raises(ValueError, match="empty"):
        prepare_output_dir(output, data_dir=source)
    with pytest.raises(ValueError, match="overlap"):
        prepare_output_dir(source / "outputs", data_dir=source)
    assert (source / "values.csv").read_text() == "name,value\nitem,1\n"


def test_paper_missing_input_fails_before_creating_output(tmp_path):
    output = tmp_path / "fresh"
    with pytest.raises(FileNotFoundError):
        prepare_output_dir(output, data_dir=tmp_path / "missing")
    assert not output.exists()


def test_openclaw_proxy_keeps_gateway_fields_and_capture_separate():
    """Constructed SSE event exercises the gateway's distinct wire contract."""
    import threading
    from types import SimpleNamespace

    from alphadiana.harness.proxies.openclaw import _OpenClawLogprobProxyHandler

    handler = _OpenClawLogprobProxyHandler.__new__(_OpenClawLogprobProxyHandler)
    handler.server = SimpleNamespace(lock=threading.Lock(), streaming_logprobs=[])
    token = {"token": "answer", "logprob": -0.1, "top_logprobs": []}
    payload = {
        "choices": [
            {"delta": {"reasoning": "thinking"}, "logprobs": {"content": [token]}}
        ]
    }
    event = b"event: message\ndata: " + json.dumps(payload).encode()
    forwarded = handler._strip_and_capture_event(event)
    result = json.loads(forwarded.split(b"data: ", 1)[1])
    assert result["choices"][0]["delta"] == {"reasoning_content": "thinking"}
    assert result["choices"][0]["logprobs"] is None
    assert handler.server.streaming_logprobs == [token]
    assert handler._strip_and_capture_event(b"data: [DONE]") == b"data: [DONE]"
    assert handler._strip_and_capture_event(b"data: {truncated") == b"data: {truncated"


def test_runtime_skill_resolution_keeps_bundle_root_and_cwd(tmp_path, monkeypatch):
    from alphadiana.harness import base
    from alphadiana.harness.base import resolve_runtime_skill_folder

    monkeypatch.chdir(tmp_path)
    assert resolve_runtime_skill_folder(None) is None
    assert resolve_runtime_skill_folder("advanced-maths") == str(
        Path(base.__file__).parent / "skills/advanced-maths"
    )
    assert resolve_runtime_skill_folder("custom/folder") == str(
        tmp_path / "custom/folder"
    )
    assert resolve_runtime_skill_folder(str(tmp_path / "absolute")) == str(
        tmp_path / "absolute"
    )
