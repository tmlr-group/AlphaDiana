"""TerminalBench-2 Docker agent — per-task container with multi-turn LLM loop."""
from __future__ import annotations

import logging
import os
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


class TerminalBench2DockerAgent(TerminalBench2ContainerMixin, Agent):
    """Per-task Docker container agent for terminal-bench-2 evaluation."""

    name = "terminal_bench2_docker"
    version = "1.0"

    def setup(self, config: dict) -> None:
        self._model = config.get("model") or os.environ.get("OPENAI_MODEL_NAME", "")
        self._api_base = config.get("api_base") or os.environ.get("OPENAI_BASE_URL", "")
        self._api_key = (
            config.get("api_key") or os.environ.get("OPENAI_API_KEY", "EMPTY")
        )
        self._max_rounds = int(config.get("max_rounds", 10))
        self._max_tokens = int(config.get("max_tokens", 4096))
        self._temperature = float(config.get("temperature", 0.0))
        self._setup_container_config(config)
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import httpx
                from openai import OpenAI

                self._client = OpenAI(
                    base_url=self._api_base or None,
                    api_key=self._api_key,
                    http_client=httpx.Client(trust_env=False),
                )
            except ImportError:
                raise RuntimeError(
                    "The 'openai' package is required for TerminalBench2DockerAgent. "
                    "Install with: pip install openai"
                )
        return self._client

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        t_start = time.time()
        client = self._get_client()

        docker_image = task.metadata.get("docker_image", "")
        if not docker_image:
            raise ValueError(
                f"Task {task.task_id} missing 'docker_image' in metadata. "
                "Ensure TerminalBench2Benchmark populated task.metadata correctly."
            )

        logs_dir = self._logs_dir_for_task(task)
        timeout_sec = int(task.metadata.get("timeout_sec", self._timeout_sec))
        test_timeout_sec = self._test_timeout_sec

        container_id: str = ""
        reward_content: str = ""
        trajectory: list[dict] = []
        raw_output_parts: list[str] = []

        try:
            container_id = self._start_container(docker_image, logs_dir, task)

            messages: list[dict] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": task.problem},
            ]
            trajectory.extend([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": task.problem},
            ])

            for round_num in range(self._max_rounds):
                logger.debug("Task %s — round %d/%d", task.task_id, round_num + 1, self._max_rounds)

                response = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                )
                llm_text = response.choices[0].message.content or ""
                raw_output_parts.append(llm_text)
                messages.append({"role": "assistant", "content": llm_text})
                trajectory.append({"role": "assistant", "content": llm_text})

                commands = parse_commands(llm_text)
                done = is_done(llm_text)

                if commands:
                    # Always execute commands before respecting DONE.
                    # LLMs often output a full plan (all commands + DONE) in one turn;
                    # we must execute those commands so the task state is actually modified.
                    exec_outputs: list[str] = []
                    for cmd in commands:
                        cmd_output = self._exec_command(container_id, cmd, timeout_sec)
                        exec_outputs.append(f"$ {cmd}\n{cmd_output}")

                    turn_output = "\n".join(exec_outputs)
                    messages.append({"role": "user", "content": turn_output})
                    trajectory.append({"role": "user", "content": turn_output})

                    if done:
                        logger.info(
                            "Task %s — LLM signalled DONE with %d commands at round %d (executed)",
                            task.task_id, len(commands), round_num + 1,
                        )
                        break
                elif done:
                    logger.info("Task %s — LLM signalled DONE at round %d", task.task_id, round_num + 1)
                    break
                else:
                    logger.info(
                        "Task %s — no commands and no DONE at round %d, treating as done",
                        task.task_id, round_num + 1,
                    )
                    break

            logger.info("Task %s — running tests/test.sh", task.task_id)
            self._run_tests(container_id, test_timeout_sec)

            reward_content = self._read_reward(logs_dir, task.task_id)
            logger.info("Task %s — reward.txt: %r", task.task_id, reward_content)

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
            },
            system_prompt=SYSTEM_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_docker", TerminalBench2DockerAgent)
