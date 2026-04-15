"""OpenCode agent wrapper - runs opencode CLI in non-interactive mode.

OpenCode is a CLI-based coding agent (similar to Claude Code) that can
use tools like code execution to solve problems. This wrapper invokes
`opencode run` with the problem text and collects the output.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.utils.math_answer import extract_answer_candidate, extract_boxed

logger = logging.getLogger(__name__)

_EXPLICIT_ANSWER_RE = re.compile(
    r"(?:\*{0,2})(?:the\s+)?(?:final\s+)?answer(?:\*{0,2})\s*(?:[:：]|is|=)\s*(.+)",
    re.IGNORECASE,
)

_DEFAULT_SYSTEM_PROMPT = (
    "You are an expert problem solver. When given a problem, actively use "
    "your available tools throughout your reasoning process. Use code execution "
    "to verify intermediate steps and confirm your final answer.\n\n"
    "When you have reached your final answer, you MUST present it in the following format:\n\n"
    "$$\\boxed{your answer here}$$\n\n"
    "Do not skip the boxed format. The boxed answer must appear at the very end "
    "of your response and contain only the final answer, not explanations."
)


def _extract_event_texts(obj: dict[str, Any]) -> list[str]:
    """Extract user-visible assistant text from OpenCode JSON events."""
    texts: list[str] = []

    if obj.get("type") == "text":
        text = obj.get("text")
        if isinstance(text, str) and text:
            texts.append(text)

    part = obj.get("part")
    if isinstance(part, dict):
        if part.get("type") == "text":
            text = part.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
        elif part.get("type") == "assistant":
            message = part.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    texts.append(content)

    if obj.get("type") == "assistant":
        message = obj.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str) and content:
                texts.append(content)

    return texts


def _extract_strict_answer(text: str) -> str:
    """Extract only explicit final answers from partial OpenCode output."""
    boxed = extract_boxed(text)
    if boxed is not None:
        return boxed.strip()

    matches = list(_EXPLICIT_ANSWER_RE.finditer(text))
    if matches:
        return matches[-1].group(1).strip()

    return ""


class OpenCodeAgent(Agent):
    """Agent that runs OpenCode CLI to solve tasks.

    Config keys:
        model: Model in provider/model format (e.g. "custom/deepseek-chat")
        api_base: OpenAI-compatible API base URL
        api_key: API key
        model_name: Model name for the provider
        tool_call: Whether the model supports tool calling (default: True)
        timeout: Timeout in seconds for opencode run (default: 1200)
        variant: Optional provider-specific reasoning effort
        agent: Optional opencode agent name
        print_logs: Whether to print opencode logs to stderr
        log_level: Optional opencode log level
        system_prompt: Custom system prompt (optional)
    """

    name = "opencode"

    def setup(self, config: dict) -> None:
        self._model = self._resolve_setting(config, "model", "OPENAI_MODEL_NAME")
        self._api_base = self._resolve_setting(config, "api_base", "OPENAI_BASE_URL")
        self._api_key = self._resolve_setting(
            config,
            "api_key",
            "OPENAI_API_KEY",
            default="EMPTY",
        )
        self._model_name = self._resolve_setting(config, "model_name", "OPENAI_MODEL_NAME")
        if not self._model and self._model_name:
            self._model = f"custom/{self._model_name}"
        self._tool_call = bool(config.get("tool_call", True))
        self._timeout = int(config.get("timeout", 1200))
        self._variant = str(config.get("variant", "")).strip()
        self._agent_name = str(config.get("agent", "")).strip()
        self._agent_md_path = str(config.get("agent_md_path", "")).strip()
        self._agent_md_content = str(config.get("agent_md_content", "")).strip()
        self._print_logs = bool(config.get("print_logs", False))
        self._log_level = str(config.get("log_level", "")).strip()
        self._system_prompt = config.get("system_prompt", _DEFAULT_SYSTEM_PROMPT)
        self._opencode_bin = config.get("opencode_bin", "opencode")
        self._streaming = config.get("streaming") if "streaming" in config else None

        if not self._agent_name:
            if self._agent_md_path:
                self._agent_name = Path(self._agent_md_path).stem
            elif self._agent_md_content:
                self._agent_name = "custom-agent"

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

    def solve(self, task: BenchmarkTask, sandbox: Any = None) -> AgentResponse:
        start = time.time()

        # Build the prompt with system instructions + problem
        prompt = task.problem
        if self._system_prompt.strip():
            prompt = f"{self._system_prompt}\n\n--- Problem ---\n{task.problem}"

        # Create a temporary working directory for opencode
        with tempfile.TemporaryDirectory(prefix="opencode-task-") as workdir:
            # Write opencode config
            config_root = Path(workdir) / "xdg-config"
            config_dir = config_root / "opencode"
            config_dir.mkdir(parents=True, exist_ok=True)
            provider_options: dict[str, Any] = {
                "apiKey": self._api_key,
                "baseURL": self._api_base,
                "timeout": self._timeout * 1000,
            }
            if self._streaming is not None:
                provider_options["streaming"] = bool(self._streaming)
            provider_config = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "custom": {
                        "api": "openai",
                        "name": "Custom Provider",
                        "options": provider_options,
                        "models": {
                            self._model_name: {
                                "name": self._model_name,
                                "tool_call": self._tool_call,
                            }
                        },
                    }
                },
                "model": f"custom/{self._model_name}",
                "small_model": f"custom/{self._model_name}",
            }
            config_path = config_dir / "opencode.json"
            config_path.write_text(json.dumps(provider_config, indent=2))

            if self._agent_name and (self._agent_md_path or self._agent_md_content):
                agent_dir = config_dir / "agent"
                agent_dir.mkdir(parents=True, exist_ok=True)
                if self._agent_md_path:
                    agent_path = Path(self._agent_md_path).expanduser()
                    if not agent_path.is_absolute():
                        agent_path = (Path.cwd() / agent_path).resolve()
                    agent_text = agent_path.read_text()
                else:
                    agent_text = self._agent_md_content
                (agent_dir / f"{self._agent_name}.md").write_text(agent_text)

            # Run opencode
            env = os.environ.copy()
            env["OPENAI_API_KEY"] = self._api_key
            env["OPENAI_BASE_URL"] = self._api_base
            env["XDG_CONFIG_HOME"] = str(config_root)
            # Clear proxy vars that can interfere
            for var in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
                        "all_proxy", "http_proxy", "https_proxy"):
                env.pop(var, None)

            cmd = [
                self._opencode_bin, "run",
                "--format", "json",
                "--dir", workdir,
                "--title", task.task_id,
                prompt,
            ]
            if self._model:
                cmd.extend(["--model", self._model])
            if self._variant:
                cmd.extend(["--variant", self._variant])
            if self._agent_name:
                cmd.extend(["--agent", self._agent_name])
            if self._print_logs:
                cmd.append("--print-logs")
            if self._log_level:
                cmd.extend(["--log-level", self._log_level])

            logger.info("Running opencode for task %s (timeout=%ds)", task.task_id, self._timeout)

            process: subprocess.Popen[str] | None = None
            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    env=env,
                    cwd=workdir,
                    start_new_session=True,
                )
                raw_output, stderr = process.communicate(timeout=self._timeout)
                returncode = process.returncode
            except subprocess.TimeoutExpired:
                logger.warning("OpenCode timed out for task %s after %ds", task.task_id, self._timeout)
                raw_output = ""
                stderr = f"Timeout after {self._timeout}s"
                returncode = -1
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        raw_output, timed_out_stderr = process.communicate(timeout=5)
                        stderr = timed_out_stderr or stderr
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        raw_output, timed_out_stderr = process.communicate()
                        stderr = timed_out_stderr or stderr
                    except ProcessLookupError:
                        raw_output, timed_out_stderr = process.communicate()
                        stderr = timed_out_stderr or stderr

        wall_time = time.time() - start

        # Parse JSON output to extract content
        content_parts = []
        events = []
        session_id = ""
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                events.append(obj)
                if not session_id:
                    session_id = str(obj.get("sessionID", ""))
                    if not session_id and isinstance(obj.get("part"), dict):
                        session_id = str(obj["part"].get("sessionID", ""))
                content_parts.extend(_extract_event_texts(obj))
            except (json.JSONDecodeError, ValueError):
                # Non-JSON output, treat as raw text
                content_parts.append(line)

        assistant_text = "\n".join(part for part in content_parts if part).strip()
        full_content = assistant_text or raw_output

        # Extract answer
        if returncode == -1:
            answer = _extract_strict_answer(assistant_text)
        else:
            answer = extract_answer_candidate(full_content)

        if returncode != 0 and not answer:
            logger.warning(
                "OpenCode returned non-zero exit code %d for task %s. stderr: %s",
                returncode, task.task_id, stderr[:500],
            )

        return AgentResponse(
            answer=answer,
            trajectory=[
                {"role": "user", "content": task.problem},
                {"role": "assistant", "content": full_content},
            ],
            raw_output=full_content,
            wall_time_sec=wall_time,
            metadata={
                "returncode": returncode,
                "stderr": stderr[:2000] if stderr else "",
                "num_events": len(events),
                "session_id": session_id,
            },
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("opencode", OpenCodeAgent)
