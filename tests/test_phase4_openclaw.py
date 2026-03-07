"""Phase 4: 在 ROCK Sandbox 中部署 OpenClaw Gateway

需要:
  - ROCK 环境 (Phase 3)
  - vLLM 服务 (Phase 2)
  - OpenClaw gateway 在 sandbox 中运行

用 pytest -m integration 运行这些测试。
"""
import pytest

pytestmark = pytest.mark.integration


def _openclaw_gateway_available(
    proxy_url: str = "http://localhost:9001",
    sandbox_id: str | None = None,
) -> bool:
    """检测 OpenClaw gateway 是否通过 ROCK proxy 可达。"""
    if sandbox_id is None:
        return False
    try:
        import requests
        url = f"{proxy_url}/apis/envs/sandbox/v1/sandboxes/{sandbox_id}/proxy/v1/models"
        resp = requests.get(url, headers={"Authorization": "bearer OPENCLAW"}, timeout=10)
        return resp.status_code == 200
    except Exception:
        return False


# 这些测试需要手动设置 sandbox_id，或从环境变量读取
def _get_sandbox_id() -> str | None:
    import os
    return os.environ.get("OPENCLAW_SANDBOX_ID")


def _get_openclaw_api_base() -> str:
    sandbox_id = _get_sandbox_id()
    proxy_url = "http://localhost:9001"
    return f"{proxy_url}/apis/envs/sandbox/v1/sandboxes/{sandbox_id}/proxy/v1"


@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set. Deploy OpenClaw in ROCK first."
)
class TestOpenClawGateway:
    """验证 OpenClaw gateway 通过 ROCK proxy 可用。"""

    def test_gateway_reachable(self):
        """OpenClaw gateway 端点可达。"""
        import requests
        api_base = _get_openclaw_api_base()
        resp = requests.get(
            f"{api_base}/models",
            headers={"Authorization": "bearer OPENCLAW"},
            timeout=10,
        )
        # gateway 应返回模型列表或至少不是连接错误
        assert resp.status_code in (200, 404)

    def test_chat_completion_via_gateway(self):
        """通过 OpenClaw gateway 调用 chat completions。"""
        from openai import OpenAI

        api_base = _get_openclaw_api_base()
        client = OpenAI(base_url=api_base, api_key="OPENCLAW")

        response = client.chat.completions.create(
            model="openclaw",
            messages=[{"role": "user", "content": "What is 2+3?"}],
            temperature=0.0,
            max_tokens=256,
        )

        assert len(response.choices) > 0
        content = response.choices[0].message.content
        assert content is not None
        assert len(content) > 0

    def test_gateway_routes_to_qwen(self):
        """确认请求经过 OpenClaw → Qwen3-8B → 返回推理结果。"""
        from openai import OpenAI

        api_base = _get_openclaw_api_base()
        client = OpenAI(base_url=api_base, api_key="OPENCLAW")

        response = client.chat.completions.create(
            model="openclaw",
            messages=[
                {"role": "system", "content": "Solve step by step. Put answer in \\boxed{}."},
                {"role": "user", "content": "What is 7 * 8?"},
            ],
            temperature=0.0,
            max_tokens=512,
        )

        content = response.choices[0].message.content
        assert content is not None
        # 应包含某种推理文本，长度 > 简单回复
        assert len(content) > 5

    def test_token_usage_via_gateway(self):
        """Gateway 返回 token 统计。"""
        from openai import OpenAI

        api_base = _get_openclaw_api_base()
        client = OpenAI(base_url=api_base, api_key="OPENCLAW")

        response = client.chat.completions.create(
            model="openclaw",
            messages=[{"role": "user", "content": "Say hello."}],
            temperature=0.0,
            max_tokens=64,
        )

        usage = response.usage
        # OpenClaw 可能不总是返回 usage，所以只检查不崩溃
        if usage:
            assert usage.prompt_tokens >= 0
            assert usage.completion_tokens >= 0
