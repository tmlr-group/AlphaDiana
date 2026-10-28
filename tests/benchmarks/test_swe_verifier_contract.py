"""Prebuilt SWE images and exact patch bytes are verifier inputs, not build requests."""

import json
from types import SimpleNamespace

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.benchmarks.swe_bench import scorer as module
from alphadiana.harness.base import AgentResponse


@pytest.mark.parametrize("ending", ["\n", " \n", ""])
def test_remote_image_skips_builds_and_preserves_patch_context(
    tmp_path, monkeypatch, ending
):
    task = BenchmarkTask(
        "swe_fixture",
        "Fix",
        "reference",
        {
            "instance_id": "fixture",
            "repo": "owner/repo",
            "base_commit": "commit",
        },
    )
    patch = "diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new" + ending
    expected = patch if patch.endswith("\n") else patch + "\n"
    (tmp_path / "report.json").write_text(json.dumps({"fixture": {"resolved": True}}))

    def run_instance(spec, prediction, *args, **kwargs):
        assert prediction["model_patch"] == expected
        return {"completed": True}

    runtime = {
        "docker": object(),
        "make_test_spec": lambda *args, **kwargs: SimpleNamespace(is_remote_image=True),
        "run_instance": run_instance,
        "KEY_INSTANCE_ID": "instance_id",
        "KEY_MODEL": "model_name_or_path",
        "KEY_PREDICTION": "model_patch",
        "LOG_REPORT": "report.json",
        "LOG_INSTANCE": "run_instance.log",
    }
    scorer = module.SWEBenchScorer()
    monkeypatch.setattr(scorer, "_runtime_api", lambda: runtime)
    monkeypatch.setattr(scorer, "_set_eval_log_dir", lambda runtime: None)
    monkeypatch.setattr(scorer, "_docker_client", lambda docker: object())
    monkeypatch.setattr(scorer, "_log_dir_for", lambda *args: tmp_path)
    monkeypatch.setattr(
        module, "harden_test_spec_repo_clone", lambda *args, **kwargs: None
    )
    result = scorer.score(task, AgentResponse(patch))
    assert result.correct


@pytest.mark.parametrize("cached,force", [(True, False), (False, False), (True, True)])
def test_remote_sandbox_uses_image_cache_or_pull(tmp_path, monkeypatch, cached, force):
    from unittest.mock import Mock

    from alphadiana.engine.sandbox.swebench_container import SWEBenchContainerSandbox

    class MissingImage(Exception):
        pass

    client = Mock()
    if not cached:
        client.images.get.side_effect = MissingImage
    runtime = {
        "docker": SimpleNamespace(errors=SimpleNamespace(ImageNotFound=MissingImage)),
        "setup_logger": Mock(),
        "close_logger": Mock(),
    }
    sandbox = SWEBenchContainerSandbox()
    sandbox._config = {"force_rebuild": force}
    sandbox._log_dir = tmp_path
    monkeypatch.setattr(sandbox, "_runtime_api", lambda: runtime)
    monkeypatch.setattr(sandbox, "_docker_client", lambda docker: client)
    task = BenchmarkTask("swe_fixture", "Fix", None, {"instance_id": "fixture"})
    spec = SimpleNamespace(is_remote_image=True, instance_image_key="owned:fixture")
    sandbox._build_image(task, spec)
    assert client.images.pull.call_count == int(force or not cached)
    runtime["close_logger"].assert_called_once()


@pytest.mark.parametrize("owner", ["sandbox", "scorer"])
@pytest.mark.parametrize("engine", ["docker", "podman"])
@pytest.mark.parametrize("configured", [None, 240])
def test_container_start_can_exceed_sdk_default_timeout(tmp_path, owner, engine, configured):
    from alphadiana.engine.sandbox.swebench_container import SWEBenchContainerSandbox

    settings = {"container_engine": engine, "docker_api_version": "1.40", "log_dir": str(tmp_path)}
    if configured is not None:
        settings["docker_api_timeout"] = configured
    provider = SWEBenchContainerSandbox() if owner == "sandbox" else module.SWEBenchScorer()
    provider.setup(settings)
    observed = {}

    def from_env(**kwargs):
        observed.update(kwargs)
        deadline = kwargs.get("timeout", 60)

        def start():
            # Real A100 startup took 60.6s; the SDK's default 60s fails first.
            if deadline <= 61:
                raise TimeoutError("Container startup exceeded Docker SDK deadline")
            return "running"

        return SimpleNamespace(containers=SimpleNamespace(run=start))

    client = provider._docker_client(SimpleNamespace(from_env=from_env))
    assert client.containers.run() == "running"
    assert observed["timeout"] == (configured if configured is not None else 180)
    assert observed.get("version") == ("1.40" if engine == "podman" else None)
