from __future__ import annotations

from alphadiana.agent.terminal_bench2_openclaw import TerminalBench2OpenClawAgent


class _FakeResponse:
    status_code = 200

    def json(self):
        return {
            "data": [
                {
                    "id": "qwen/qwen3.5-27b",
                    "canonical_slug": "qwen/qwen3.5-27b-20260224",
                    "hugging_face_id": "Qwen/Qwen3.5-27B",
                    "name": "Qwen: Qwen3.5-27B",
                    "context_length": 262144,
                    "top_provider": {"context_length": 262144},
                }
            ]
        }


def test_openclaw_context_probe_accepts_openrouter_context_length(monkeypatch) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "secret",
        "model_name": "Qwen/Qwen3.5-27B",
    })

    def _fake_get(*args, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr("httpx.get", _fake_get)

    resolved, source = agent._resolve_context_window()

    assert resolved == 262144
    assert source == "models_endpoint"


def test_openclaw_runtime_contract_patch_raises_inner_context_window(tmp_path) -> None:
    agent = TerminalBench2OpenClawAgent()
    agent.setup({
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": "secret",
        "model_name": "qwen/qwen3.5-27b",
        "min_context_window": 32768,
    })
    config_path = tmp_path / ".openclaw" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        """
{
  "models": {
    "providers": {
      "custom-openrouter-ai": {
        "models": [
          {
            "id": "qwen/qwen3.5-27b",
            "name": "qwen/qwen3.5-27b (Custom Provider)",
            "contextWindow": 16000,
            "maxTokens": 4096
          }
        ]
      }
    }
  }
}
""".strip(),
        encoding="utf-8",
    )

    patch_info = agent._patch_runtime_model_contract(
        config_path,
        resolved_context_window=262144,
    )

    updated = config_path.read_text(encoding="utf-8")
    assert patch_info["patched"] is True
    assert patch_info["matched"] is True
    assert '"contextWindow": 262144' in updated
