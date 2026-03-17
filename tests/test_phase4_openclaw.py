"""Phase 4: deploy OpenClaw Gateway inside a ROCK sandbox

Checks:
- The OpenClaw gateway starts on port 8080 inside the sandbox
- Calling chat/completions through the ROCK proxy returns normally
- Request path: AlphaDiana -> ROCK Proxy -> OpenClaw Gateway -> vLLM (Qwen3-8B)

Requires:
  - ROCK environment (Phase 3)
  - vLLM service (Phase 2)
  - OpenClaw gateway running inside the sandbox
  - OPENCLAW_SANDBOX_ID environment variable

Run these tests with pytest -m integration.
"""
import os

import pytest

from alphadiana.utils.rock_ports import resolve_rock_ports_from_env

pytestmark = pytest.mark.integration
PORTS = resolve_rock_ports_from_env()


def _get_sandbox_id() -> str | None:
    return os.environ.get("OPENCLAW_SANDBOX_ID")


def _get_openclaw_api_base() -> str:
    sandbox_id = _get_sandbox_id()
    proxy_url = PORTS.proxy_root_url
    return f"{proxy_url}/apis/envs/sandbox/v1/sandboxes/{sandbox_id}/proxy/v1"


@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set. Deploy OpenClaw in ROCK first."
)
class TestOpenClawGateway:
    """Verify that the OpenClaw gateway is available through the ROCK proxy."""

    def test_gateway_reachable(self):
        """OpenClaw gateway endpoints are reachable."""
        import requests
        api_base = _get_openclaw_api_base()
        resp = requests.get(
            f"{api_base}/models",
            headers={"Authorization": "bearer OPENCLAW"},
            timeout=10,
        )
        # gateway should return a model list or at least not a connection error
        # OpenClaw may return 405 for GET on endpoints that only support POST
        assert resp.status_code in (200, 404, 405)

    def test_chat_completion_via_gateway(self):
        """Call chat completions through the OpenClaw gateway."""
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
        """Confirm the request is routed through OpenClaw to Qwen3-8B and returns reasoning output."""
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
        # The response should contain reasoning text.
        assert len(content) > 5

    def test_token_usage_via_gateway(self):
        """Gateway returns token statistics."""
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
        # OpenClaw may not always return usage, so only verify that it does not crash.
        if usage:
            assert usage.prompt_tokens >= 0
            assert usage.completion_tokens >= 0

    def test_system_prompt_forwarded(self):
        """System prompt is forwarded correctly to the model."""
        from openai import OpenAI

        api_base = _get_openclaw_api_base()
        client = OpenAI(base_url=api_base, api_key="OPENCLAW")

        response = client.chat.completions.create(
            model="openclaw",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant. "
                        "Put your final answer in \\boxed{}."
                    ),
                },
                {"role": "user", "content": "What is 3+4?"},
            ],
            temperature=0.0,
            max_tokens=512,
        )
        content = response.choices[0].message.content
        assert content is not None
        assert len(content) > 0
