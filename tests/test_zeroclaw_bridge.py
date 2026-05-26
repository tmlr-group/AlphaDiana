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
        "ZEROCLAW_TOP_P",
        "ZEROCLAW_PROVIDER_REQUEST_OVERRIDES",
        "ZEROCLAW_PROVIDER_TIMEOUT_SECS",
        "ZEROCLAW_PROVIDER_MAX_TOKENS",
        "ZEROCLAW_DISABLE_TOOLS",
        "ZEROCLAW_REASONING_ENABLED",
        "ZEROCLAW_REASONING_EFFORT",
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


def test_zeroclaw_runtime_manager_exports_provider_timeout_for_bridge():
    manager = ZeroClawRuntimeManager({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "request_timeout": 900,
    })

    env = manager._runtime_env()

    assert env["ZEROCLAW_PROVIDER_TIMEOUT_SECS"] == "900"


def test_zeroclaw_runtime_manager_exports_request_controls_for_bridge():
    manager = ZeroClawRuntimeManager({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
        "max_tokens": 32768,
        "top_p": 0.95,
        "enable_thinking": False,
        "disable_tools": True,
        "reasoning_enabled": True,
        "reasoning_effort": "high",
    })

    env = manager._runtime_env()

    assert env["ZEROCLAW_PROVIDER_MAX_TOKENS"] == "32768"
    assert env["ZEROCLAW_TOP_P"] == "0.95"
    assert json.loads(env["ZEROCLAW_PROVIDER_REQUEST_OVERRIDES"]) == {
        "chat_template_kwargs": {"enable_thinking": False},
        "reasoning": {"enabled": True},
    }
    assert env["ZEROCLAW_DISABLE_TOOLS"] == "true"
    assert env["ZEROCLAW_REASONING_ENABLED"] == "true"
    assert env["ZEROCLAW_REASONING_EFFORT"] == "high"


def test_zeroclaw_runtime_manager_reuses_alive_gateway(monkeypatch):
    manager = ZeroClawRuntimeManager({
        "model": "demo-model",
        "api_base": "https://example.test/v1",
        "api_key": "sk-test",
    })

    class _Sandbox:
        sandbox_id = "sandbox-123"

        def proxy_v1_base(self) -> str:
            return "http://proxy.test/apis/envs/sandbox/v1/sandboxes/sandbox-123/proxy/v1"

        def upload(self, *_args, **_kwargs):
            raise AssertionError("upload should not be called when reusing a live gateway")

        def execute(self, *_args, **_kwargs):
            raise AssertionError("execute should not be called when reusing a live gateway")

    monkeypatch.setattr(manager, "_probe_gateway_alive", lambda sandbox: True)

    info = manager.ensure_ready(_Sandbox())

    assert info["sandbox_id"] == "sandbox-123"
    assert info["gateway_url"].endswith("/chat/completions")


def test_zeroclaw_bridge_config_toml_uses_env_default_temperature(monkeypatch):
    module = _load_bridge_module(
        monkeypatch,
        ZEROCLAW_TEMPERATURE="0.2",
        ZEROCLAW_PROVIDER_TIMEOUT_SECS="600",
        ZEROCLAW_PROVIDER_MAX_TOKENS="32768",
        ZEROCLAW_REASONING_ENABLED="true",
        ZEROCLAW_REASONING_EFFORT="high",
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    assert "default_temperature = 0.2" in module._build_config_toml()
    assert "provider_timeout_secs = 600" in module._build_config_toml()
    assert "provider_max_tokens = 32768" in module._build_config_toml()
    assert "reasoning_enabled = true" in module._build_config_toml()
    assert 'reasoning_effort = "high"' in module._build_config_toml()
    assert 'default_provider = "custom"' in module._build_config_toml()
    assert "[model_providers.custom]" in module._build_config_toml()
    assert 'base_url = "https://example.test/v1"' in module._build_config_toml()
    assert "[model_providers.custom:https://example.test/v1]" not in module._build_config_toml()
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


def test_zeroclaw_bridge_disable_tools_uses_provider_path(monkeypatch):
    module = _load_bridge_module(
        monkeypatch,
        ZEROCLAW_GATEWAY_TOKEN="ZEROCLAW",
        ZEROCLAW_DISABLE_TOOLS="true",
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    seen: dict[str, object] = {}

    def fake_run_provider(messages, *, temperature: float | None = None, attachments=None) -> str:
        seen["messages"] = messages
        seen["temperature"] = temperature
        seen["attachments"] = attachments
        return "analysis\n\n$$\\boxed{A}$$"

    monkeypatch.setattr(module, "_run_provider_chat_without_tools", fake_run_provider)

    body = json.dumps({
        "model": "demo-model",
        "disable_tools": True,
        "messages": [{"role": "user", "content": "Read the image."}],
        "temperature": 0.4,
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

    assert seen["messages"][0]["content"] == "Read the image."
    assert seen["temperature"] == 0.4
    assert seen["attachments"][0]["mime"] == "image/png"
    assert captured["status"] == 200


def test_zeroclaw_bridge_provider_path_sends_top_p_and_max_tokens(monkeypatch, tmp_path):
    module = _load_bridge_module(
        monkeypatch,
        ZEROCLAW_DISABLE_TOOLS="true",
        ZEROCLAW_TEMPERATURE="0.0",
        ZEROCLAW_TOP_P="0.95",
        ZEROCLAW_PROVIDER_MAX_TOKENS="65536",
        ZEROCLAW_PROVIDER_REQUEST_OVERRIDES=json.dumps({
            "chat_template_kwargs": {"enable_thinking": False},
        }),
        ZEROCLAW_ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    seen: dict[str, object] = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return json.dumps({
                "choices": [{"message": {"content": "analysis\n\n$$\\boxed{A}$$"}}]
            }).encode("utf-8")

    def fake_urlopen(request, timeout: int):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr(module, "urlopen", fake_urlopen)

    output = module._run_provider_chat_without_tools(
        [{"role": "user", "content": "Pick A."}],
        temperature=0.2,
    )

    assert output == "analysis\n\n$$\\boxed{A}$$"
    assert seen["url"] == "https://example.test/v1/chat/completions"
    assert seen["timeout"] == 1200
    assert seen["payload"]["temperature"] == 0.2
    assert seen["payload"]["top_p"] == 0.95
    assert seen["payload"]["max_tokens"] == 65536
    assert seen["payload"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_zeroclaw_bridge_persists_last_request_artifacts(monkeypatch, tmp_path):
    module = _load_bridge_module(
        monkeypatch,
        ZEROCLAW_ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    def fake_run(command, shell, cwd, env, capture_output, text):
        assert env["OPENAI_BASE_URL"].startswith("http://127.0.0.1:")
        assert env["ZEROCLAW_PROVIDER"].startswith("custom:http://127.0.0.1:")
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
    assert status["vision_proxy_image_count"] == 1
    assert status["vision_proxy_request_count"] == 0
    assert status["vision_proxy_injection_count"] == 0
    manifest = json.loads((last_request / "attachment_manifest.json").read_text(encoding="utf-8"))
    assert manifest["attachments"][0]["path"] == "attachments/image_1.png"
    assert (last_request / "zeroclaw_output.txt").read_text(encoding="utf-8") == "partial reasoning"
    assert (last_request / "zeroclaw_stderr.log").read_text(encoding="utf-8") == "stderr text"
    assert '"event":"tool"' in (last_request / "runtime_trace.jsonl").read_text(encoding="utf-8")


def test_zeroclaw_bridge_sanitize_output_drops_runtime_logs(monkeypatch):
    module = _load_bridge_module(
        monkeypatch,
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    cleaned, dropped = module._sanitize_output(
        "\n".join(
            [
                "[ZeroClawBridge] listening on 0.0.0.0:8080",
                "2026-04-19T01:23:45Z INFO zeroclaw::provider retrying request in 2s",
                "OpenRouter transport error while reading response body",
                "$$\\boxed{42}$$",
            ]
        )
    )

    assert cleaned == "$$\\boxed{42}$$"
    assert any("transport error" in line.lower() for line in dropped)


def test_zeroclaw_bridge_raises_when_stdout_is_only_runtime_logs(monkeypatch, tmp_path):
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
        Path(stdout_path).write_text(
            "\n".join(
                [
                    "[ZeroClawBridge] listening on 0.0.0.0:8080",
                    "OpenRouter transport error while reading response body",
                    "Retrying request in 2s",
                ]
            ),
            encoding="utf-8",
        )
        Path(stderr_path).write_text("", encoding="utf-8")
        return type("Proc", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    try:
        module._run_zeroclaw("Inspect logs.")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected provider/runtime log-only stdout to fail")

    assert (
        "transport error" in message.lower()
        or "runtime/provider logs" in message.lower()
        or "retrying request" in message.lower()
    )


def test_readme_zeroclaw_autodeploy_mentions_sourcing_rock_env():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "bash scripts/start_zeroclaw.sh" in readme
    assert "source scripts/rock_env.sh" in readme


def test_zeroclaw_bridge_server_allows_fast_rebind(monkeypatch):
    module = _load_bridge_module(
        monkeypatch,
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )

    assert module.ReusableThreadingHTTPServer.allow_reuse_address is True
    assert module.ReusableThreadingHTTPServer.daemon_threads is True


# ---------------------------------------------------------------------------
# Finding #5 — non-leading system message normalizer (no system at index>0)
# ---------------------------------------------------------------------------

def test_normalize_system_messages_hoists_user_system(monkeypatch):
    """[user, system] -> leading system, then user."""
    module = _load_bridge_module(
        monkeypatch,
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )
    out = module._normalize_system_messages(
        [
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "remember X"},
        ]
    )
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "remember X"
    assert out[1]["role"] == "user"
    # Invariant: no role=system after index 0.
    assert all(
        not (isinstance(m, dict) and m.get("role") == "system")
        for m in out[1:]
    )


def test_normalize_system_messages_merges_leading_and_trailing(monkeypatch):
    """[system, user, system] -> [system_merged, user]."""
    module = _load_bridge_module(
        monkeypatch,
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )
    out = module._normalize_system_messages(
        [
            {"role": "system", "content": "rule A"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "rule B"},
        ]
    )
    assert len(out) == 2
    assert out[0]["role"] == "system"
    assert "rule A" in out[0]["content"]
    assert "rule B" in out[0]["content"]
    assert out[1] == {"role": "user", "content": "hi"}
    assert all(
        not (isinstance(m, dict) and m.get("role") == "system")
        for m in out[1:]
    )


def test_normalize_system_messages_mixed_content_types(monkeypatch):
    """system(str) + system(list) → leading system, no crash, no surviving non-leading system."""
    module = _load_bridge_module(
        monkeypatch,
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )
    out = module._normalize_system_messages(
        [
            {"role": "system", "content": "rule A"},
            {"role": "user", "content": "hi"},
            {"role": "system", "content": [{"type": "text", "text": "rule B"}]},
        ]
    )
    assert out[0]["role"] == "system"
    # Leading content is now a list because we promoted str+list union.
    assert isinstance(out[0]["content"], list)
    assert all(
        not (isinstance(m, dict) and m.get("role") == "system")
        for m in out[1:]
    )


def test_normalize_system_messages_no_system_or_empty(monkeypatch):
    """No system or empty input is a no-op (no crash)."""
    module = _load_bridge_module(
        monkeypatch,
        OPENAI_MODEL_NAME="demo-model",
        OPENAI_BASE_URL="https://example.test/v1",
        OPENAI_API_KEY="sk-test",
    )
    assert module._normalize_system_messages([]) == []
    only_user = [{"role": "user", "content": "hi"}]
    assert module._normalize_system_messages(only_user) == only_user
