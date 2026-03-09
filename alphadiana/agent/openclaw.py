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

This wrapper calls the gateway, then retrieves the session file from the
sandbox to capture the real agentic trajectory (tool calls, intermediate
reasoning, token usage, etc.).

Deployment reference:
  https://github.com/alibaba/ROCK/blob/master/examples/agents/openclaw/REAMDE.md

Prerequisites:
  1. Start ROCK admin + proxy servers
  2. Launch a sandbox with OpenClaw gateway installed (npm install -g openclaw@latest)
  3. Configure openclaw.json with model provider (OPENAI_BASE_URL, OPENAI_API_KEY, etc.)
  4. The gateway listens on port 8080 inside the sandbox, proxied via ROCK proxy
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)


def _extract_answer(text: str) -> str:
    r"""Extract the answer from model output.

    Priority:
      1. \boxed{...} content (last occurrence)
      2. $\boxed{...}$ content (LaTeX variant)
      3. "Answer: <value>" or "**Answer:** <value>" pattern
      4. Last non-empty line (fallback)
    """
    # 1. Try \boxed{} (last occurrence)
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

    # 2. Try "Answer: <value>" or "answer is <value>" pattern
    answer_pattern = re.compile(
        r"(?:\*{0,2})(?:the\s+)?(?:final\s+)?answer(?:\*{0,2})\s*(?:[:：]|is)\s*(.+)",
        re.IGNORECASE,
    )
    matches = list(answer_pattern.finditer(text))
    if matches:
        raw = matches[-1].group(1).strip()
        # Strip trailing period, markdown, units for numeric answers
        raw = re.sub(r"[.\s]*$", "", raw)
        # Try to extract just the number if there's surrounding text
        num_match = re.search(r"(-?\d[\d,]*\.?\d*)", raw)
        if num_match:
            return num_match.group(1).replace(",", "")
        return raw

    # 3. Fallback to last non-empty line
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return lines[-1] if lines else text.strip()


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

    We extract these into a flat list of trajectory events, preserving
    the full detail of each step including thinking and tool calls.
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
            # Content can be a string or a list of content blocks
            content = msg.get("content", "")
            if isinstance(content, list):
                # Extract structured info from content blocks
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
                        # Content can be a list of text blocks
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

            # Add usage info if present (for assistant messages)
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
    """

    name = "openclaw"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "unknown")
        self._api_base = config.get("api_base", "")
        self._model = config.get("model", "openclaw")
        self._gateway_token = config.get("gateway_token", "OPENCLAW")
        self._temperature = config.get("temperature", 0.0)
        self._max_tokens = config.get("max_tokens", 8192)
        self._config = config

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
        self._sandbox_client = None

    def _get_sandbox_client(self):
        """Lazily initialize the ROCK sandbox client for trajectory retrieval."""
        if self._sandbox_client is not None:
            return self._sandbox_client

        if not self._sandbox_id or not self._rock_sandbox_url:
            return None

        try:
            from rock.sdk.sandbox.client import Sandbox
            from rock.sdk.sandbox.config import SandboxConfig

            # Create a client that points to the existing sandbox.
            # We use a dummy base_url for SandboxConfig since we override
            # the URL to the proxy for runtime operations.
            admin_url = self._rock_sandbox_url.replace(
                "/apis/envs/sandbox/v1", ""
            ).replace(":9001", ":9000")
            config = SandboxConfig(base_url=admin_url, image="python:3.11")
            sb = Sandbox(config)
            sb._sandbox_id = self._sandbox_id
            sb.url = self._rock_sandbox_url
            self._sandbox_client = sb
            return sb
        except Exception as e:
            logger.warning("Failed to create ROCK sandbox client: %s", e)
            return None

    async def _retrieve_session_trajectory(self) -> list[dict]:
        """Retrieve the latest session JSONL from the sandbox.

        Returns the parsed trajectory, or an empty list on failure.
        """
        sb = self._get_sandbox_client()
        if sb is None:
            return []

        try:
            from rock.actions.sandbox.request import CreateBashSessionRequest

            # Ensure we have a session for running commands
            try:
                await sb.create_session(
                    CreateBashSessionRequest(session="trajectory")
                )
            except Exception:
                pass  # Session may already exist

            # List session files, sorted by modification time (newest first)
            result = await sb.arun(
                "ls -t /root/.openclaw/agents/main/sessions/*.jsonl 2>/dev/null | head -1",
                session="trajectory",
            )
            session_file = result.output.strip()
            if not session_file or not session_file.endswith(".jsonl"):
                return []

            # Read the session file
            result = await sb.arun(
                f"cat {session_file}",
                session="trajectory",
            )
            return _parse_openclaw_session(result.output)

        except Exception as e:
            logger.warning("Failed to retrieve session trajectory: %s", e)
            return []

    def _retrieve_trajectory_sync(self) -> list[dict]:
        """Synchronous wrapper for trajectory retrieval."""
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an existing event loop; run in a new thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(
                        asyncio.run, self._retrieve_session_trajectory()
                    )
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(
                    self._retrieve_session_trajectory()
                )
        except RuntimeError:
            return asyncio.run(self._retrieve_session_trajectory())
        except Exception as e:
            logger.warning("Trajectory retrieval failed: %s", e)
            return []

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

        # OpenClaw's gateway builds its own system prompt internally
        # (AGENTS.md, SOUL.md, tool definitions, skills, etc.).
        # We only send the user message.  The gateway wraps it in
        # its agentic framework and runs a multi-turn agent loop.
        messages = [
            {"role": "user", "content": task.problem},
        ]

        response = client.chat.completions.create(
            model=self._model,
            messages=messages,
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

        # Try to retrieve the real agentic trajectory from the sandbox.
        # This contains the full multi-turn conversation including tool
        # calls, intermediate reasoning, etc.
        trajectory = self._retrieve_trajectory_sync()
        if not trajectory:
            # Fallback: record what we know from the API response
            trajectory = [
                {"role": "user", "content": task.problem},
                {"role": "assistant", "content": raw_output},
            ]

        return AgentResponse(
            answer=answer,
            trajectory=trajectory,
            raw_output=raw_output,
            token_usage=token_usage,
            wall_time_sec=wall_time,
            metadata={
                "gateway_response_id": response.id,
                "sandbox_id": self._sandbox_id,
            },
        )

    def teardown(self) -> None:
        self._sandbox_client = None


AgentRegistry.register("openclaw", OpenClawAgent)
