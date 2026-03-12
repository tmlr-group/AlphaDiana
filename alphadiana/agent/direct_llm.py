"""Direct LLM baseline - single-turn prompting without an agent framework.

This agent calls an OpenAI-compatible API directly with a system prompt
and the user's problem, returning the model's raw output.  It serves as
a baseline to measure the LLM's own reasoning ability without any
agentic orchestration (no tool calling, no multi-turn, no code execution).
"""
from __future__ import annotations

import time
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.math_answer import extract_answer_candidate

_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant that solves problems step by step. "
    "Put your final answer in \\boxed{}."
)


def _extract_answer(text: str) -> str:
    """Extract the answer from model output, preferring \\boxed{} content."""
    return extract_answer_candidate(text)


class DirectLLMAgent(Agent):
    """Baseline agent that directly calls an OpenAI-compatible LLM API.

    Single-turn prompting: system prompt + user problem -> model response.
    No tool calling, no multi-turn reasoning, no agent framework.
    """

    name = "direct_llm"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "unknown")
        self._model = config.get("model", "")
        self._api_base = config.get("api_base", "")
        self._api_key = config.get("api_key", "EMPTY")
        self._temperature = config.get("temperature", 0.7)
        self._top_p = config.get("top_p", None)
        self._max_tokens = config.get("max_tokens", None)
        self._resolved_max_tokens: int | None = None
        self._system_prompt = config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)

    def _resolve_max_tokens(self) -> int:
        """Resolve max_tokens by querying the model endpoint or using a fallback."""
        if self._resolved_max_tokens is not None:
            return self._resolved_max_tokens
        if self._max_tokens is not None:
            self._resolved_max_tokens = int(self._max_tokens)
            return self._resolved_max_tokens

        try:
            import httpx

            api_base = self._api_base.rstrip("/")
            response = httpx.get(f"{api_base}/models", timeout=5.0)
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    max_len = data[0].get("max_model_len")
                    if isinstance(max_len, int) and max_len > 0:
                        self._resolved_max_tokens = max_len
                        return max_len
        except Exception:
            pass

        self._resolved_max_tokens = 32768
        return self._resolved_max_tokens

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "The 'openai' package is required for DirectLLMAgent. "
                "Install with: pip install openai"
            )

        start = time.time()

        client = OpenAI(base_url=self._api_base, api_key=self._api_key)
        request_kwargs = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": task.problem},
            ],
            "temperature": self._temperature,
            "max_tokens": self._resolve_max_tokens(),
        }
        if self._top_p is not None:
            request_kwargs["top_p"] = self._top_p
        response = client.chat.completions.create(
            **request_kwargs,
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


AgentRegistry.register("direct_llm", DirectLLMAgent)
