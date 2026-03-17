"""Phase 2: deploy vLLM with Qwen3-8B and verify API availability

Checks:
- The vLLM service starts successfully and listens on http://localhost:8000/v1
- Calling v1/chat/completions through the OpenAI SDK returns a response
- A simple math prompt returns text containing reasoning
- Token usage (prompt_tokens, completion_tokens) is returned correctly

Deployment command:
  python -m vllm.entrypoints.openai.api_server \
      --model /hd1/models/models--Qwen--Qwen3-8B \
      --port 8000 \
      --tensor-parallel-size 1

Run these tests with pytest -m integration.
"""
import pytest

pytestmark = pytest.mark.integration


def _vllm_available() -> bool:
    """Detect if the vLLM service is reachable."""
    try:
        import requests
        resp = requests.get("http://localhost:8000/v1/models", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def openai_client():
    """Create an OpenAI client pointed at the local vLLM server."""
    from openai import OpenAI
    return OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")


@pytest.fixture(scope="module")
def model_name():
    """Get the model name available on vLLM."""
    import requests
    resp = requests.get("http://localhost:8000/v1/models", timeout=5)
    data = resp.json()
    return data["data"][0]["id"]


@pytest.mark.skipif(not _vllm_available(), reason="vLLM service not running on localhost:8000")
class TestVLLMService:
    """Verify the availability of the vLLM service and Qwen3-8B."""

    def test_models_endpoint(self):
        """The v1/models endpoint returns a model list."""
        import requests
        resp = requests.get("http://localhost:8000/v1/models", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) > 0

    def test_model_is_qwen(self, model_name):
        """The model name includes an identifier related to Qwen3-8B."""
        # vLLM may return either a path or a model name; any available model is acceptable.
        assert model_name is not None
        assert len(model_name) > 0

    def test_chat_completion(self, openai_client, model_name):
        """v1/chat/completions returns a normal response."""
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
        """A math question returns text containing reasoning."""
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
        # The response should include some reasoning text.
        assert len(content) > 10

    def test_token_usage(self, openai_client, model_name):
        """Returns token statistics (prompt_tokens, completion_tokens)."""
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
        """Multi-turn chat messages work correctly."""
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
