"""Agent 配置与定制（问题 2.1 ~ 2.3）

覆盖:
- 2.1 Agent.md 自定义：append/override/none 三种模式、inject_agent_md()
- 2.2 默认参数：temperature=0.7、max_tokens=None、自动探测与回退
- 2.3 Embedding 配置：provider 注入、未配置时禁用 memory
"""

import json
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ===================================================================
# 2.1 Agent.md 自定义
# ===================================================================


class TestAgentMdConfig:
    """agent_md_mode / agent_md_content 配置读取。"""

    def test_default_mode_none(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1"})
        assert agent._agent_md_mode == "none"

    def test_mode_append(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({
            "api_base": "http://localhost/v1",
            "agent_md_mode": "append",
            "agent_md_content": "Use \\boxed{}",
        })
        assert agent._agent_md_mode == "append"
        assert agent._agent_md_content == "Use \\boxed{}"

    def test_mode_override(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({
            "api_base": "http://localhost/v1",
            "agent_md_mode": "override",
            "agent_md_content": "Custom instructions",
        })
        assert agent._agent_md_mode == "override"

    def test_multiline_content_preserved(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        content = "Line 1\nLine 2\nLine 3"
        agent = OpenClawAgent()
        agent.setup({
            "api_base": "http://localhost/v1",
            "agent_md_mode": "append",
            "agent_md_content": content,
        })
        assert agent._agent_md_content == content


class TestAgentMdInjection:
    """inject_agent_md() 对 sandbox 的读写行为。"""

    def _make_mgr(self, mode="none", content=""):
        from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager

        return OpenClawRuntimeManager({
            "rock_agent_config_path": "/fake/config.yaml",
            "openclaw_config_path": "/fake/openclaw.json",
            "agent_md_mode": mode,
            "agent_md_content": content,
        })

    def test_append_reads_then_writes(self):
        mgr = self._make_mgr("append", "\nUse \\boxed{}")
        sb = MagicMock()
        sb.sandbox_id = "sb-1"
        sb.read_text.return_value = "# Default\nBe helpful."

        mgr.inject_agent_md(sb)

        sb.read_text.assert_called_once_with("/root/AGENTS.md")
        sb.upload.assert_called_once()
        data = sb.upload.call_args[0][1]
        text = data.decode() if isinstance(data, bytes) else data
        assert "Default" in text
        assert "\\boxed{}" in text

    def test_override_writes_directly(self):
        mgr = self._make_mgr("override", "Completely custom")
        sb = MagicMock()
        sb.sandbox_id = "sb-2"

        mgr.inject_agent_md(sb)

        sb.read_text.assert_not_called()
        sb.upload.assert_called_once()
        data = sb.upload.call_args[0][1]
        text = data.decode() if isinstance(data, bytes) else data
        assert "Completely custom" in text

    def test_none_does_nothing(self):
        mgr = self._make_mgr("none")
        sb = MagicMock()

        mgr.inject_agent_md(sb)

        sb.read_text.assert_not_called()
        sb.upload.assert_not_called()

    def test_inject_is_idempotent_per_sandbox(self):
        mgr = self._make_mgr("append", "\nExtra")
        sb = MagicMock()
        sb.sandbox_id = "sb-3"
        sb.read_text.return_value = "Base"

        mgr.inject_agent_md(sb)
        mgr.inject_agent_md(sb)

        sb.upload.assert_called_once()


# ===================================================================
# 2.2 默认参数（temperature / max_tokens）
# ===================================================================


class TestOpenClawDefaults:
    """OpenClawAgent 默认 temperature=0.7, max_tokens=None。"""

    def _setup(self, **extra):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1", **extra})
        return agent

    def test_default_temperature(self):
        assert self._setup()._temperature == 0.7

    def test_default_max_tokens_none(self):
        assert self._setup()._max_tokens is None

    def test_explicit_temperature(self):
        assert self._setup(temperature=0.0)._temperature == 0.0

    def test_explicit_max_tokens(self):
        assert self._setup(max_tokens=4096)._max_tokens == 4096


class TestDirectLLMDefaults:
    """DirectLLMAgent 默认 temperature=0.7, max_tokens=None。"""

    def _setup(self, **extra):
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({
            "model": "m", "api_base": "http://x/v1", "api_key": "k",
            **extra,
        })
        return agent

    def test_default_temperature(self):
        assert self._setup()._temperature == 0.7

    def test_default_max_tokens_none(self):
        assert self._setup()._max_tokens is None

    def test_explicit_temperature(self):
        assert self._setup(temperature=0.0)._temperature == 0.0

    def test_explicit_max_tokens(self):
        assert self._setup(max_tokens=16384)._max_tokens == 16384


class TestDirectLLMMaxTokensAutoDetect:
    """DirectLLM max_tokens=None 时自动探测 / 回退。"""

    def _setup(self, **extra):
        from alphadiana.agent.direct_llm import DirectLLMAgent

        agent = DirectLLMAgent()
        agent.setup({
            "model": "m",
            "api_base": "http://localhost/v1",
            "api_key": "k",
            **extra,
        })
        return agent

    def test_has_resolve_method(self):
        assert hasattr(self._setup(), "_resolve_max_tokens")

    def test_caches_result(self):
        agent = self._setup()
        agent._resolved_max_tokens = 65536
        assert agent._resolve_max_tokens() == 65536

    def test_fallback_on_failure(self):
        agent = self._setup(api_base="http://unreachable:8000/v1")
        with patch("httpx.get", side_effect=Exception("fail")):
            assert agent._resolve_max_tokens() == 32768

    def test_detects_max_model_len(self):
        agent = self._setup()
        response = MagicMock(status_code=200)
        response.json.return_value = {"data": [{"max_model_len": 12345}]}

        with patch("httpx.get", return_value=response) as get:
            assert agent._resolve_max_tokens() == 12345
            assert agent._resolve_max_tokens() == 12345

        get.assert_called_once_with("http://localhost/v1/models", timeout=5.0)

    def test_solve_uses_resolved_max_tokens(self):
        from alphadiana.benchmark.base import BenchmarkTask

        agent = self._setup()
        fake_response = MagicMock()
        fake_response.choices = [MagicMock(message=MagicMock(content="\\boxed{42}"))]
        fake_response.usage = None
        create = MagicMock(return_value=fake_response)
        client = MagicMock()
        client.chat.completions.create = create

        fake_openai_module = types.SimpleNamespace(OpenAI=MagicMock(return_value=client))
        with patch.dict("sys.modules", {"openai": fake_openai_module}):
            with patch.object(agent, "_resolve_max_tokens", return_value=54321):
                agent.solve(BenchmarkTask(task_id="t1", problem="P", ground_truth="42"))

        assert create.call_args.kwargs["max_tokens"] == 54321


class TestMaxTokensAutoDetect:
    """max_tokens=None 时自动探测 / 回退。"""

    def test_has_resolve_method(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1"})
        assert hasattr(agent, "_resolve_max_tokens")

    def test_caches_result(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1"})
        agent._resolved_max_tokens = 32768
        assert agent._resolve_max_tokens() == 32768

    def test_fallback_on_failure(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://unreachable:8000/v1"})
        with patch("httpx.get", side_effect=Exception("fail")):
            assert agent._resolve_max_tokens() == 32768

    def test_payload_no_hardcoded_8192(self):
        from alphadiana.agent.openclaw import OpenClawAgent

        agent = OpenClawAgent()
        agent.setup({"api_base": "http://localhost/v1"})
        payload = agent._build_request_payload(
            [{"role": "user", "content": "hi"}]
        )
        if "max_tokens" in payload:
            assert payload["max_tokens"] != 8192


# ===================================================================
# 2.3 Embedding 配置
# ===================================================================


class TestEmbeddingConfig:
    """Embedding provider 注入与 schema 字段。"""

    def _make_mgr(self, **extra):
        from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager

        return OpenClawRuntimeManager({
            "rock_agent_config_path": "/fake/config.yaml",
            "openclaw_config_path": "/fake/openclaw.json",
            **extra,
        })

    def test_embedding_provider_injected(self):
        mgr = self._make_mgr(
            embedding_api_base="http://localhost:8001/v1",
            embedding_api_key="key",
        )
        cfg = mgr._build_openclaw_config()
        providers = cfg.get("models", {}).get("providers", {})
        emb = providers.get("embedding", {})
        assert emb["baseUrl"] == "http://localhost:8001/v1"
        assert emb["apiKey"] == "key"

    def test_no_embedding_disables_memory(self):
        mgr = self._make_mgr()
        cfg = mgr._build_openclaw_config()
        providers = cfg.get("models", {}).get("providers", {})
        assert "embedding" not in providers
        mem = cfg.get("memory", {})
        if mem:
            assert mem.get("enabled") is False

    def test_schema_has_embedding_fields(self):
        content = (Path(__file__).parent.parent / "configs" / "schema.yaml").read_text()
        assert "embedding_api_base" in content
        assert "embedding_api_key" in content

    def test_prepare_runtime_files_merges_embedding_and_memory(self, tmp_path):
        import json

        from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager

        rock_cfg = tmp_path / "rock_agent_config.yaml"
        rock_cfg.write_text("name: test\n", encoding="utf-8")
        openclaw_cfg = tmp_path / "openclaw.json"
        openclaw_cfg.write_text(
            '{"models":{"providers":{"custom_openai":{"baseUrl":"http://llm"}}}}',
            encoding="utf-8",
        )
        mgr = OpenClawRuntimeManager({
            "rock_agent_config_path": str(rock_cfg),
            "openclaw_config_path": str(openclaw_cfg),
            "embedding_api_base": "http://embed/v1",
            "embedding_api_key": "secret",
        })
        sb = MagicMock()

        mgr._prepare_runtime_files(sb)

        uploaded = sb.upload.call_args.args[1].decode("utf-8")
        payload = json.loads(uploaded)
        assert payload["models"]["providers"]["custom_openai"]["baseUrl"] == "http://llm"
        assert payload["models"]["providers"]["embedding"]["baseUrl"] == "http://embed/v1"

    def test_prepare_runtime_files_disables_memory_without_embedding(self, tmp_path):
        import json

        from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager

        rock_cfg = tmp_path / "rock_agent_config.yaml"
        rock_cfg.write_text("name: test\n", encoding="utf-8")
        openclaw_cfg = tmp_path / "openclaw.json"
        openclaw_cfg.write_text("{}", encoding="utf-8")
        mgr = OpenClawRuntimeManager({
            "rock_agent_config_path": str(rock_cfg),
            "openclaw_config_path": str(openclaw_cfg),
        })
        sb = MagicMock()

        mgr._prepare_runtime_files(sb)

        uploaded = sb.upload.call_args.args[1].decode("utf-8")
        payload = json.loads(uploaded)
        assert payload["memory"]["enabled"] is False

    def test_static_template_disables_memory_by_default(self):
        payload = json.loads((Path(__file__).parent.parent / "openclaw_deploy" / "openclaw.json").read_text())
        assert payload["memory"]["enabled"] is False
