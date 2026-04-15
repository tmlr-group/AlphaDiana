"""Terminal-bench-2 relay agent backed by OpenCode."""

from __future__ import annotations

import json
import logging
import os
import shlex
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.opencode import _extract_event_texts
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_common import TerminalBench2ContainerMixin
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)

_TERMINAL_BENCH_OPENCODE_PROMPT = """You are solving a terminal-bench-2 task against a running Docker container.

Use only the helper scripts in the current directory:
- `./tb2-exec 'cmd'` runs `cmd` inside the target container via `docker exec`.
- `./tb2-copy-from <remote_path> <local_path>` copies a file from the container.
- `./tb2-copy-to <local_path> <remote_path>` copies a file into the container.
- `./tb2-test` runs the benchmark verifier.

Do not stop or replace the target container. Work through these helpers only.
Inspect `TASK.md` for the benchmark instruction.
"""


class TerminalBench2OpenCodeAgent(TerminalBench2ContainerMixin, Agent):
    """Host-side terminal-bench relay that lets OpenCode operate on the container."""

    name = "terminal_bench2_opencode"
    version = "1.0"

    def setup(self, config: dict) -> None:
        self._setup_container_config(config)
        self._api_base = self._resolve_setting(config, "api_base", "OPENAI_BASE_URL")
        self._api_key = self._resolve_setting(config, "api_key", "OPENAI_API_KEY")
        self._model_name = self._resolve_setting(config, "model_name", "OPENAI_MODEL_NAME")
        self._model = str(config.get("model", "") or "").strip()
        if not self._model and self._model_name:
            self._model = f"custom/{self._model_name}"
        self._tool_call = bool(config.get("tool_call", True))
        self._solver_timeout_sec = int(config.get("solver_timeout_sec", config.get("timeout", 1800)))
        self._variant = str(config.get("variant", "")).strip()
        self._print_logs = bool(config.get("print_logs", False))
        self._log_level = str(config.get("log_level", "")).strip()
        self._opencode_bin = config.get("opencode_bin", "opencode")
        self._streaming = config.get("streaming") if "streaming" in config else None

    @staticmethod
    def _resolve_setting(config: dict, key: str, env_var: str) -> str:
        value = str(config.get(key, "") or "").strip()
        if value and value.upper() != "EMPTY":
            return value
        return os.environ.get(env_var, "").strip()

    def _write_helper_scripts(self, workdir: Path, container_id: str, task: BenchmarkTask) -> None:
        quoted_container = shlex.quote(container_id)
        (workdir / "TASK.md").write_text(task.problem, encoding="utf-8")
        (workdir / "tb2-exec").write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"docker exec {quoted_container} bash -lc \"$*\"",
            ]),
            encoding="utf-8",
        )
        (workdir / "tb2-copy-from").write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"docker cp {quoted_container}:\"$1\" \"$2\"",
            ]),
            encoding="utf-8",
        )
        (workdir / "tb2-copy-to").write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"docker cp \"$1\" {quoted_container}:\"$2\"",
            ]),
            encoding="utf-8",
        )
        (workdir / "tb2-test").write_text(
            "\n".join([
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                f"docker exec {quoted_container} bash /tests/test.sh",
            ]),
            encoding="utf-8",
        )
        for helper in ("tb2-exec", "tb2-copy-from", "tb2-copy-to", "tb2-test"):
            os.chmod(workdir / helper, 0o755)
        (workdir / "PROMPT.txt").write_text(
            f"{_TERMINAL_BENCH_OPENCODE_PROMPT}\n\n--- Task ---\n{task.problem}\n",
            encoding="utf-8",
        )

    def _run_opencode(self, workdir: Path, task_id: str) -> tuple[str, str, int]:
        config_root = workdir / "xdg-config"
        config_dir = config_root / "opencode"
        config_dir.mkdir(parents=True, exist_ok=True)
        provider_options: dict[str, Any] = {
            "apiKey": self._api_key,
            "baseURL": self._api_base,
            "timeout": self._solver_timeout_sec * 1000,
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
            "model": self._model,
            "small_model": self._model,
        }
        (config_dir / "opencode.json").write_text(
            json.dumps(provider_config, indent=2),
            encoding="utf-8",
        )

        env = os.environ.copy()
        env["OPENAI_API_KEY"] = self._api_key
        env["OPENAI_BASE_URL"] = self._api_base
        env["XDG_CONFIG_HOME"] = str(config_root)
        for var in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
            env.pop(var, None)

        prompt = (workdir / "PROMPT.txt").read_text(encoding="utf-8")
        cmd = [
            self._opencode_bin,
            "run",
            "--format",
            "json",
            "--dir",
            str(workdir),
            "--title",
            task_id,
            prompt,
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if self._variant:
            cmd.extend(["--variant", self._variant])
        if self._print_logs:
            cmd.append("--print-logs")
        if self._log_level:
            cmd.extend(["--log-level", self._log_level])

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
            raw_output, stderr = process.communicate(timeout=self._solver_timeout_sec)
            return raw_output, stderr, int(process.returncode or 0)
        except subprocess.TimeoutExpired:
            raw_output = ""
            stderr = f"Timeout after {self._solver_timeout_sec}s"
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
            return raw_output, stderr, -1

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        t_start = time.time()
        docker_image = task.metadata.get("docker_image", "")
        if not docker_image:
            raise ValueError(
                f"Task {task.task_id} missing 'docker_image' in metadata. "
                "Ensure TerminalBench2Benchmark populated task.metadata correctly."
            )

        logs_dir = self._logs_dir_for_task(task)
        test_timeout_sec = self._test_timeout_sec
        container_id = ""
        reward_content = ""
        stderr = ""
        returncode = 0
        raw_output = ""
        session_id = ""
        events: list[dict[str, Any]] = []

        try:
            container_id = self._start_container(docker_image, logs_dir, task)
            with tempfile.TemporaryDirectory(prefix="tb2-opencode-") as tempdir:
                workdir = Path(tempdir)
                self._write_helper_scripts(workdir, container_id, task)
                raw_output, stderr, returncode = self._run_opencode(workdir, task.task_id)

            self._run_tests(container_id, test_timeout_sec)
            reward_content = self._read_reward(logs_dir, task.task_id)
        finally:
            if container_id:
                self._stop_container(container_id, task.task_id)
            self._cleanup_logs_dir(logs_dir)

        content_parts: list[str] = []
        for line in raw_output.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
                events.append(obj)
                if not session_id:
                    session_id = str(obj.get("sessionID", ""))
                    if not session_id and isinstance(obj.get("part"), dict):
                        session_id = str(obj["part"].get("sessionID", ""))
                content_parts.extend(_extract_event_texts(obj))
            except (json.JSONDecodeError, ValueError):
                content_parts.append(stripped)
        full_content = "\n".join(part for part in content_parts if part).strip() or raw_output

        return AgentResponse(
            answer=reward_content,
            trajectory=[
                {"role": "user", "content": task.problem},
                {"role": "assistant", "content": full_content},
            ],
            raw_output=full_content,
            wall_time_sec=time.time() - t_start,
            metadata={
                "docker_image": docker_image,
                "category": task.metadata.get("category", ""),
                "difficulty": task.metadata.get("difficulty", ""),
                "returncode": returncode,
                "stderr": stderr[:2000] if stderr else "",
                "num_events": len(events),
                "session_id": session_id,
            },
            system_prompt=_TERMINAL_BENCH_OPENCODE_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_opencode", TerminalBench2OpenCodeAgent)
