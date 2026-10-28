"""Constructed fixtures verify settings delivered to the installed Pi CLI."""

import json
import os
from types import SimpleNamespace

import pytest

from alphadiana.harness._internal.deploy import bridge
from alphadiana.harness._internal.podman_agent import validate_config


def config(**overrides):
    return {
        "image": "sha256:" + "a" * 64,
        "model": "fixture",
        "api_base": "http://localhost:1/v1",
        "max_tokens": 32000,
        "context_window": 65536,
        **overrides,
    }


@pytest.mark.parametrize("penalty", [None, 0.0, 1.5])
@pytest.mark.parametrize(
    "window, output", [(24576, 4096), (65536, 32000), (114688, 81920), (131072, 81920)]
)
def test_pi_preserves_native_compaction_budget(
    tmp_path, monkeypatch, penalty, window, output
):
    monkeypatch.setenv("HOME", str(tmp_path))
    seen = []

    def native(*args, **kwargs):
        home = tmp_path / ".pi" / "agent"
        model = json.loads((home / "models.json").read_text())["providers"]["diana"][
            "models"
        ][0]
        settings = json.loads((home / "settings.json").read_text())["compaction"]
        assert model["contextWindow"] == window
        assert model["maxTokens"] == output
        if penalty is None:
            assert "samplingParams" not in model
        else:
            assert model["samplingParams"] == {"presence_penalty": penalty}
        assert settings == {
            "enabled": True,
            "reserveTokens": 16384,
            "keepRecentTokens": min(20000, (window - 16384) // 2),
        }
        assert (
            settings["keepRecentTokens"] + settings["reserveTokens"]
            < model["contextWindow"]
        )
        seen.append(True)
        return SimpleNamespace(
            stderr="",
            stdout=json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "ok"}],
                        "stopReason": "stop",
                    },
                }
            ),
        )

    monkeypatch.setattr(bridge.subprocess, "run", native)
    settings = config(
        context_window=window,
        max_tokens=output,
        **({} if penalty is None else {"presence_penalty": penalty}),
    )
    validate_config(settings)
    result = bridge.pi_run({"config": settings, "problem": "fixture"})
    assert seen and result["answer"] == "ok"


@pytest.mark.parametrize("window", [True, "65536", 0, -1, 32000, 36096])
def test_invalid_context_window_fails_before_launch(window):
    with pytest.raises(ValueError, match="context_window"):
        validate_config(config(context_window=window))


def test_other_harness_rejects_pi_only_setting():
    from alphadiana.harness.deepseek_harness.agent import DeepSeekHarnessAgent

    with pytest.raises(ValueError, match="supported only by Pi"):
        DeepSeekHarnessAgent().setup(config())


@pytest.mark.parametrize("penalty", [True, "1.5", -3, 3, float("nan"), float("inf")])
def test_invalid_presence_penalty_fails_before_launch(penalty):
    with pytest.raises(ValueError, match="presence_penalty"):
        validate_config(config(presence_penalty=penalty))


def test_deepseek_rejects_presence_penalty():
    from alphadiana.harness.deepseek_harness.agent import DeepSeekHarnessAgent

    settings = config(presence_penalty=1.5)
    settings.pop("context_window")
    with pytest.raises(ValueError, match="presence_penalty is supported only by Pi"):
        DeepSeekHarnessAgent().setup(settings)


@pytest.mark.parametrize("enabled", [None, True, False])
def test_presence_penalty_cannot_be_silently_overridden_by_training(enabled):
    from dataclasses import asdict
    from tests.harness.test_pi_training import plan

    training = {"sampling_plan": asdict(plan())}
    if enabled is not None:
        training["enabled"] = enabled
    settings = config(presence_penalty=1.5, training=training)
    if enabled is False:
        validate_config(settings)
    else:
        with pytest.raises(ValueError, match="presence_penalty cannot be combined"):
            validate_config(settings)


@pytest.mark.skipif(
    not os.environ.get("ALPHADIANA_PI_BIN"),
    reason="Set ALPHADIANA_PI_BIN to an installed Pi executable",
)
def test_installed_pi_compacts_with_native_summary_budget(tmp_path, monkeypatch):
    from scripts.validation.native_model_fixture import model_server

    monkeypatch.setenv("HOME", str(tmp_path))
    # Constructed usage forces real Pi's compaction without generating 35k tokens.
    with model_server(prompt_tokens=35000, shell_command="printf '%49000s' X") as (
        url, requests
    ):
        result = bridge.pi_run(
            {
                "config": config(
                    pi_bin=os.environ["ALPHADIANA_PI_BIN"],
                    api_base=url.replace("host.containers.internal", "127.0.0.1"),
                    timeout=30,
                    context_window=40000,
                ),
                "problem": "Read marker using the shell.",
                "work_dir": str(tmp_path),
            }
        )
    summaries = [r for r in requests if not r.get("tools")]
    assert summaries, "Pi did not compact above the configured threshold"
    # Pi budgets 80% for the summary and 50% for a split-turn prefix.
    budgets = {r.get("max_tokens", r.get("max_completion_tokens")) for r in summaries}
    assert budgets <= {int(16384 * 0.8), int(16384 * 0.5)}, budgets
    assert result["answer"] == "0" and result["finish_reason"] == "stop"
