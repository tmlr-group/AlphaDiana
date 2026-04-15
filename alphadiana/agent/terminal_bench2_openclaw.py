"""Terminal-bench-2 relay agent backed by an OpenClaw gateway."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_common import (
    SYSTEM_PROMPT,
    TerminalBench2ContainerMixin,
    is_done,
    parse_commands,
)
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)


class TerminalBench2OpenClawAgent(TerminalBench2ContainerMixin, Agent):
    """Host-side terminal-bench relay that uses OpenClaw for command planning."""

    name = "terminal_bench2_openclaw"
    version = "1.0"

    def setup(self, config: dict) -> None:
        self._api_base = str(config.get("api_base", "") or "").strip()
        self._model = str(config.get("model", "openclaw") or "openclaw").strip()
        self._gateway_token = str(config.get("gateway_token", "OPENCLAW") or "OPENCLAW").strip()
        self._max_rounds = int(config.get("max_rounds", 10))
        self._max_tokens = int(config.get("max_tokens", 4096))
        self._temperature = float(config.get("temperature", 0.0))
        self._request_timeout = float(config.get("request_timeout", 900))
        self._max_attempts = max(1, int(config.get("max_attempts", 3)))
        self._continue_on_planner_error = bool(config.get("continue_on_planner_error", False))
        self._setup_container_config(config)
        try:
            from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager

            self._runtime_manager = OpenClawRuntimeManager(config)
        except Exception:
            self._runtime_manager = None

    def _resolve_runtime_info(self, sandbox: Any) -> dict[str, str]:
        if self._api_base:
            return {
                "api_base": self._api_base,
                "gateway_url": f"{self._api_base.rstrip('/')}/chat/completions",
                "gateway_token": self._gateway_token,
                "sandbox_id": str(getattr(sandbox, "sandbox_id", "")) if sandbox is not None else "",
            }
        if sandbox is None or self._runtime_manager is None or not self._runtime_manager.is_configured:
            raise RuntimeError(
                "terminal_bench2_openclaw requires agent.config.api_base or "
                "OpenClaw auto-deploy config plus a live ROCK sandbox."
            )
        return self._runtime_manager.ensure_ready(sandbox)

    def _chat(self, messages: list[dict[str, str]], runtime_info: dict[str, str]) -> tuple[str, dict]:
        import httpx

        from alphadiana.agent.openclaw_runtime import _extract_text_from_gateway_payload

        url = f"{runtime_info['api_base'].rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": False,
        }

        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self._request_timeout,
                    trust_env=False,
                )
                body = response.json()
                response.raise_for_status()
                text = _extract_text_from_gateway_payload(body)
                if text:
                    return text, body
                last_error = RuntimeError(f"empty OpenClaw response body: {body!r}")
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "TerminalBench2OpenClawAgent attempt %d/%d failed: %s",
                    attempt,
                    self._max_attempts,
                    exc,
                )
        assert last_error is not None
        raise last_error

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        t_start = time.time()
        docker_image = task.metadata.get("docker_image", "")
        if not docker_image:
            raise ValueError(
                f"Task {task.task_id} missing 'docker_image' in metadata. "
                "Ensure TerminalBench2Benchmark populated task.metadata correctly."
            )

        logs_dir = self._logs_dir_for_task(task)
        timeout_sec = int(task.metadata.get("timeout_sec", self._timeout_sec))
        test_timeout_sec = self._test_timeout_sec
        runtime_info = self._resolve_runtime_info(sandbox)

        container_id = ""
        reward_content = ""
        trajectory: list[dict] = []
        raw_output_parts: list[str] = []
        last_response_json: dict = {}
        planner_error = ""

        try:
            container_id = self._start_container(docker_image, logs_dir, task)
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": task.problem},
            ]
            trajectory.extend(messages)

            for round_num in range(self._max_rounds):
                logger.debug("Task %s — round %d/%d", task.task_id, round_num + 1, self._max_rounds)
                try:
                    llm_text, response_json = self._chat(messages, runtime_info)
                except Exception as exc:
                    if not self._continue_on_planner_error:
                        raise
                    planner_error = str(exc)
                    logger.warning(
                        "TerminalBench2OpenClawAgent planner failed for task %s round %d: %s",
                        task.task_id,
                        round_num + 1,
                        exc,
                    )
                    raw_output_parts.append(f"[PLANNER_ERROR] {planner_error}")
                    trajectory.append({"role": "assistant", "content": f"[PLANNER_ERROR] {planner_error}"})
                    break
                last_response_json = response_json
                raw_output_parts.append(llm_text)
                messages.append({"role": "assistant", "content": llm_text})
                trajectory.append({"role": "assistant", "content": llm_text})

                commands = parse_commands(llm_text)
                done = is_done(llm_text)

                if commands:
                    exec_outputs: list[str] = []
                    for cmd in commands:
                        cmd_output = self._exec_command(container_id, cmd, timeout_sec)
                        exec_outputs.append(f"$ {cmd}\n{cmd_output}")
                    turn_output = "\n".join(exec_outputs)
                    messages.append({"role": "user", "content": turn_output})
                    trajectory.append({"role": "user", "content": turn_output})
                    if done:
                        break
                elif done:
                    break
                else:
                    break

            self._run_tests(container_id, test_timeout_sec)
            reward_content = self._read_reward(logs_dir, task.task_id)
        finally:
            if container_id:
                self._stop_container(container_id, task.task_id)
            self._cleanup_logs_dir(logs_dir)

        return AgentResponse(
            answer=reward_content,
            trajectory=trajectory,
            raw_output="\n---\n".join(raw_output_parts),
            wall_time_sec=time.time() - t_start,
            metadata={
                "docker_image": docker_image,
                "category": task.metadata.get("category", ""),
                "difficulty": task.metadata.get("difficulty", ""),
                "rounds_used": len([m for m in trajectory if m["role"] == "assistant"]),
                "planner_error": planner_error,
            },
            response_json=last_response_json,
            sandbox_id=str(runtime_info.get("sandbox_id", "")),
            gateway_url=str(runtime_info.get("gateway_url", "")),
            system_prompt=SYSTEM_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_openclaw", TerminalBench2OpenClawAgent)
