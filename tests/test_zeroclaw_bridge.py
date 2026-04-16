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
        "ZEROCLAW_ARTIFACT_ROOT",
        "ZEROCLAW_PROVIDER",
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

    def fake_run(prompt: str, *, temperature: float | None = None, attachments=None) -> str:
        seen["prompt"] = prompt
        seen["temperature"] = temperature
        seen["attachments"] = attachments
        return "analysis\n\n$$\\boxed{42}$$"

    monkeypatch.setattr(module, "_run_zeroclaw", fake_run)

    body = json.dumps({
        "model": "demo-model",
        "messages": [{"role": "user", "content": "What is 6*7?"}],
        "temperature": 0.25,
        "attachments": [
            {
                "key": "image_1",
                "filename": "image_1.png",
                "path": "attachments/image_1.png",
                "mime": "image/png",
                "data_base64": "ZmFrZS1wbmc=",
            }
        ],
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
    assert seen["attachments"][0]["path"] == "attachments/image_1.png"
    assert captured["status"] == 200


def test_zeroclaw_bridge_persists_last_request_artifacts(monkeypatch, tmp_path):
    module = _load_bridge_module(
        monkeypatch,
        ZEROCLAW_ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    def fake_run(command, shell, cwd, env, capture_output, text):
        stdout_path = command.split("> ", 1)[1].split(" 2> ", 1)[0]
        stderr_path = command.split(" 2> ", 1)[1]
        Path(stdout_path).write_text("partial reasoning", encoding="utf-8")
        Path(stderr_path).write_text("stderr text", encoding="utf-8")
        (Path(cwd) / "state" / "runtime-trace.jsonl").write_text('{"event":"tool"}\n', encoding="utf-8")
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    output = module._run_zeroclaw(
        "Inspect attachments.",
        temperature=0.3,
        attachments=[
            {
                "key": "image_1",
                "filename": "image_1.png",
                "path": "attachments/image_1.png",
                "mime": "image/png",
                "data": b"fake-png",
            }
        ],
    )

    last_request = tmp_path / "artifacts" / "last_request"
    assert output == "partial reasoning"
    assert (last_request / "task.txt").read_text(encoding="utf-8") == "Inspect attachments."
    status = json.loads((last_request / "status.json").read_text(encoding="utf-8"))
    assert status["temperature"] == 0.3
    manifest = json.loads((last_request / "attachment_manifest.json").read_text(encoding="utf-8"))
    assert manifest["attachments"][0]["path"] == "attachments/image_1.png"
    assert (last_request / "zeroclaw_output.txt").read_text(encoding="utf-8") == "partial reasoning"
    assert (last_request / "zeroclaw_stderr.log").read_text(encoding="utf-8") == "stderr text"
    assert '"event":"tool"' in (last_request / "runtime_trace.jsonl").read_text(encoding="utf-8")


def test_readme_zeroclaw_autodeploy_mentions_sourcing_rock_env():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "bash scripts/start_zeroclaw.sh" in readme
    assert "source scripts/rock_env.sh" in readme
