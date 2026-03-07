"""ZeroClaw agent - minimal direct LLM prompting without an agent framework."""
from __future__ import annotations

import re
import time
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask

_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant that solves problems step by step. "
    "Put your final answer in \\boxed{}."
)


def _extract_answer(text: str) -> str:
    """Extract the answer from model output, preferring \\boxed{} content."""
    # Try \boxed{} extraction with nested brace handling
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

    # Fallback: last non-empty line
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return lines[-1] if lines else text.strip()


class ZeroClawAgent(Agent):
    """Minimal agent that directly calls an OpenAI-compatible LLM API."""

    name = "zeroclaw"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "unknown")
        self._model = config.get("model", "")
        self._api_base = config.get("api_base", "")
        self._api_key = config.get("api_key", "EMPTY")
        self._temperature = config.get("temperature", 0.0)
        self._max_tokens = config.get("max_tokens", 8192)
        self._system_prompt = config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "The 'openai' package is required for ZeroClawAgent. "
                "Install with: pip install openai"
            )

        start = time.time()

        client = OpenAI(base_url=self._api_base, api_key=self._api_key)
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


AgentRegistry.register("zeroclaw", ZeroClawAgent)
