"""TerminalBench-2 Docker agent — per-task container with multi-turn LLM loop.

Lifecycle per task:
  1. docker run -d --rm -v /tmp/tb2_logs/{task_id}:/logs {image} sleep infinity
  2. Multi-turn LLM loop:
     a. LLM generates shell commands (lines starting with "$ ")
     b. docker exec {container_id} bash -c "{cmd}" — execute each command
     c. Feed output back to LLM as user message
     d. Repeat until LLM outputs "DONE" or max_rounds reached
  3. docker exec {container_id} bash /tests/test.sh — run verifier (deterministic)
  4. Read /tmp/tb2_logs/{task_id}/verifier/reward.txt from host (mounted volume)
  5. docker stop {container_id} — teardown (in finally block)

Config keys:
    model           : Model name (default: OPENAI_MODEL_NAME env var)
    api_base        : OpenAI-compatible base URL (default: OPENAI_BASE_URL env var)
    api_key         : API key (default: OPENAI_API_KEY env var, fallback "EMPTY")
    max_rounds      : Maximum LLM turns per task (default: 10)
    max_tokens      : Max tokens per LLM response (default: 4096)
    temperature     : LLM sampling temperature (default: 0.0)
    timeout_sec     : Per-command docker exec timeout in seconds (default: 300)
    test_timeout_sec: Timeout for tests/test.sh execution in seconds (default: 120)
    logs_base_dir   : Host directory for per-task log volumes (default: /tmp/tb2_logs)

Pre-pull required Docker images before running:
    docker pull alexgshaw/{task-name}:20251031
Image names come from task.toml [environment].docker_image field.
Missing images produce a clear error from `docker run` before any LLM calls.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert software engineer working in a Linux shell environment.\n"
    "You have access to a bash shell — output shell commands on their own line\n"
    "prefixed with `$ ` and I will execute them and return the output.\n"
    "When you believe the task is complete, output DONE on its own line.\n"
    "Do not use \\boxed{} format — this is a shell task, not a math problem."
)


def _parse_commands(text: str) -> list[str]:
    """Extract shell commands from LLM output (lines starting with '$ ')."""
    commands: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("$ "):
            cmd = stripped[2:].rstrip()
            if cmd:
                commands.append(cmd)
    return commands


def _is_done(text: str) -> bool:
    """Return True if the LLM output contains a standalone DONE line."""
    for line in text.splitlines():
        if line.strip() == "DONE":
            return True
    return False


class TerminalBench2DockerAgent(Agent):
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
        self._timeout_sec = int(config.get("timeout_sec", 300))
        self._test_timeout_sec = int(config.get("test_timeout_sec", 120))
        self._logs_base_dir = Path(config.get("logs_base_dir", "/tmp/tb2_logs"))
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self._api_base or None,
                    api_key=self._api_key,
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

        logs_dir = self._logs_base_dir / task.task_id
        logs_dir.mkdir(parents=True, exist_ok=True)

        container_id: str = ""
        reward_content: str = ""
        trajectory: list[dict] = []
        raw_output_parts: list[str] = []

        try:
            container_id = self._start_container(docker_image, logs_dir, task)

            messages: list[dict] = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": task.problem},
            ]
            trajectory.extend([
                {"role": "system", "content": _SYSTEM_PROMPT},
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

                commands = _parse_commands(llm_text)
                done = _is_done(llm_text)

                if commands:
                    # Always execute commands before respecting DONE.
                    # LLMs often output a full plan (all commands + DONE) in one turn;
                    # we must execute those commands so the task state is actually modified.
                    exec_outputs: list[str] = []
                    for cmd in commands:
                        cmd_output = self._exec_command(container_id, cmd)
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
            self._run_tests(container_id)

            reward_content = self._read_reward(logs_dir, task.task_id)
            logger.info("Task %s — reward.txt: %r", task.task_id, reward_content)

        finally:
            if container_id:
                self._stop_container(container_id, task.task_id)
            shutil.rmtree(logs_dir, ignore_errors=True)

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
            system_prompt=_SYSTEM_PROMPT,
        )

    def _start_container(self, docker_image: str, logs_dir: Path, task: BenchmarkTask) -> str:
        # Mount tests/ dir from the task repo into /tests inside the container.
        # test.sh and test_outputs.py are in the repo, NOT baked into the Docker image.
        task_dir = task.metadata.get("task_dir", "")
        tests_host_dir = str(Path(task_dir) / "tests") if task_dir else ""

        cmd = ["docker", "run", "-d", "--rm", "-v", f"{logs_dir}:/logs"]
        if tests_host_dir and Path(tests_host_dir).is_dir():
            cmd += ["-v", f"{tests_host_dir}:/tests:ro"]
        cmd += [docker_image, "sleep", "infinity"]

        logger.info("Task %s — starting container: %s", task.task_id, docker_image)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise RuntimeError(
                f"docker run failed for task {task.task_id} "
                f"(image: {docker_image}): {result.stderr.strip()}\n"
                f"Pre-pull the image with: docker pull {docker_image}"
            )
        container_id = result.stdout.strip()
        if not container_id:
            raise RuntimeError(f"docker run produced no container ID for task {task.task_id}")
        # Ensure /logs/verifier exists inside the container so test.sh can write reward.txt
        subprocess.run(
            ["docker", "exec", container_id, "mkdir", "-p", "/logs/verifier"],
            capture_output=True, timeout=10,
        )
        return container_id

    def _exec_command(self, container_id: str, cmd: str) -> str:
        exec_cmd = ["docker", "exec", container_id, "bash", "-c", cmd]
        try:
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=self._timeout_sec,
            )
            output = result.stdout
            if result.stderr:
                output += result.stderr
            return output.strip()
        except subprocess.TimeoutExpired:
            logger.warning("Command timed out after %ds: %s", self._timeout_sec, cmd[:100])
            return f"[TIMEOUT after {self._timeout_sec}s]"
        except Exception as exc:
            logger.warning("Command exec error: %s", exc)
            return f"[ERROR: {exc}]"

    def _run_tests(self, container_id: str) -> str:
        exec_cmd = ["docker", "exec", container_id, "bash", "/tests/test.sh"]
        try:
            result = subprocess.run(
                exec_cmd, capture_output=True, text=True, timeout=self._test_timeout_sec,
            )
            output = result.stdout
            if result.stderr:
                output += result.stderr
            return output.strip()
        except subprocess.TimeoutExpired:
            logger.warning("tests/test.sh timed out after %ds", self._test_timeout_sec)
            return f"[TIMEOUT after {self._test_timeout_sec}s]"
        except Exception as exc:
            logger.warning("tests/test.sh exec error: %s", exc)
            return f"[ERROR: {exc}]"

    def _read_reward(self, logs_dir: Path, task_id: str) -> str:
        reward_path = logs_dir / "verifier" / "reward.txt"
        if not reward_path.exists():
            logger.warning(
                "Task %s — reward.txt not found at %s "
                "(tests/test.sh may have failed to write it)",
                task_id, reward_path,
            )
            return "0"
        try:
            return reward_path.read_text(encoding="utf-8").strip()
        except Exception as exc:
            logger.warning("Task %s — failed to read reward.txt: %s", task_id, exc)
            return "0"

    def _stop_container(self, container_id: str, task_id: str) -> None:
        try:
            subprocess.run(
                ["docker", "stop", container_id],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as exc:
            logger.warning("Task %s — failed to stop container %s: %s", task_id, container_id[:12], exc)

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_docker", TerminalBench2DockerAgent)
