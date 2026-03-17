"""Direct LLM baseline - single-turn prompting without an agent framework.

This agent calls an OpenAI-compatible API directly with a system prompt
and the user's problem, returning the model's raw output.  It serves as
a baseline to measure the LLM's own reasoning ability without any
agentic orchestration (no tool calling, no multi-turn, no code execution).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

logger = logging.getLogger(__name__)

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
        self._model = config.get("model", "")
        self._api_base = config.get("api_base", "")
        self._api_key = config.get("api_key", "EMPTY")
        self._temperature = config.get("temperature", 0.7)
        self._top_p = config.get("top_p", None)
        self._max_tokens = config.get("max_tokens", None)
        self._max_completion_tokens = config.get("max_completion_tokens", None)
        self._max_retries = int(config.get("max_retries", 3))
        self._stream = config.get("stream", True)
        self._resolved_max_tokens: int | None = None
        self._system_prompt = config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)
        try:
            from openai import OpenAI
            self._client = OpenAI(base_url=self._api_base, api_key=self._api_key)
        except ImportError:
            self._client = None

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
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(base_url=self._api_base, api_key=self._api_key)
            except ImportError:
                raise RuntimeError(
                    "The 'openai' package is required for DirectLLMAgent. "
                    "Install with: pip install openai"
                )

        start = time.time()

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": task.problem},
        ]
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
        }
        if self._max_completion_tokens is not None:
            request_kwargs["max_completion_tokens"] = self._max_completion_tokens
        else:
            request_kwargs["max_tokens"] = self._resolve_max_tokens()
        if self._top_p is not None:
            request_kwargs["top_p"] = self._top_p

        # Retry loop with exponential backoff for transient errors.
        last_exc: Exception | None = None
        raw_output = ""
        finish_reason = ""
        token_usage: dict = {}

        for attempt in range(self._max_retries + 1):
            try:
                if self._stream:
                    raw_output, finish_reason, token_usage = self._call_streaming(
                        request_kwargs
                    )
                else:
                    raw_output, finish_reason, token_usage = self._call_non_streaming(
                        request_kwargs
                    )
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                if not self._is_retryable(exc) or attempt == self._max_retries:
                    break
                delay = min(2.0 * (2 ** attempt), 60.0)
                jitter = random.uniform(0, delay * 0.3)
                logger.warning(
                    "DirectLLM attempt %d/%d failed: %s. Retrying in %.1fs",
                    attempt + 1, self._max_retries, exc, delay + jitter,
                )
                time.sleep(delay + jitter)

        if last_exc is not None:
            raise last_exc

        wall_time = time.time() - start
        answer = _extract_answer(raw_output)

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
            system_prompt=self._system_prompt,
            request_messages=messages,
            finish_reason=finish_reason,
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True for transient errors worth retrying."""
        try:
            from openai import RateLimitError, APITimeoutError, APIConnectionError, APIStatusError
            if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
                return True
            if isinstance(exc, APIStatusError) and exc.status_code >= 500:
                return True
        except ImportError:
            pass
        msg = str(exc).lower()
        return "timeout" in msg or "rate" in msg or "429" in msg or "502" in msg or "503" in msg

    def _call_streaming(self, request_kwargs: dict) -> tuple[str, str, dict]:
        """Call the API in streaming mode, returning (raw_output, finish_reason, token_usage)."""
        kwargs = {**request_kwargs, "stream": True, "stream_options": {"include_usage": True}}
        stream = self._client.chat.completions.create(**kwargs)

        content_parts: list[str] = []
        finish_reason = ""
        token_usage: dict = {}
        for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    content_parts.append(delta.content)
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
            if hasattr(chunk, "usage") and chunk.usage:
                token_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0,
                }

        return "".join(content_parts), finish_reason, token_usage

    def _call_non_streaming(self, request_kwargs: dict) -> tuple[str, str, dict]:
        """Call the API in non-streaming mode, returning (raw_output, finish_reason, token_usage)."""
        response = self._client.chat.completions.create(**request_kwargs)
        choice = response.choices[0]
        raw_output = choice.message.content or ""
        finish_reason = choice.finish_reason or ""
        token_usage: dict = {}
        if response.usage:
            token_usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        return raw_output, finish_reason, token_usage

    def teardown(self) -> None:
        pass


AgentRegistry.register("direct_llm", DirectLLMAgent)
