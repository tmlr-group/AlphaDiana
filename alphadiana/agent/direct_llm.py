"""Direct LLM baseline - single-turn prompting without an agent framework.

This agent calls an OpenAI-compatible API directly with a system prompt
and the user's problem, returning the model's raw output.  It serves as
a baseline to measure the LLM's own reasoning ability without any
agentic orchestration (no tool calling, no multi-turn, no code execution).
"""
from __future__ import annotations

import base64
import logging
import os
import random
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

from alphadiana.agent._logprobs import INT16_PROB_SCALE, quantize_records_int16
from alphadiana.agent.logprob_capture import (
    apply_openai_logprob_request,
    extract_openai_logprob_records,
    finalize_logprob_capture,
    resolve_logprob_capture_config,
)
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


_THINK_TAG_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


def _extract_reasoning_from_model_extra(obj: object) -> str:
    """Extract reasoning content from an OpenAI SDK object's model_extra.

    Different APIs use different field names:
      - Volcengine Kimi: model_extra["reasoning_content"]
      - OpenRouter Kimi: model_extra["reasoning"]
    """
    extra = getattr(obj, "model_extra", None)
    if not extra or not isinstance(extra, dict):
        return ""
    for key in ("reasoning_content", "reasoning"):
        val = extra.get(key)
        if val and isinstance(val, str):
            return val
    return ""


def _split_think_tags(content: str) -> tuple[str, str]:
    """Split <think>...</think> tags from content (Qwen3/vLLM pattern).

    Returns (reasoning, cleaned_content).
    """
    parts = _THINK_TAG_RE.findall(content)
    if not parts:
        return "", content
    reasoning = "\n".join(parts)
    cleaned = _THINK_TAG_RE.sub("", content).strip()
    return reasoning, cleaned


class DirectLLMAgent(Agent):
    """Baseline agent that directly calls an OpenAI-compatible LLM API.

    Single-turn prompting: system prompt + user problem -> model response.
    No tool calling, no multi-turn reasoning, no agent framework.
    """

    name = "direct_llm"
    # Logprobs capture — defaults to on (int16); disable per-run by setting
    # `capture_logprobs: false` in yaml if the backend rejects `logprobs=true`.
    _capture_logprobs: bool = True
    _top_logprobs: int = 20
    _logprobs_format: str = "int16"

    def setup(self, config: dict) -> None:
        self._model = self._resolve_setting(config, "model", "OPENAI_MODEL_NAME")
        self._api_base = self._resolve_setting(config, "api_base", "OPENAI_BASE_URL")
        self._api_key = self._resolve_setting(config, "api_key", "OPENAI_API_KEY", default="EMPTY")
        self._temperature = config.get("temperature", 0.7)
        self._top_p = config.get("top_p", None)
        self._max_tokens = config.get("max_tokens", None)
        self._max_completion_tokens = config.get("max_completion_tokens", None)
        self._max_retries = int(config.get("max_retries", 3))
        self._stream = config.get("stream", True)
        self._resolved_max_tokens: int | None = None
        self._system_prompt = config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)
        self._enable_thinking = config.get("enable_thinking", None)
        self._extra_body: dict[str, Any] | None = config.get("extra_body", None)
        capture_config = dict(config)
        # DirectLLM defaults to enabled capture for backward compatibility.
        capture_config.setdefault("capture_logprobs", True)
        self._logprob_capture = resolve_logprob_capture_config(capture_config)
        self._capture_logprobs = self._logprob_capture["enabled"]
        self._top_logprobs = self._logprob_capture["top_logprobs"]
        fmt = str(config.get("logprobs_format", "int16")).lower()
        if fmt not in ("float", "int16"):
            raise ValueError(f"logprobs_format must be 'float' or 'int16', got {fmt!r}")
        self._logprobs_format = fmt
        try:
            self._client = self._build_client()
        except ImportError:
            self._client = None

    def _build_client(self):
        """Build an OpenAI client that ignores inherited proxy env vars.

        The user environment may export SOCKS/HTTP proxy variables for other
        tools. Direct LLM calls should not depend on those settings, because
        `httpx` can fail to initialize proxy transports if optional extras like
        `socksio` are unavailable.
        """
        import httpx
        from openai import OpenAI

        return OpenAI(
            base_url=self._api_base,
            api_key=self._api_key,
            http_client=httpx.Client(trust_env=False),
        )

    @staticmethod
    def _resolve_setting(
        config: dict,
        key: str,
        env_var: str,
        *,
        default: str = "",
    ) -> str:
        value = config.get(key, default)
        if value is None:
            value = default
        if isinstance(value, str):
            stripped = value.strip()
            if stripped and stripped.upper() != "EMPTY":
                return stripped
        env_value = os.environ.get(env_var, "").strip()
        if env_value:
            return env_value
        return value if isinstance(value, str) else default

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
            response = httpx.get(f"{api_base}/models", timeout=5.0, trust_env=False)
            if response.status_code == 200:
                data = response.json().get("data", [])
                if data:
                    max_len = data[0].get("max_model_len")
                    if isinstance(max_len, int) and max_len > 0:
                        self._max_model_len = max_len
                        self._resolved_max_tokens = max_len
                        return max_len
        except Exception:
            pass

        self._resolved_max_tokens = 65536
        return self._resolved_max_tokens

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        if self._client is None:
            try:
                self._client = self._build_client()
            except ImportError:
                raise RuntimeError(
                    "The 'openai' package is required for DirectLLMAgent. "
                    "Install with: pip install openai"
                )

        start = time.time()

        user_content: Any = task.problem
        if task.attachments:
            parts: list[dict[str, Any]] = [{"type": "text", "text": task.problem}]
            img_keys = sorted(k for k in task.attachments if not k.endswith("_mime"))
            for key in img_keys:
                img_bytes = task.attachments[key]
                mime_key = f"{key}_mime"
                mime = task.attachments.get(mime_key, b"image/png").decode()
                b64 = base64.b64encode(img_bytes).decode()
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            user_content = parts

        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_content},
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

        # Build extra_body for provider-specific params (e.g. enable_thinking for z-ai/GLM).
        extra_body: dict[str, Any] = {}
        if self._enable_thinking is not None:
            extra_body["enable_thinking"] = bool(self._enable_thinking)
        if self._extra_body and isinstance(self._extra_body, dict):
            extra_body.update(self._extra_body)
        if extra_body:
            request_kwargs["extra_body"] = extra_body

        apply_openai_logprob_request(request_kwargs, self._logprob_capture)

        # Retry loop with exponential backoff for transient errors.
        last_exc: Exception | None = None
        raw_output = ""
        raw_reasoning = ""
        finish_reason = ""
        token_usage: dict = {}
        logprob_records: list[dict] = []

        for attempt in range(self._max_retries + 1):
            try:
                if self._stream:
                    raw_output, finish_reason, token_usage, raw_reasoning, logprob_records = (
                        self._call_streaming(request_kwargs)
                    )
                else:
                    raw_output, finish_reason, token_usage, raw_reasoning, logprob_records = (
                        self._call_non_streaming(request_kwargs)
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
        # Only compute entropy when capture was explicitly enabled; discard any
        # records that leaked through (e.g. test stubs always returning logprobs).
        if not self._capture_logprobs:
            logprob_records = []
        token_entropy_stats, response_metadata = finalize_logprob_capture(
            harness="direct_llm",
            enabled=self._logprob_capture["enabled"],
            records=logprob_records,
            metadata={},
        )
        if self._capture_logprobs and self._logprobs_format == "int16" and logprob_records:
            response_metadata["logprob_records"] = quantize_records_int16(
                logprob_records, top_k=self._top_logprobs or 20
            )

        # Extract answer: try raw_output first, fall back to reasoning
        answer = _extract_answer(raw_output) if raw_output else ""
        if not answer and raw_reasoning:
            answer = _extract_answer(raw_reasoning)

        # Persist a normalized response envelope even when the provider does
        # not expose separate reasoning fields. This keeps non-thinking models
        # from collapsing to response_json={} in saved task records.
        response_message: dict[str, Any] = {
            "role": "assistant",
            "content": raw_output,
        }
        if raw_reasoning:
            response_message["reasoning_content"] = raw_reasoning
        response_json: dict[str, Any] = {
            "choices": [{
                "message": response_message,
                "finish_reason": finish_reason,
            }]
        }
        if token_usage:
            response_json["usage"] = token_usage

        reasoning_trajectory: list[dict] = []
        if raw_reasoning:
            reasoning_trajectory = [
                {"role": "assistant", "reasoning_content": raw_reasoning},
            ]

        # Build trajectory; include "thinking" in assistant message to match openclaw format
        assistant_msg: dict[str, str] = {"role": "assistant", "content": raw_output}
        if raw_reasoning:
            assistant_msg["thinking"] = raw_reasoning

        return AgentResponse(
            answer=answer,
            trajectory=[
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": task.problem},
                assistant_msg,
            ],
            raw_output=raw_output,
            token_usage=token_usage,
            wall_time_sec=wall_time,
            token_entropy_stats=token_entropy_stats,
            metadata=response_metadata,
            system_prompt=self._system_prompt,
            request_messages=messages,
            finish_reason=finish_reason,
            response_json=response_json,
            reasoning_trajectory=reasoning_trajectory,
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """Return True for transient errors worth retrying.

        Covers SDK-typed errors, HTTP 5xx, and streaming mid-flight
        disconnections (e.g. ``Network connection lost``,
        ``incomplete chunked read``).
        """
        try:
            from openai import RateLimitError, APITimeoutError, APIConnectionError, APIStatusError, APIError
            if isinstance(exc, (RateLimitError, APITimeoutError, APIConnectionError)):
                return True
            if isinstance(exc, APIStatusError) and exc.status_code >= 500:
                return True
            # APIError covers generic failures like "Network connection lost"
            # that happen during streaming reads.
            if isinstance(exc, APIError):
                return True
        except ImportError:
            pass

        # httpx / httpcore transport errors (streaming disconnections).
        try:
            import httpx
            if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError,
                                httpx.ReadTimeout, httpx.ConnectError,
                                httpx.ConnectTimeout)):
                return True
        except ImportError:
            pass

        try:
            import httpcore
            if isinstance(exc, (httpcore.RemoteProtocolError, httpcore.ReadError)):
                return True
        except ImportError:
            pass

        msg = str(exc).lower()
        _RETRYABLE_KEYWORDS = (
            "timeout", "rate", "429", "502", "503",
            "network connection lost", "incomplete chunked read",
            "peer closed connection", "remoteprotocolerror",
            "response payload is not completed",
        )
        return any(kw in msg for kw in _RETRYABLE_KEYWORDS)

    _stream_options_supported: bool = True

    def _call_streaming(self, request_kwargs: dict) -> tuple[str, str, dict, str, list[dict]]:
        """Call the API in streaming mode, returning (raw_output, finish_reason, token_usage, raw_reasoning, logprob_records)."""
        kwargs = {**request_kwargs, "stream": True}
        if self._stream_options_supported:
            kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Fallback: some APIs don't support stream_options.
            if self._stream_options_supported and (
                "stream_options" in str(exc).lower()
                or getattr(exc, "status_code", 0) == 400
            ):
                logger.info("stream_options not supported by API, retrying without it")
                self._stream_options_supported = False
                kwargs.pop("stream_options", None)
                stream = self._client.chat.completions.create(**kwargs)
            else:
                raise

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        finish_reason = ""
        token_usage: dict = {}
        logprob_records: list[dict] = []
        token_index = 0
        for chunk in stream:
            if chunk.choices:
                delta = chunk.choices[0].delta
                if delta:
                    if delta.content:
                        content_parts.append(delta.content)
                    # Collect reasoning from model_extra (Kimi/OpenRouter/Volcengine)
                    rc = _extract_reasoning_from_model_extra(delta)
                    if rc:
                        reasoning_parts.append(rc)
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                chunk_logprobs = getattr(chunk.choices[0], "logprobs", None)
                if chunk_logprobs is not None and getattr(chunk_logprobs, "content", None):
                    chunk_records, token_index = extract_openai_logprob_records(
                        {"choices": [{"logprobs": {"content": chunk_logprobs.content}}]},
                        start_index=token_index,
                    )
                    logprob_records.extend(chunk_records)
            if hasattr(chunk, "usage") and chunk.usage:
                token_usage = {
                    "prompt_tokens": chunk.usage.prompt_tokens or 0,
                    "completion_tokens": chunk.usage.completion_tokens or 0,
                    "total_tokens": chunk.usage.total_tokens or 0,
                }

        raw_content = "".join(content_parts)
        raw_reasoning = "".join(reasoning_parts)

        # Handle <think> tags embedded in content (Qwen3/vLLM pattern)
        if not raw_reasoning and "<think>" in raw_content:
            tag_reasoning, raw_content = _split_think_tags(raw_content)
            if tag_reasoning:
                raw_reasoning = tag_reasoning

        return raw_content, finish_reason, token_usage, raw_reasoning, logprob_records

    def _call_non_streaming(self, request_kwargs: dict) -> tuple[str, str, dict, str, list[dict]]:
        """Call the API in non-streaming mode, returning (raw_output, finish_reason, token_usage, raw_reasoning, logprob_records)."""
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
        raw_reasoning = _extract_reasoning_from_model_extra(choice.message)
        # Handle <think> tags embedded in content (Qwen3/vLLM pattern)
        if not raw_reasoning and "<think>" in raw_output:
            raw_reasoning, raw_output = _split_think_tags(raw_output)
        logprob_records, _ = extract_openai_logprob_records(response, start_index=0)
        return raw_output, finish_reason, token_usage, raw_reasoning, logprob_records

    def teardown(self) -> None:
        pass


AgentRegistry.register("direct_llm", DirectLLMAgent)
