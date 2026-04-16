from __future__ import annotations

import importlib.util
import io
import json
import uuid
from pathlib import Path

from alphadiana.agent.zeroclaw_runtime import ZeroClawRuntimeManager


def _load_bridge_module(monkeypatch, **env):
    bridge_path = Path("zeroclaw_deploy/zeroclaw_bridge.py")
    for key in [
        "ZEROCLAW_GATEWAY_TOKEN",
        "ZEROCLAW_TEMPERATURE",
        "OPENAI_MODEL_NAME",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, str(value))

    module_name = f"test_zeroclaw_bridge_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, bridge_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_zeroclaw_runtime_manager_exports_temperature_for_bridge():
    manager = ZeroClawRuntimeManager({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "temperature": 0.2,
    })

    env = manager._runtime_env()

    assert env["ZEROCLAW_TEMPERATURE"] == "0.2"


def test_zeroclaw_bridge_config_toml_uses_env_default_temperature(monkeypatch):
    module = _load_bridge_module(
        monkeypatch,
        ZEROCLAW_TEMPERATURE="0.2",
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    assert "default_temperature = 0.2" in module._build_config_toml()
    assert "default_temperature = 0.4" in module._build_config_toml(0.4)


def test_zeroclaw_bridge_post_forwards_request_temperature(monkeypatch):
    module = _load_bridge_module(
        monkeypatch,
        ZEROCLAW_GATEWAY_TOKEN="ZEROCLAW",
        ZEROCLAW_TEMPERATURE="0.0",
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    seen: dict[str, object] = {}

    def fake_run(prompt: str, *, temperature: float | None = None) -> str:
        seen["prompt"] = prompt
        seen["temperature"] = temperature
        return "analysis\n\n$$\\boxed{42}$$"

    monkeypatch.setattr(module, "_run_zeroclaw", fake_run)

    body = json.dumps({
        "model": "demo-model",
        "messages": [{"role": "user", "content": "What is 6*7?"}],
        "temperature": 0.25,
    }).encode("utf-8")

    handler = object.__new__(module.Handler)
    handler.headers = {
        "Authorization": "bearer ZEROCLAW",
        "Content-Length": str(len(body)),
    }
    handler.path = "/v1/chat/completions"
    handler.rfile = io.BytesIO(body)
    captured: dict[str, object] = {}

    def fake_write_json(status: int, payload: dict[str, object]) -> None:
        captured["status"] = status
        captured["payload"] = payload

    handler._write_json = fake_write_json  # type: ignore[attr-defined]

    module.Handler.do_POST(handler)

    assert seen["prompt"] == "What is 6*7?"
    assert seen["temperature"] == 0.25
    assert captured["status"] == 200


def test_readme_zeroclaw_autodeploy_mentions_sourcing_rock_env():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "bash scripts/start_zeroclaw.sh" in readme
    assert "source scripts/rock_env.sh" in readme
