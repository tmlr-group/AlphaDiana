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
  - Static: ``api_base`` is pre-configured (gateway already running)
  - Dynamic: ``rock_agent_config_path`` + ``openclaw_config_path`` provided,
    gateway is bootstrapped inside a live ROCK sandbox automatically

Deployment reference:
  https://github.com/alibaba/ROCK/blob/master/examples/agents/openclaw/REAMDE.md
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
from alphadiana.utils.math_answer import extract_answer_candidate

logger = logging.getLogger(__name__)


def _extract_answer(text: str) -> str:
    r"""Extract the final answer from model output."""
    return extract_answer_candidate(text)


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
      - rock_agent_config_path: Path to ROCK agent config YAML (for dynamic bootstrap)
      - openclaw_config_path: Path to OpenClaw gateway config JSON (for dynamic bootstrap)
    """

    name = "openclaw"

    def setup(self, config: dict) -> None:
        self.version = config.get("version", "unknown")
        self._api_base = config.get("api_base", "")
        self._model = config.get("model", "openclaw")
        self._gateway_token = config.get("gateway_token", "OPENCLAW")
        self._temperature = config.get("temperature", 0.0)
        self._max_tokens = config.get("max_tokens", 8192)
        self._max_attempts = max(1, int(config.get("max_attempts", 3)))
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

        # Runtime manager for dynamic gateway bootstrap
        try:
            from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager
            self._runtime_manager = OpenClawRuntimeManager(config)
        except ImportError:
            self._runtime_manager = None

    def _get_sandbox_client(self):
        """Lazily initialize the ROCK sandbox client for trajectory retrieval."""
        if self._sandbox_client is not None:
            return self._sandbox_client

        if not self._sandbox_id or not self._rock_sandbox_url:
            return None

        try:
            from rock.sdk.sandbox.client import Sandbox
            from rock.sdk.sandbox.config import SandboxConfig

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
        """Retrieve the latest session JSONL from the sandbox."""
        sb = self._get_sandbox_client()
        if sb is None:
            return []

        try:
            from rock.actions.sandbox.request import CreateBashSessionRequest

            try:
                await sb.create_session(
                    CreateBashSessionRequest(session="trajectory")
                )
            except Exception:
                pass  # Session may already exist

            result = await sb.arun(
                "ls -t /root/.openclaw/agents/main/sessions/*.jsonl 2>/dev/null | head -1",
                session="trajectory",
            )
            session_file = result.output.strip()
            if not session_file or not session_file.endswith(".jsonl"):
                return []

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
            import httpx
        except ImportError:
            raise RuntimeError(
                "The 'httpx' package is required for OpenClawAgent. "
                "Install with: pip install httpx"
            )

        start = time.time()

        # OpenClaw's gateway builds its own system prompt internally.
        # We only send the user message.
        request_messages = [
            {"role": "user", "content": task.problem},
        ]

        sandbox_id = self._sandbox_id
        if sandbox is not None:
            sandbox_id = str(getattr(sandbox, "sandbox_id", "") or sandbox_id)

        # Dynamic bootstrap if api_base not set but runtime manager is configured
        runtime_info: dict[str, Any] = {
            "sandbox_id": sandbox_id,
            "gateway_url": f"{self._api_base}/chat/completions" if self._api_base else "",
            "api_base": self._api_base,
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
                "OpenClawAgent requires either agent.config.api_base or a live ROCK "
                "sandbox plus rock_agent_config_path/openclaw_config_path."
            )

        url = f"{runtime_info['api_base'].rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        request_payload = {
            "model": self._model,
            "messages": request_messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }

        # Retry loop with exponential backoff
        response_json: dict = {}
        raw_output = ""
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                with httpx.Client(timeout=300.0, trust_env=False) as client:
                    response = client.post(url, headers=headers, json=request_payload)
                response.raise_for_status()
                response_json = response.json()
                raw_output = _extract_raw_output_from_payload(response_json)
                if raw_output:
                    break
                last_error = RuntimeError(
                    f"OpenClaw returned no assistant content on attempt {attempt}"
                )
            except Exception as exc:
                last_error = exc
                logger.warning("OpenClaw attempt %d/%d failed: %s", attempt, self._max_attempts, exc)

            if attempt < self._max_attempts:
                time.sleep(min(2, attempt))

        if not raw_output:
            if last_error is not None and not response_json:
                raise last_error
            detail = response_json or {"url": url, "request": request_payload}
            raise RuntimeError(f"OpenClaw response did not contain assistant text: {detail}")

        wall_time = time.time() - start

        answer = _extract_answer(raw_output)

        token_usage = {}
        usage = response_json.get("usage")
        if usage:
            token_usage = {
                "prompt_tokens": int(usage.get("prompt_tokens", 0)),
                "completion_tokens": int(usage.get("completion_tokens", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
            }

        # Try to retrieve the real agentic trajectory from the sandbox.
        trajectory = self._retrieve_trajectory_sync()
        if not trajectory:
            # Also try extracting from response payload
            trajectory = _extract_reasoning_trajectory_from_payload(response_json)
        if not trajectory:
            # Fallback: record what we know from the API response
            trajectory = [
                {"role": "user", "content": task.problem},
                {"role": "assistant", "content": raw_output},
            ]

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
            metadata={
                "gateway_response_id": response_json.get("id", ""),
                "sandbox_id": sandbox_id,
                "model": self._model,
            },
        )

    def teardown(self) -> None:
        self._sandbox_client = None


AgentRegistry.register("openclaw", OpenClawAgent)
