"""TerminalBench-2 DirectLLM agent with a shared control-side harness."""
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
    """DirectLLM terminal-bench-2 agent using local helper scripts."""

    name = "terminal_bench2_docker"
    version = "1.0"

    def setup(self, config: dict) -> None:
        self._model = config.get("model") or os.environ.get("OPENAI_MODEL_NAME", "")
        self._api_base = config.get("api_base") or os.environ.get("OPENAI_BASE_URL", "")
        self._api_key = (
            config.get("api_key") or os.environ.get("OPENAI_API_KEY", "EMPTY")
        )
        self._max_rounds = int(config.get("max_rounds", 10))
        self._max_tokens = int(config.get("max_tokens", 131072))
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
        reward_content: str = ""
        trajectory: list[dict] = []
        raw_output_parts: list[str] = []
        last_response_json: dict[str, Any] = {}
        test_output = ""
        runtime = self._prepare_runtime(task, temp_prefix="tb2-directllm-")
        timeout_sec = int(task.metadata.get("timeout_sec", self._timeout_sec))

        try:
            messages: list[dict] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Solve this terminal-bench-2 task from the local control workspace.\n\n"
                        f"Task:\n{task.problem}\n"
                    ),
                },
            ]
            trajectory.extend([
                {"role": "system", "content": SYSTEM_PROMPT},
                messages[1],
            ])

            for round_num in range(self._max_rounds):
                logger.debug("Task %s — round %d/%d", task.task_id, round_num + 1, self._max_rounds)

                response = client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                )
                last_response_json = response.model_dump(mode="json") if hasattr(response, "model_dump") else {}
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
                        cmd_output = self._exec_local_command(runtime.workdir, cmd, timeout_sec)
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
            verifier_result = self._run_verifier_and_read_reward(
                runtime,
                timeout_sec=self._test_timeout_sec,
            )
            test_output = verifier_result.test_output
            reward_content = verifier_result.reward
            logger.info("Task %s — reward.txt: %r", task.task_id, reward_content)

        finally:
            artifact_files = self._collect_text_artifacts({
                "/terminal_bench2/direct_llm/TASK.md": runtime.helper_paths["task"],
                "/terminal_bench2/direct_llm/AGENTS.md": runtime.helper_paths["agents"],
            })
            self._cleanup_runtime(runtime)

        return AgentResponse(
            answer=reward_content,
            trajectory=trajectory,
            raw_output="\n---\n".join(raw_output_parts),
            wall_time_sec=time.time() - t_start,
            metadata=self._build_metadata(
                runtime,
                reward=reward_content,
                rounds_used=len([m for m in trajectory if m["role"] == "assistant"]),
                runner="direct_llm",
                extra={
                    "test_output": test_output,
                    "verifier_status": verifier_result.status,
                    "verifier_reward_observed": verifier_result.reward is not None,
                },
            ),
            request_messages=messages,
            response_json=last_response_json,
            workspace_file_contents=artifact_files,
            system_prompt=SYSTEM_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_docker", TerminalBench2DockerAgent)
