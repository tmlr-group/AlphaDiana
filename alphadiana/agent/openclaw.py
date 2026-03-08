"""OpenClaw agent wrapper.

OpenClaw runs inside a ROCK sandbox and exposes an OpenAI-compatible
v1/chat/completions endpoint via its gateway. This agent calls that
endpoint to solve benchmark tasks.

Deployment reference:
  https://github.com/alibaba/ROCK/blob/master/examples/agents/openclaw/REAMDE.md

Prerequisites:
  1. Start ROCK admin + proxy servers
  2. Launch a sandbox with OpenClaw gateway installed (npm install -g openclaw@latest)
  3. Configure openclaw.json with model provider (OPENAI_BASE_URL, OPENAI_API_KEY, etc.)
  4. The gateway listens on port 8080 inside the sandbox, proxied via ROCK proxy
"""
from __future__ import annotations

import re
import time
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask


def _extract_answer(text: str) -> str:
    """Extract the answer from model output, preferring \\boxed{} content."""
    idx = text.rfind("\\boxed{")
    if idx != -1:
        start = idx + len("\\boxed{")
        depth = 1
        pos = start
        while pos < len(text) and depth > 0:
            if text[pos] == "{":
                depth += 1
            elif text[pos] == "}":
                depth -= 1
            pos += 1
        if depth == 0:
            return text[start:pos - 1].strip()

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return lines[-1] if lines else text.strip()


class OpenClawAgent(Agent):
    """Agent that talks to an OpenClaw gateway via OpenAI-compatible API.

    OpenClaw is deployed inside a ROCK sandbox:
      1. ROCK sandbox runs the openclaw gateway on port 8080
      2. ROCK proxy exposes it at:
         http://<proxy>:<port>/apis/envs/sandbox/v1/sandboxes/<sandbox_id>/proxy/v1/chat/completions
      3. This agent sends chat completion requests to that endpoint

    Required config keys:
      - api_base: Full URL to the OpenClaw gateway endpoint
                  (e.g. "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<id>/proxy/v1")
      - model: Model name as configured in openclaw.json (default: "openclaw")
      - gateway_token: OpenClaw gateway auth token (default: "OPENCLAW")
    """

    name = "openclaw"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "unknown")
        self._api_base = config.get("api_base", "")
        self._model = config.get("model", "openclaw")
        self._gateway_token = config.get("gateway_token", "OPENCLAW")
        self._temperature = config.get("temperature", 0.0)
        self._max_tokens = config.get("max_tokens", 8192)
        self._system_prompt = config.get(
            "system_prompt",
            "You are a helpful assistant that solves problems step by step. "
            "Put your final answer in \\boxed{}.",
        )
        self._config = config

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "The 'openai' package is required for OpenClawAgent. "
                "Install with: pip install openai"
            )

        start = time.time()

        client = OpenAI(
            base_url=self._api_base,
            api_key=self._gateway_token,
        )

        response = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": task.problem},
            ],
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        wall_time = time.time() - start

        if not response.choices:
            raw_output = ""
        else:
            raw_output = response.choices[0].message.content or ""
        answer = _extract_answer(raw_output)

        usage = response.usage
        token_usage = {}
        if usage:
            token_usage = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }

        return AgentResponse(
            answer=answer,
            trajectory=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": task.problem},
                {"role": "assistant", "content": raw_output},
            ],
            raw_output=raw_output,
            token_usage=token_usage,
            wall_time_sec=wall_time,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("openclaw", OpenClawAgent)
