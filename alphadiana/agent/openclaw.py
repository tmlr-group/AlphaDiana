"""OpenClaw agent wrapper.

OpenClaw is an agentic framework that performs multi-round reasoning with
tool calling inside a ROCK sandbox.  When it receives a chat/completions
request it does NOT just proxy to the LLM; instead it:

  1. Builds a rich system prompt (~8K tokens) with agent instructions,
     skill definitions, workspace context (AGENTS.md, SOUL.md, etc.)
  2. Runs an embedded agentic loop (ReAct-style):
       user message → LLM call → tool calls → tool results → LLM call → ...
  3. Returns the final assistant message via the OpenAI-compatible response
  4. Stores the full internal trajectory in session JSONL files at
     /root/.openclaw/agents/main/sessions/<session_id>.jsonl

This wrapper supports two modes:
  - Direct gateway: ``api_base`` is pre-configured (gateway already running)
  - Auto-deploy: ``rock_agent_config_path`` + ``openclaw_config_path`` provided,
    the gateway is started inside a live ROCK sandbox automatically

Deployment reference:
  https://github.com/alibaba/ROCK/blob/master/examples/agents/openclaw/REAMDE.md
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import shlex
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.math_answer import extract_answer_candidate
from alphadiana.utils.rock_ports import resolve_rock_ports_from_env

logger = logging.getLogger(__name__)


class OpenClawRequestError(RuntimeError):
    """Request failure with attached diagnostics for result persistence."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        request_payload: dict | None = None,
        response_body: Any = None,
        retry_responses: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.request_payload = request_payload
        self.response_body = response_body
        self.retry_responses = retry_responses or []


class BackendDownError(OpenClawRequestError):
    """Raised when the model backend is detected as down (circuit breaker open).

    Triggered after ``backend_down_threshold`` consecutive empty-SSE-body
    responses, where HTTP 200 is returned but no content arrives and the SSE
    stream never sends ``[DONE]``.  This pattern strongly indicates the upstream
    model service (e.g. vLLM) has crashed or become unresponsive.

    Once raised, all subsequent ``solve()`` calls on this agent instance will
    immediately raise this error without making any HTTP requests (fail-fast).
    """

    def __init__(self, message: str, *, consecutive_failures: int, url: str) -> None:
        super().__init__(
            message,
            error_type="backend_down",
        )
        self.consecutive_failures = consecutive_failures
        self.url = url


def _extract_answer(text: str) -> str:
    r"""Extract the final answer from model output."""
    return extract_answer_candidate(text)


_PARTIAL_REASONING_ANSWER_RE = re.compile(
    r"(?:\*{0,2})(?:the\s+)?(?:final\s+)?answer(?:\*{0,2})\s*(?:[:：]|is|=)\s*(.+)",
    re.IGNORECASE,
)


def _extract_answer_from_partial_reasoning(text: str) -> str | None:
    """Extract an answer conservatively from incomplete reasoning.

    For partial reasoning, avoid treating arbitrary prose as the answer.
    Only accept:
      1. Explicit final-answer markers, e.g. ``the final answer is ...``
      2. ``\\boxed{...}``
      3. A short standalone math-like answer string (e.g. ``77``)
    """
    stripped = text.strip()
    if not stripped:
        return None

    candidate = _extract_answer(stripped).strip().rstrip(".")
    if not candidate:
        return None

    if r"\boxed{" in stripped:
        return candidate

    if _PARTIAL_REASONING_ANSWER_RE.search(stripped):
        return candidate

    if len(stripped) <= 50 and candidate == stripped and any(ch.isdigit() for ch in candidate):
        # Accept short direct answers such as "77" or "$77$",
        # but do not treat short prose snippets as answers.
        math_like = re.fullmatch(r"[\s$\\(){}\[\]\d+\-*/^.,=%]+", candidate)
        if math_like:
            return candidate

    return None


def extract_system_prompt(trajectory: list[dict]) -> str:
    """Extract system prompt from a trajectory list."""
    for entry in trajectory:
        if entry.get("role") == "system":
            return entry.get("content", "")
    # Fallback: check first assistant message for agent-like instructions
    for entry in trajectory:
        if entry.get("role") == "assistant":
            content = entry.get("content", "")
            if "You are" in content:
                return content
    return ""


# ---------------------------------------------------------------------------
# Session pollution detection
# ---------------------------------------------------------------------------

# Common math words that appear in almost any reasoning — not useful for
# distinguishing one problem from another.
_COMMON_MATH_WORDS = frozenset({
    "find", "each", "with", "where", "that", "this", "from", "have",
    "such", "then", "when", "which", "what", "there", "given", "since",
    "also", "define", "value", "values", "expressed", "possible", "least",
    "most", "positive", "negative", "integers", "integer", "number",
    "numbers", "prime", "primes", "square", "point", "points", "plane",
    "divisible", "total", "equal", "equals", "answer",
})

# Extract distinctive tokens: uppercase multi-letter names (ABCDE, AB),
# domain-specific words (≥5 chars, not common), numbers ≥3 digits.
_DISTINCTIVE_TOKEN_RE = re.compile(
    r"[A-Z]{2,}|\d{3,}|[a-zA-Z]{5,}"
)


def _detect_session_pollution(
    problem: str,
    raw_output: str,
    raw_reasoning: str,
) -> bool:
    """Heuristic check: did the model actually work on the given problem?

    Extracts distinctive tokens from *problem* — uppercase variable names
    (ABCDE, AB), domain-specific words (pentagon, quadrant), and large
    numbers — then checks how many appear in the model's reasoning/output.

    Returns True if pollution is suspected, False otherwise.
    """
    model_text = f"{raw_reasoning} {raw_output}".lower()
    if len(model_text.strip()) < 100:
        # Too short to judge reliably.
        return False

    raw_tokens = set(_DISTINCTIVE_TOKEN_RE.findall(problem))
    # Filter out common math words
    tokens = {t for t in raw_tokens if t.lower() not in _COMMON_MATH_WORDS}
    if len(tokens) < 3:
        # Problem doesn't have enough distinctive features to fingerprint.
        return False

    # Use word-boundary matching to avoid false positives from substrings
    # (e.g. "AB" matching "about", "DE" matching "determine").
    hits = 0
    for t in tokens:
        pattern = r"\b" + re.escape(t.lower()) + r"\b"
        if re.search(pattern, model_text):
            hits += 1
    ratio = hits / len(tokens)
    return ratio < 0.35


def classify_error(
    exc: Exception | None = None,
    *,
    response_json: dict | None = None,
    status_code: int | None = None,
) -> str:
    """Classify an error into a category for reporting."""
    if exc is not None:
        try:
            import httpx
            if isinstance(exc, httpx.TimeoutException):
                return "timeout"
        except ImportError:
            pass

    if isinstance(response_json, dict):
        msg = response_json.get("message", "")
        msg_lower = str(msg).lower()
        if "post proxy failed" in msg_lower:
            if "not started" in msg_lower or "not alive" in msg_lower:
                return "control_plane_unavailable"
            return "proxy_timeout"

        # Detect "internal error" from gateway (workspace contention, etc.)
        error_field = response_json.get("error")
        error_text = ""
        if isinstance(error_field, dict):
            error_text = str(error_field.get("message", "")).lower()
        elif isinstance(error_field, str):
            error_text = error_field.lower()
        if "internal error" in msg_lower or "internal error" in error_text:
            return "internal_error"

        choices = response_json.get("choices", [])
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            if isinstance(message, dict):
                content = message.get("content", "")
                if content == "" or content is None:
                    return "empty_response"

    if status_code is not None and status_code >= 500:
        return "gateway_error"

    return "unknown"


def _coerce_text_content(content: Any) -> str:
    """Coerce various content formats to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue
            if item.get("type") == "output_text":
                value = item.get("text") or item.get("content")
                if isinstance(value, str):
                    parts.append(value)
        return "".join(parts).strip()
    return ""


def _extract_raw_output_from_payload(payload: Any) -> str:
    """Extract assistant text from various gateway response formats."""
    if not isinstance(payload, dict):
        return ""

    # Standard OpenAI format
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        if isinstance(message, dict):
            content = _coerce_text_content(message.get("content"))
            if content:
                return content
        delta = choices[0].get("delta", {})
        if isinstance(delta, dict):
            content = _coerce_text_content(delta.get("content"))
            if content:
                return content
        text = choices[0].get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()

    # Gateway-specific variants
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = payload.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = _coerce_text_content(item.get("content"))
            if content:
                return content

    message = payload.get("message")
    if isinstance(message, dict):
        content = _coerce_text_content(message.get("content"))
        if content:
            return content

    return ""


def _extract_reasoning_trajectory_from_payload(payload: Any) -> list[dict]:
    """Best-effort extraction of a structured reasoning/tool trajectory."""
    if not isinstance(payload, dict):
        return []

    for key in (
        "trajectory",
        "reasoning_trajectory",
        "reasoning_trace",
        "trace",
        "steps",
        "events",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    output = payload.get("output")
    if isinstance(output, list):
        extracted = [item for item in output if isinstance(item, dict)]
        if extracted:
            return extracted

    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message")
        if isinstance(message, dict):
            tool_calls = message.get("tool_calls")
            if isinstance(tool_calls, list):
                return [item for item in tool_calls if isinstance(item, dict)]

    return []


def _recover_partial_output_from_trajectory(trajectory: list[dict]) -> tuple[str, str]:
    """Recover partial assistant output and reasoning from session trajectory."""
    if not isinstance(trajectory, list):
        return "", ""

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for entry in trajectory:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "assistant":
            continue
        content = entry.get("content", "")
        thinking = entry.get("thinking", "")
        content_text = _coerce_text_content(content)
        thinking_text = _coerce_text_content(thinking)
        if content_text:
            text_parts.append(content_text)
        if thinking_text:
            reasoning_parts.append(thinking_text)

    return "\n".join(text_parts).strip(), "\n".join(reasoning_parts).strip()


def _extract_trajectory_error(trajectory: list[dict]) -> str:
    """Return the latest explicit assistant error recorded in a session trajectory."""
    if not isinstance(trajectory, list):
        return ""

    for entry in reversed(trajectory):
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != "assistant":
            continue
        error = _coerce_text_content(entry.get("error", "")).strip()
        if error:
            return error
    return ""


def _normalize_request_content(content: str) -> str:
    """Normalize request text for transcript matching."""
    return content.replace("\r\n", "\n").strip()


def _trajectory_matches_request(
    trajectory: list[dict], expected_user_content: str,
) -> bool:
    """Return whether a transcript contains the current request message."""
    expected = _normalize_request_content(expected_user_content)
    if not expected:
        return False

    for entry in trajectory:
        if not isinstance(entry, dict) or entry.get("role") != "user":
            continue
        candidate = _normalize_request_content(_coerce_text_content(entry.get("content", "")))
        if candidate == expected:
            return True
    return False


def _parse_openclaw_session(session_jsonl: str) -> list[dict]:
    """Parse an OpenClaw session JSONL file into a trajectory list.

    OpenClaw stores each event as a JSONL line with a "type" field:
      - "session": session metadata (id, cwd, timestamp)
      - "model_change": model provider switch
      - "message": user/assistant message with content blocks:
          - "text": plain text output
          - "thinking": model's internal reasoning (chain-of-thought)
          - "toolCall": tool invocation (name + arguments)
          - "toolResult": tool execution result
          - "tool_use" / "tool_result": alternative names for the above
      - "custom": custom events (model-snapshot, etc.)
      - "thinking_level_change": thinking mode changes
    """
    trajectory = []
    for line in session_jsonl.strip().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "message":
            msg = event.get("message", {})
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                thinking_parts = []
                tool_calls = []
                tool_results = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    block_type = block.get("type", "")
                    if block_type == "text":
                        text_parts.append(block.get("text", ""))
                    elif block_type == "thinking":
                        thinking_parts.append(block.get("thinking", ""))
                    elif block_type in ("toolCall", "tool_use"):
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "tool": block.get("name", ""),
                            "input": block.get("arguments", block.get("input", {})),
                        })
                    elif block_type in ("toolResult", "tool_result"):
                        tc = block.get("content", "")
                        if isinstance(tc, list):
                            tc = "\n".join(
                                b.get("text", "")
                                for b in tc
                                if isinstance(b, dict)
                            )
                        tool_results.append({
                            "tool_use_id": block.get("tool_use_id", block.get("id", "")),
                            "content": tc,
                            "is_error": block.get("isError", block.get("is_error", False)),
                        })
                content_text = "\n".join(text_parts) if text_parts else ""
                entry = {"role": role, "content": content_text}
                if thinking_parts:
                    entry["thinking"] = "\n".join(thinking_parts)
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                if tool_results:
                    entry["tool_results"] = tool_results
            else:
                entry = {"role": role, "content": content}

            usage = msg.get("usage")
            if usage:
                entry["usage"] = usage
            stop_reason = msg.get("stopReason")
            if stop_reason:
                entry["stop_reason"] = stop_reason
            error = msg.get("errorMessage")
            if error:
                entry["error"] = error

            trajectory.append(entry)

        elif event_type == "tool_use":
            trajectory.append({
                "role": "tool_use",
                "tool": event.get("name", ""),
                "input": event.get("input", {}),
                "id": event.get("id", ""),
            })

        elif event_type == "tool_result":
            trajectory.append({
                "role": "tool_result",
                "tool_use_id": event.get("toolUseId", ""),
                "content": event.get("content", ""),
                "is_error": event.get("isError", False),
            })

    return trajectory


class OpenClawAgent(Agent):
    """Agent that talks to an OpenClaw gateway via OpenAI-compatible API.

    OpenClaw is deployed inside a ROCK sandbox and performs agentic
    orchestration internally (multi-turn LLM calls, tool use, context
    compaction).  This wrapper:

      1. Sends the user's problem to the OpenClaw gateway
      2. Receives the final consolidated response
      3. Retrieves the real agentic trajectory from the sandbox's
         session JSONL files (if sandbox access is available)

    Required config keys:
      - api_base: Full URL to the OpenClaw gateway endpoint
                  (e.g. "http://localhost:9001/apis/envs/sandbox/v1/sandboxes/<id>/proxy/v1")
      - model: Model name as configured in openclaw.json (default: "openclaw")
      - gateway_token: OpenClaw gateway auth token (default: "OPENCLAW")

    Optional config keys:
      - rock_sandbox_url: ROCK proxy base URL for retrieving session files
                          (e.g. "http://127.0.0.1:9001/apis/envs/sandbox/v1")
      - sandbox_id: ROCK sandbox ID (extracted from api_base if not set)
      - rock_agent_config_path: Path to ROCK agent config YAML (for auto-deploy)
      - openclaw_config_path: Path to OpenClaw gateway config JSON (for auto-deploy)
    """

    name = "openclaw"

    def setup(self, config: dict) -> None:
        self._api_base = config.get("api_base", "")
        self._model = config.get("model", "openclaw")
        self._gateway_token = config.get("gateway_token", "OPENCLAW")
        self._temperature = config.get("temperature", 0.7)
        self._top_p = config.get("top_p", None)
        self._max_tokens = config.get("max_tokens", None)
        self._max_attempts = max(1, int(config.get("max_attempts", 5)))
        self._request_timeout = float(config.get("request_timeout", 1800))
        self._proxy_timeout = int(config.get("proxy_timeout", 600))
        self._config = config

        # Agent.md customization
        self._agent_md_mode = config.get("agent_md_mode", "none")
        self._agent_md_content = config.get("agent_md_content", "")

        # Optional system prompt prepended to the user message
        self._user_system_prompt = config.get("system_prompt", "")

        # Cached resolved max_tokens
        self._resolved_max_tokens: int | None = None

        # Health check flag
        self._health_checked: bool = False

        # Circuit breaker: detect when the model backend has crashed.
        # After ``backend_down_threshold`` consecutive empty-body responses
        # (HTTP 200 + empty SSE, never receives [DONE]), the circuit opens and
        # all subsequent solve() calls fail immediately without making requests.
        self._backend_down_threshold: int = max(1, int(config.get("backend_down_threshold", 5)))
        self._consecutive_empty_sse: int = 0
        self._circuit_open: bool = False
        self._circuit_open_error: BackendDownError | None = None
        self._circuit_lock = threading.Lock()

        # Gateway pool for concurrent isolation (static multi-sandbox mode).
        # Users can set gateway_pool: [url1, url2, url3] in agent config to pre-deploy
        # multiple OpenClaw gateways and have each concurrent task use a different one.
        # This prevents workspace-state.json.tmp contention inside the gateway.
        gateway_pool_list: list[str] = config.get("gateway_pool", [])
        # Auto-build gateway_pool from SANDBOX_IDS env var (comma-separated).
        # Example: export SANDBOX_IDS=id1,id2,id3
        if not gateway_pool_list:
            sandbox_ids_env = os.environ.get("SANDBOX_IDS", "")
            sandbox_ids = [s.strip() for s in sandbox_ids_env.split(",") if s.strip()]
            if len(sandbox_ids) > 1:
                proxy_base = self._resolve_proxy_base()
                gateway_pool_list = [
                    f"{proxy_base}/sandboxes/{sid}/proxy/v1"
                    for sid in sandbox_ids
                ]
                logger.info(
                    "Auto-built gateway_pool from SANDBOX_IDS env: %d gateways",
                    len(gateway_pool_list),
                )
        # Also allow single api_base to be treated as a pool of one.
        if not gateway_pool_list and self._api_base:
            gateway_pool_list = [self._api_base]
        self._gateway_pool: deque[str] = deque(gateway_pool_list)
        self._gateway_pool_lock = threading.Lock()

        # For retrieving agentic trajectory from sandbox
        self._rock_sandbox_url = config.get("rock_sandbox_url", "")
        self._sandbox_id = config.get("sandbox_id", "")

        # Try to extract sandbox_id from api_base if not explicitly set
        if not self._sandbox_id and self._api_base:
            match = re.search(r"/sandboxes/([a-f0-9]+)/proxy", self._api_base)
            if match:
                self._sandbox_id = match.group(1)

        # Derive rock_sandbox_url from api_base if not set
        if not self._rock_sandbox_url and self._api_base:
            match = re.search(r"(https?://[^/]+)/apis/envs/sandbox/v1", self._api_base)
            if match:
                self._rock_sandbox_url = f"{match.group(1)}/apis/envs/sandbox/v1"

        # ROCK SDK client for reading session files (lazy init)
        self._sandbox_clients: dict[tuple[str, str], Any] = {}

        # Runtime manager for auto-deploy gateway startup
        try:
            from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager
            self._runtime_manager = OpenClawRuntimeManager(config)
        except ImportError:
            self._runtime_manager = None

    def _ensure_agent_md(self, sandbox: Any = None) -> None:
        if (
            sandbox is None
            or self._runtime_manager is None
            or self._agent_md_mode == "none"
            or not hasattr(self._runtime_manager, "inject_agent_md")
        ):
            return
        try:
            self._runtime_manager.inject_agent_md(sandbox)
        except Exception as exc:
            logger.warning("Failed to inject AGENTS.md customization: %s", exc)

    def _health_check(self, url: str, headers: dict) -> None:
        """One-time connectivity check on first solve() call.

        Some OpenClaw gateway configs intentionally do not expose ``/models``.
        In that case ``404``/``405`` still means the proxy path is live.
        """
        if self._health_checked:
            return
        self._health_checked = True
        import httpx
        base = url.rsplit("/chat/completions", 1)[0]
        try:
            resp = httpx.get(f"{base}/models", headers=headers, timeout=10.0)
            if resp.status_code not in (200, 404, 405):
                logger.warning(
                    "OpenClaw health check failed: GET %s/models returned %d. "
                    "The gateway may not be deployed correctly. Check: "
                    "1) sandbox is running, 2) openclaw deploy succeeded, "
                    "3) proxy port is correct.",
                    base, resp.status_code,
                )
        except Exception as exc:
            logger.warning(
                "OpenClaw health check failed: %s. "
                "Possible causes: sandbox not running, proxy port wrong, "
                "or OpenClaw gateway not deployed. Continuing anyway...",
                exc,
            )

    def _extract_sandbox_target_from_api_base(self, api_base: str) -> tuple[str, str]:
        """Extract sandbox_id and ROCK proxy base from a gateway api_base."""
        if not api_base:
            return "", ""
        sandbox_id = ""
        rock_sandbox_url = ""
        match = re.search(r"/sandboxes/([A-Za-z0-9_-]+)/proxy", api_base)
        if match:
            sandbox_id = match.group(1)
        match = re.search(r"(https?://[^/]+)/apis/envs/sandbox/v1", api_base)
        if match:
            rock_sandbox_url = f"{match.group(1)}/apis/envs/sandbox/v1"
        return sandbox_id, rock_sandbox_url

    def _get_sandbox_client(
        self,
        *,
        sandbox_id: str = "",
        rock_sandbox_url: str = "",
    ):
        """Lazily initialize a ROCK sandbox client for the requested sandbox."""
        sandbox_id = sandbox_id or self._sandbox_id
        rock_sandbox_url = rock_sandbox_url or self._rock_sandbox_url
        if not sandbox_id or not rock_sandbox_url:
            return None
        cache_key = (rock_sandbox_url, sandbox_id)
        cached = self._sandbox_clients.get(cache_key)
        if cached is not None:
            return cached

        try:
            from rock.sdk.sandbox.client import Sandbox
            from rock.sdk.sandbox.config import SandboxConfig

            admin_url = self._derive_admin_url(rock_sandbox_url=rock_sandbox_url)
            config = SandboxConfig(base_url=admin_url, image="python:3.11")
            sb = Sandbox(config)
            sb._sandbox_id = sandbox_id
            sb.url = rock_sandbox_url
            self._sandbox_clients[cache_key] = sb
            return sb
        except Exception as e:
            logger.warning("Failed to create ROCK sandbox client: %s", e)
            return None

    def _resolve_proxy_base(self) -> str:
        """Resolve the ROCK proxy base URL for building gateway_pool URLs."""
        if self._rock_sandbox_url:
            return self._rock_sandbox_url.rstrip("/")
        proxy_url = os.environ.get("ROCK_PROXY_URL", "")
        if proxy_url:
            return proxy_url.rstrip("/")
        return resolve_rock_ports_from_env().proxy_api_url.rstrip("/")

    def _derive_admin_url(self, rock_sandbox_url: str | None = None) -> str:
        rock_sandbox_url = rock_sandbox_url or self._rock_sandbox_url
        if not rock_sandbox_url:
            return resolve_rock_ports_from_env().base_url

        parsed = urlsplit(rock_sandbox_url)
        env_ports = resolve_rock_ports_from_env()
        hostname = parsed.hostname or "127.0.0.1"
        netloc = f"{hostname}:{env_ports.admin_port}"
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth = f"{auth}:{parsed.password}"
            netloc = f"{auth}@{netloc}"
        return urlunsplit((parsed.scheme or "http", netloc, "", "", ""))

    async def _retrieve_session_trajectory(
        self,
        *,
        sandbox_id: str = "",
        rock_sandbox_url: str = "",
        expected_user_content: str = "",
    ) -> list[dict]:
        """Retrieve the matching session JSONL from the sandbox."""
        sb = self._get_sandbox_client(
            sandbox_id=sandbox_id,
            rock_sandbox_url=rock_sandbox_url,
        )
        if sb is None:
            return []

        try:
            try:
                from rock.actions.sandbox.request import CreateBashSessionRequest
            except ImportError:
                CreateBashSessionRequest = None

            if CreateBashSessionRequest is not None:
                try:
                    await sb.create_session(
                        CreateBashSessionRequest(session="trajectory")
                    )
                except Exception:
                    pass  # Session may already exist

            session_globs = " ".join(
                f"{home}/agents/main/sessions/*.jsonl"
                for home in self._OPENCLAW_HOME_CANDIDATES
            )
            result = await sb.arun(
                f"ls -t {session_globs} 2>/dev/null | head -20",
                session="trajectory",
            )
            session_files = [
                line.strip()
                for line in result.output.strip().splitlines()
                if line.strip().endswith(".jsonl")
            ]
            if not session_files:
                return []

            fallback_trajectory: list[dict] = []
            for session_file in session_files:
                result = await sb.arun(
                    f"cat {shlex.quote(session_file)}",
                    session="trajectory",
                )
                trajectory = _parse_openclaw_session(result.output)
                if trajectory and not fallback_trajectory:
                    fallback_trajectory = trajectory
                if expected_user_content and _trajectory_matches_request(
                    trajectory,
                    expected_user_content,
                ):
                    return trajectory

            # Do NOT return an unmatched fallback trajectory: it likely belongs
            # to a different task/run on the same sandbox and would cause the
            # stored trajectory to show a completely different problem than the
            # one actually sent to the model (see: problem-user-content-mismatch).
            if fallback_trajectory and expected_user_content:
                logger.warning(
                    "Trajectory retrieval found %d session file(s) but none matched "
                    "the expected user content (len=%d). Discarding stale fallback "
                    "to avoid recording a mismatched trajectory.",
                    len(session_files),
                    len(expected_user_content),
                )
                return []
            return fallback_trajectory

        except Exception as e:
            logger.warning("Failed to retrieve session trajectory: %s", e)
            return []

    def _retrieve_trajectory_sync(
        self,
        *,
        sandbox_id: str = "",
        rock_sandbox_url: str = "",
        expected_user_content: str = "",
    ) -> list[dict]:
        """Synchronous wrapper for trajectory retrieval."""
        import asyncio

        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run,
                        self._retrieve_session_trajectory(
                            sandbox_id=sandbox_id,
                            rock_sandbox_url=rock_sandbox_url,
                            expected_user_content=expected_user_content,
                        ),
                    )
                    return future.result(timeout=30)
            else:
                return asyncio.run(
                    self._retrieve_session_trajectory(
                        sandbox_id=sandbox_id,
                        rock_sandbox_url=rock_sandbox_url,
                        expected_user_content=expected_user_content,
                    )
                )
        except Exception as e:
            logger.warning("Trajectory retrieval failed: %s", e)
            return []

    # Paths that OpenClaw uses for state in the workspace.
    _OPENCLAW_HOME_CANDIDATES = [
        "/root/.openclaw",
        "/tmp/oc_home/.openclaw",
    ]
    _SYSTEM_PROMPT_PATHS = [
        "/root/.openclaw/workspace/SOUL.md",
        "/root/.openclaw/workspace/AGENTS.md",
        "/tmp/oc_home/.openclaw/workspace/SOUL.md",
        "/tmp/oc_home/.openclaw/workspace/AGENTS.md",
    ]

    def _retrieve_system_prompt_from_sandbox(self, sandbox: Any = None) -> str:
        """Try to read system prompt files from the sandbox workspace."""
        # Try via sandbox session object (passed from Runner)
        if sandbox is not None:
            try:
                parts = []
                for path in self._SYSTEM_PROMPT_PATHS:
                    try:
                        content = sandbox.read_text(path)
                        if content and content.strip():
                            parts.append(content.strip())
                    except Exception:
                        continue
                if parts:
                    return "\n\n".join(parts)
            except Exception as exc:
                logger.debug("System prompt retrieval via sandbox session failed: %s", exc)

        # Try via standalone SDK client
        sb = self._get_sandbox_client()
        if sb is None:
            return ""
        try:
            import asyncio

            async def _read_files():
                from rock.actions.sandbox.request import CreateBashSessionRequest
                try:
                    await sb.create_session(CreateBashSessionRequest(session="sysprompt"))
                except Exception:
                    pass
                result = await sb.arun(
                    "cat " + " ".join(self._SYSTEM_PROMPT_PATHS) + " 2>/dev/null",
                    session="sysprompt",
                )
                return getattr(result, "output", "").strip()

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop is not None and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, _read_files())
                    return future.result(timeout=15)
            else:
                return asyncio.run(_read_files())
        except Exception as exc:
            logger.debug("System prompt retrieval via SDK client failed: %s", exc)
            return ""

    def _resolve_max_tokens(self) -> int:
        """Resolve max_tokens by querying /v1/models or using cached/fallback."""
        if self._resolved_max_tokens is not None:
            return self._resolved_max_tokens
        if self._max_tokens is not None:
            self._resolved_max_tokens = self._max_tokens
            return self._max_tokens
        # Try to query vLLM /v1/models
        try:
            import httpx
            api_base = self._api_base.rstrip("/")
            # For OpenClaw gateway, the upstream model info may not be available,
            # so we try but don't fail hard
            resp = httpx.get(f"{api_base}/models", timeout=5.0)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    max_len = data[0].get("max_model_len")
                    if isinstance(max_len, int) and max_len > 0:
                        self._resolved_max_tokens = max_len
                        return max_len
        except Exception:
            pass
        self._resolved_max_tokens = 65536
        return 65536

    def _build_request_payload(self, messages: list[dict]) -> dict:
        """Build the request payload for chat completions."""
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "stream": True,
        }
        resolved = self._resolve_max_tokens()
        if resolved is not None:
            payload["max_tokens"] = resolved
        if self._top_p is not None:
            payload["top_p"] = self._top_p
        return payload

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        # Circuit breaker: fail immediately if backend was detected as down.
        with self._circuit_lock:
            if self._circuit_open and self._circuit_open_error is not None:
                raise self._circuit_open_error

        try:
            import httpx
        except ImportError:
            raise RuntimeError(
                "The 'httpx' package is required for OpenClawAgent. "
                "Install with: pip install httpx"
            )

        start = time.time()

        # OpenClaw's gateway builds its own system prompt internally.
        # We only send the user message, optionally prefixed with a custom system prompt.
        if self._user_system_prompt:
            user_content = f"{self._user_system_prompt}\n\n{task.problem}"
        else:
            user_content = task.problem
        request_messages = [
            {"role": "user", "content": user_content},
        ]

        sandbox_id = self._sandbox_id
        if sandbox is not None:
            sandbox_id = str(getattr(sandbox, "sandbox_id", "") or sandbox_id)

        # Resolve the api_base for this invocation.
        # Priority:
        #   1. Sandbox session has a proxy endpoint (auto-deploy or pool session)
        #   2. Round-robin from gateway_pool (predeployed multi-sandbox mode)
        #   3. Single configured api_base
        resolved_api_base = ""
        if sandbox is not None and hasattr(sandbox, "proxy_v1_base"):
            # Auto-deploy: sandbox session provides its own proxy endpoint.
            resolved_api_base = ""  # let ensure_ready() set it below
        elif self._gateway_pool:
            with self._gateway_pool_lock:
                # Round-robin: rotate the deque so each concurrent call gets
                # a different gateway when multiple are configured.
                resolved_api_base = self._gateway_pool[0]
                self._gateway_pool.rotate(-1)
        else:
            resolved_api_base = self._api_base

        # Auto-deploy if api_base not set but runtime manager is configured
        runtime_info: dict[str, Any] = {
            "sandbox_id": sandbox_id,
            "gateway_url": f"{resolved_api_base}/chat/completions" if resolved_api_base else "",
            "api_base": resolved_api_base,
        }
        artifact_data: dict[str, Any] = {
            "artifact_manifest": {},
            "gateway_log_excerpt": "",
            "workspace_snapshot_paths": [],
            "workspace_file_contents": {},
            "sandbox_metadata": {},
        }
        if not runtime_info["api_base"] and sandbox is not None and self._runtime_manager and self._runtime_manager.is_configured:
            runtime_info = self._runtime_manager.ensure_ready(sandbox)
        elif not runtime_info["api_base"]:
            raise RuntimeError(
                "OpenClawAgent requires either agent.config.api_base (or gateway_pool) "
                "or a live ROCK sandbox plus rock_agent_config_path/openclaw_config_path."
            )
        else:
            self._ensure_agent_md(sandbox)

        url = f"{runtime_info['api_base'].rstrip('/')}/chat/completions"
        actual_sandbox_id, actual_rock_sandbox_url = self._extract_sandbox_target_from_api_base(
            runtime_info.get("api_base", "")
        )
        if actual_sandbox_id:
            runtime_info["sandbox_id"] = actual_sandbox_id
        if not actual_rock_sandbox_url:
            actual_rock_sandbox_url = self._rock_sandbox_url
        headers = {
            "Authorization": f"bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }

        self._health_check(url, headers)

        request_payload = self._build_request_payload(request_messages)

        # Retry loop with exponential backoff
        response_json: dict = {}
        raw_output = ""
        raw_reasoning = ""
        last_error: Exception | None = None
        retry_responses: list[dict[str, Any]] = []
        cumulative_token_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        recovered_trajectory: list[dict] = []
        partial_reasoning_only = False
        received_done: bool = False
        for attempt in range(1, self._max_attempts + 1):
            attempt_start = time.monotonic()
            try:
                chunks: list[str] = []
                reasoning_chunks: list[str] = []
                status_code: int = 0
                resp_headers: dict = {}
                received_done: bool = False
                with httpx.Client(timeout=self._request_timeout, trust_env=False) as client:
                    with client.stream("POST", url, headers=headers, json=request_payload) as response:
                        status_code = response.status_code
                        resp_headers = dict(response.headers)
                        content_type = response.headers.get("content-type", "")

                        if "application/json" in content_type:
                            # Non-SSE JSON response (error or non-streaming reply).
                            response.read()
                            body_text = response.text
                            try:
                                response_json = json.loads(body_text)
                            except json.JSONDecodeError:
                                response_json = {}
                            if status_code >= 400:
                                # Error response — preserve the JSON body for diagnostics.
                                pass
                            else:
                                # Successful non-streaming JSON — extract assistant content.
                                choices = response_json.get("choices") or []
                                if choices:
                                    msg = choices[0].get("message", {})
                                    assistant_content = msg.get("content", "")
                                    reasoning_content = msg.get("reasoning_content", "")
                                    if assistant_content:
                                        chunks.append(_coerce_text_content(assistant_content))
                                    elif reasoning_content:
                                        reasoning_chunks.append(_coerce_text_content(reasoning_content))
                        else:
                            # SSE path (text/event-stream or unspecified Content-Type).
                            if status_code >= 400:
                                # Read the error body before raise_for_status.
                                raw_parts: list[str] = []
                                for line in response.iter_lines():
                                    raw_parts.append(line)
                                full_body = "\n".join(raw_parts)
                                try:
                                    response_json = json.loads(full_body)
                                except json.JSONDecodeError:
                                    response_json = {}
                            else:
                                received_done = False
                                for line in response.iter_lines():
                                    if not line.startswith("data:"):
                                        continue
                                    data = line[len("data:"):].strip()
                                    if data == "[DONE]":
                                        received_done = True
                                        break
                                    try:
                                        chunk_json = json.loads(data)
                                    except json.JSONDecodeError:
                                        continue
                                    choices = chunk_json.get("choices") or []
                                    delta = choices[0].get("delta", {}) if choices else {}
                                    content = delta.get("content")
                                    reasoning_content = delta.get("reasoning_content")
                                    if content:
                                        chunks.append(content)
                                    if reasoning_content:
                                        reasoning_chunks.append(_coerce_text_content(reasoning_content))
                                    if chunk_json.get("usage"):
                                        response_json = chunk_json

                raw_output = "".join(chunks)
                raw_reasoning = "".join(reasoning_chunks)
                recovered_trajectory = []
                trajectory_error = ""
                if not raw_output:
                    recovered_trajectory = self._retrieve_trajectory_sync(
                        sandbox_id=runtime_info.get("sandbox_id", ""),
                        rock_sandbox_url=actual_rock_sandbox_url,
                        expected_user_content=request_messages[0]["content"],
                    )
                    trajectory_error = _extract_trajectory_error(recovered_trajectory)
                    recovered_content, recovered_reasoning = _recover_partial_output_from_trajectory(
                        recovered_trajectory
                    )
                    if recovered_content and not raw_output:
                        raw_output = recovered_content
                    if recovered_reasoning and not raw_reasoning:
                        raw_reasoning = recovered_reasoning
                if raw_reasoning:
                    if not response_json:
                        response_json = {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": raw_output if raw_output else "",
                                        "reasoning_content": raw_reasoning,
                                    }
                                }
                            ]
                        }
                    else:
                        choices = response_json.setdefault("choices", [{}])
                        if not choices:
                            choices.append({})
                        message = choices[0].setdefault("message", {})
                        if isinstance(message, dict):
                            message.setdefault("role", "assistant")
                            message["content"] = raw_output if raw_output else ""
                            message["reasoning_content"] = raw_reasoning
                # Accumulate token usage across retries.
                attempt_usage = (response_json or {}).get("usage")
                if attempt_usage:
                    cumulative_token_usage["prompt_tokens"] += int(attempt_usage.get("prompt_tokens", 0))
                    cumulative_token_usage["completion_tokens"] += int(attempt_usage.get("completion_tokens", 0))
                    cumulative_token_usage["total_tokens"] += int(attempt_usage.get("total_tokens", 0))
                if raw_output:
                    # Successful response — reset the consecutive-empty counter.
                    with self._circuit_lock:
                        self._consecutive_empty_sse = 0
                    break
                if raw_reasoning:
                    # Partial reasoning counts as a success for the circuit breaker.
                    with self._circuit_lock:
                        self._consecutive_empty_sse = 0
                    partial_reasoning_only = True
                    logger.info(
                        "Partial reasoning only: reasoning_chars=%d received_done=%s "
                        "recovered_trajectory_len=%d elapsed=%.1fs",
                        len(raw_reasoning),
                        received_done,
                        len(recovered_trajectory),
                        time.monotonic() - attempt_start,
                    )
                    break
                elapsed = time.monotonic() - attempt_start
                response_body = response_json or ""
                error_type = classify_error(
                    response_json=response_json,
                    status_code=status_code,
                )
                # Detect crashed backend: HTTP 200 + empty SSE (no chunks, no [DONE]).
                # classify_error() returns "unknown" here because response_json is {}
                # (no choices key).  Use a dedicated "empty_sse_body" type so the
                # circuit breaker can count these separately from the "empty_response"
                # case (agent returned properly-structured but empty content).
                is_empty_sse_body = (
                    status_code == 200
                    and not chunks
                    and not received_done
                    and not response_json
                )
                if is_empty_sse_body and error_type == "unknown":
                    error_type = "empty_sse_body"
                retry_responses.append({
                    "attempt": attempt,
                    "status_code": status_code,
                    "headers": resp_headers,
                    "body": str(response_body),
                    "elapsed_sec": elapsed,
                    "error_type": error_type,
                    "trajectory_error": trajectory_error,
                })
                logger.warning(
                    "OpenClaw attempt %d/%d returned empty content: status=%s elapsed=%.2fs body=%r reasoning_chars=%d trajectory_error=%r",
                    attempt,
                    self._max_attempts,
                    status_code,
                    elapsed,
                    response_body,
                    len(raw_reasoning),
                    trajectory_error,
                )
                if trajectory_error:
                    logger.error(
                        "OpenClaw session trajectory captured upstream error: %s",
                        trajectory_error,
                    )
                if error_type == "proxy_timeout":
                    logger.error(
                        "Proxy timeout detected (likely ROCK proxy 120s default). "
                        "Increase sandbox_config.proxy_timeout (current: %ds) or "
                        "reduce problem complexity. See: sandbox_config.proxy_timeout",
                        self._proxy_timeout,
                    )
                if error_type == "control_plane_unavailable":
                    logger.error(
                        "ROCK control plane unavailable (sandbox marked 'not started' / "
                        "'not alive'). Check Redis container health and connectivity.",
                    )
                if error_type == "internal_error":
                    logger.error(
                        "Gateway internal error detected (likely workspace-state.json "
                        "contention under concurrency). Use gateway_pool with separate "
                        "sandboxes to isolate concurrent requests.",
                    )
                if error_type == "empty_sse_body":
                    with self._circuit_lock:
                        current_count = self._consecutive_empty_sse + 1  # peek before increment
                    logger.warning(
                        "Empty SSE body detected (HTTP 200, no content, no [DONE]) — "
                        "possible model backend crash. consecutive_count=%d threshold=%d. "
                        "Check if the model server at %s is still running.",
                        current_count,
                        self._backend_down_threshold,
                        url,
                    )
                # Circuit breaker: track consecutive empty-SSE-body failures.
                # "empty_sse_body" = HTTP 200 but absolutely no SSE content and no
                # [DONE] — the signature of a crashed/unresponsive model backend.
                if error_type == "empty_sse_body":
                    with self._circuit_lock:
                        self._consecutive_empty_sse += 1
                        consecutive = self._consecutive_empty_sse
                    if consecutive >= self._backend_down_threshold:
                        backend_error = BackendDownError(
                            f"Model backend appears to be down: {consecutive} consecutive "
                            f"empty responses (HTTP 200 + empty SSE body, no [DONE] signal). "
                            f"The upstream model service at {url} likely crashed or became "
                            f"unresponsive. Aborting evaluation to avoid wasting time on "
                            f"dead requests. Check if the vLLM/model server is still running. "
                            f"Set agent.config.backend_down_threshold to adjust sensitivity "
                            f"(current: {self._backend_down_threshold}).",
                            consecutive_failures=consecutive,
                            url=url,
                        )
                        logger.critical(
                            "CIRCUIT BREAKER OPEN: %d consecutive empty-SSE responses. "
                            "Model backend at %s appears down. All subsequent solve() "
                            "calls will fail immediately. Error: %s",
                            consecutive,
                            url,
                            backend_error,
                        )
                        with self._circuit_lock:
                            self._circuit_open = True
                            self._circuit_open_error = backend_error
                        raise backend_error
                last_error = OpenClawRequestError(
                    (
                        f"OpenClaw returned no assistant content on attempt {attempt}"
                        if not trajectory_error
                        else f"OpenClaw upstream error on attempt {attempt}: {trajectory_error}"
                    ),
                    error_type=error_type,
                    request_payload=request_payload,
                    response_body=response_body if not trajectory_error else {
                        "response": response_body,
                        "trajectory_error": trajectory_error,
                    },
                    retry_responses=retry_responses,
                )
            except BackendDownError:
                # Circuit breaker fired — propagate immediately without retrying.
                raise
            except Exception as exc:
                elapsed = time.monotonic() - attempt_start
                last_error = exc
                # Extract status code / body from httpx HTTPStatusError if available.
                exc_status = getattr(getattr(exc, "response", None), "status_code", None)
                exc_body = str(exc)
                if exc_status is not None:
                    try:
                        exc_body = getattr(exc, "response", None).text or exc_body
                        response_json = json.loads(exc_body)
                    except Exception:
                        pass
                error_type = classify_error(exc, response_json=response_json or None, status_code=exc_status)
                retry_responses.append(
                    {
                        "attempt": attempt,
                        "status_code": exc_status,
                        "headers": dict(getattr(getattr(exc, "response", None), "headers", {})),
                        "body": exc_body,
                        "elapsed_sec": elapsed,
                        "error_type": error_type,
                    }
                )
                logger.warning(
                    "OpenClaw attempt %d/%d failed: error_type=%s timeout=%.1fs elapsed=%.2fs error=%s",
                    attempt,
                    self._max_attempts,
                    error_type,
                    self._request_timeout,
                    elapsed,
                    exc,
                )
                if error_type == "proxy_timeout":
                    logger.error(
                        "Proxy timeout detected (likely ROCK proxy 120s default). "
                        "Increase sandbox_config.proxy_timeout (current: %ds) or "
                        "reduce problem complexity. See: sandbox_config.proxy_timeout",
                        self._proxy_timeout,
                    )
                if error_type == "control_plane_unavailable":
                    logger.error(
                        "ROCK control plane unavailable (sandbox marked 'not started' / "
                        "'not alive'). Check Redis container health and connectivity.",
                    )
                if error_type == "internal_error":
                    logger.error(
                        "Gateway internal error detected (likely workspace-state.json "
                        "contention under concurrency). Use gateway_pool with separate "
                        "sandboxes to isolate concurrent requests.",
                    )

            if attempt < self._max_attempts:
                # Distinguish "busy/empty" from real errors for retry delay.
                last_error_type = retry_responses[-1]["error_type"] if retry_responses else "unknown"
                if last_error_type == "empty_response":
                    # Agent returned a properly-structured but empty response —
                    # it may still be processing.  Wait longer before retrying.
                    delay = min(60.0 * (2 ** (attempt - 1)), 300.0)
                elif last_error_type == "empty_sse_body":
                    # Crashed backend (HTTP 200 + no SSE content + no [DONE]).
                    # The circuit breaker will open if this persists; use a short
                    # delay so we detect the crash quickly without flooding logs.
                    delay = min(5.0 * (2 ** (attempt - 1)), 30.0)
                else:
                    delay = min(10.0 * (2 ** (attempt - 1)), 120.0)
                jitter = random.uniform(0, delay * 0.3)
                logger.info("Retry delay: %.1fs (error_type=%s)", delay + jitter, last_error_type)
                time.sleep(delay + jitter)

        if not raw_output and not partial_reasoning_only:
            if isinstance(last_error, OpenClawRequestError):
                raise last_error
            error_type = classify_error(
                last_error,
                response_json=response_json or None,
                status_code=retry_responses[-1]["status_code"] if retry_responses else None,
            )
            detail = response_json or {"url": url, "request": request_payload}
            raise OpenClawRequestError(
                f"OpenClaw response did not contain assistant text: {detail}",
                error_type=error_type,
                request_payload=request_payload,
                response_body=detail,
                retry_responses=retry_responses,
            )

        wall_time = time.time() - start

        answer = None
        answer_source = ""
        if raw_output:
            answer = _extract_answer(raw_output)
            answer_source = "raw_output"
        elif raw_reasoning:
            answer = _extract_answer_from_partial_reasoning(raw_reasoning)
            if answer is not None:
                answer_source = "reasoning_content"

        # Use cumulative token usage across all retry attempts.
        token_usage = cumulative_token_usage if any(cumulative_token_usage.values()) else {}
        if not token_usage:
            # Fallback: extract from the final successful response.
            usage = response_json.get("usage")
            if usage:
                token_usage = {
                    "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                    "completion_tokens": int(usage.get("completion_tokens", 0)),
                    "total_tokens": int(usage.get("total_tokens", 0)),
                }

        # Try to retrieve the real agentic trajectory from the sandbox.
        trajectory = recovered_trajectory or self._retrieve_trajectory_sync(
            sandbox_id=runtime_info.get("sandbox_id", ""),
            rock_sandbox_url=actual_rock_sandbox_url,
            expected_user_content=request_messages[0]["content"],
        )
        if not trajectory:
            # Also try extracting from response payload
            trajectory = _extract_reasoning_trajectory_from_payload(response_json)
        if not trajectory:
            # Fallback: record what we know from the API response
            assistant_entry = {"role": "assistant", "content": raw_output}
            if raw_reasoning:
                assistant_entry["thinking"] = raw_reasoning
            trajectory = [
                {"role": "user", "content": task.problem},
                assistant_entry,
            ]

        # Detect gateway session pollution: the model may have received a
        # different problem than what we sent due to stale session state.
        session_tainted = _detect_session_pollution(
            task.problem, raw_output, raw_reasoning,
        )
        if session_tainted:
            logger.warning(
                "Session pollution detected for task %s: model response does "
                "not appear to address the given problem. The gateway may have "
                "reused a stale session. Marking result as tainted.",
                getattr(task, "task_id", "?"),
            )

        # Collect artifacts from runtime manager if available
        if sandbox is not None and self._runtime_manager and self._runtime_manager.is_configured:
            try:
                artifact_data = self._runtime_manager.collect_artifacts(sandbox)
            except Exception as exc:
                logger.warning("Artifact collection failed: %s", exc)

        return AgentResponse(
            answer=answer,
            trajectory=trajectory,
            reasoning_trajectory=_extract_reasoning_trajectory_from_payload(response_json),
            raw_output=raw_output,
            token_usage=token_usage,
            wall_time_sec=wall_time,
            request_messages=request_messages,
            response_json=response_json,
            sandbox_id=runtime_info.get("sandbox_id", ""),
            gateway_url=runtime_info.get("gateway_url", ""),
            artifact_manifest=artifact_data.get("artifact_manifest", {}),
            gateway_log_excerpt=artifact_data.get("gateway_log_excerpt", ""),
            workspace_snapshot_paths=artifact_data.get("workspace_snapshot_paths", []),
            workspace_file_contents=artifact_data.get("workspace_file_contents", {}),
            sandbox_metadata=artifact_data.get("sandbox_metadata", {}),
            system_prompt=extract_system_prompt(trajectory) or self._retrieve_system_prompt_from_sandbox(sandbox),
            finish_reason="incomplete" if partial_reasoning_only and not raw_output else "",
            metadata={
                "gateway_response_id": response_json.get("id", ""),
                "sandbox_id": runtime_info.get("sandbox_id", "") or sandbox_id,
                "model": self._model,
                "retry_responses": retry_responses,
                "retry_count": attempt,
                "token_usage_total": cumulative_token_usage,
                "partial_reasoning_only": partial_reasoning_only and not raw_output,
                "answer_source": answer_source,
                "received_done": received_done,
                "session_tainted": session_tainted,
            },
        )

    def teardown(self) -> None:
        if self._runtime_manager is not None:
            try:
                self._runtime_manager.teardown()
            except Exception as exc:
                logger.warning("RuntimeManager teardown failed: %s", exc)
        self._sandbox_clients = {}


AgentRegistry.register("openclaw", OpenClawAgent)
