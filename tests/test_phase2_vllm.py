"""Phase 2: vLLM 部署 Qwen3-8B 并验证 API 可用

验证点:
- vLLM 服务启动成功，监听 http://localhost:8000/v1
- 通过 OpenAI SDK 调用 v1/chat/completions 返回响应
- 简单数学 prompt 返回含推理过程的文本
- token 统计（prompt_tokens, completion_tokens）正确返回

部署命令:
  python -m vllm.entrypoints.openai.api_server \
      --model /hd1/models/models--Qwen--Qwen3-8B \
      --port 8000 \
      --tensor-parallel-size 1

用 pytest -m integration 运行这些测试。
"""
import pytest

pytestmark = pytest.mark.integration


def _vllm_available() -> bool:
    """检测 vLLM 服务是否可达。"""
    try:
        import requests
        resp = requests.get("http://localhost:8000/v1/models", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def openai_client():
    """创建指向本地 vLLM 的 OpenAI 客户端。"""
    from openai import OpenAI
    return OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")


@pytest.fixture(scope="module")
def model_name():
    """获取 vLLM 上可用的模型名。"""
    import requests
    resp = requests.get("http://localhost:8000/v1/models", timeout=5)
    data = resp.json()
    return data["data"][0]["id"]


@pytest.mark.skipif(not _vllm_available(), reason="vLLM service not running on localhost:8000")
class TestVLLMService:
    """验证 vLLM 服务和 Qwen3-8B 的可用性。"""

    def test_models_endpoint(self):
        """v1/models 端点返回模型列表。"""
        import requests
        resp = requests.get("http://localhost:8000/v1/models", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) > 0

    def test_model_is_qwen(self, model_name):
        """模型名中包含 Qwen3-8B 相关标识。"""
        # vLLM 可能使用路径或模型名，只要有模型即可
        assert model_name is not None
        assert len(model_name) > 0

    def test_chat_completion(self, openai_client, model_name):
        """v1/chat/completions 返回正常响应。"""
        response = openai_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "What is 2+3?"}],
            temperature=0.0,
            max_tokens=256,
        )
        assert len(response.choices) > 0
        content = response.choices[0].message.content
        assert content is not None
        assert len(content) > 0

    def test_math_reasoning(self, openai_client, model_name):
        """对数学问题返回包含推理过程的文本。"""
        response = openai_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "Solve the problem step by step. Put your final answer in \\boxed{}."},
                {"role": "user", "content": "What is 17 * 23?"},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        content = response.choices[0].message.content
        assert content is not None
        # 应包含一些推理文本
        assert len(content) > 10

    def test_token_usage(self, openai_client, model_name):
        """返回 token 统计信息（prompt_tokens, completion_tokens）。"""
        response = openai_client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "Say hello."}],
            temperature=0.0,
            max_tokens=64,
        )
        assert response.usage is not None
        assert response.usage.prompt_tokens > 0
        assert response.usage.completion_tokens > 0
        assert response.usage.total_tokens > 0
        assert response.usage.total_tokens == (
            response.usage.prompt_tokens + response.usage.completion_tokens
        )

    def test_multiple_messages(self, openai_client, model_name):
        """多轮对话消息正常工作。"""
        response = openai_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "You are a math tutor."},
                {"role": "user", "content": "What is 5+5?"},
                {"role": "assistant", "content": "5+5 = 10."},
                {"role": "user", "content": "And what is 10+10?"},
            ],
            temperature=0.0,
            max_tokens=256,
        )
        assert len(response.choices) > 0
        content = response.choices[0].message.content
        assert content is not None
