"""Terminal-bench-2 native agent backed by OpenCode."""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.opencode import _extract_event_texts
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_common import NATIVE_AGENT_PROMPT, TerminalBench2ContainerMixin
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)


class TerminalBench2OpenCodeAgent(TerminalBench2ContainerMixin, Agent):
    """Control-side OpenCode runner for terminal-bench-2."""

    name = "terminal_bench2_opencode"
    version = "1.0"
    _DEFAULT_CONTROLLER_IMAGE = "alphadiana/tb2-opencode-controller:latest"
    _DEFAULT_DOCKER_ENTRYPOINT = "/usr/lib/node_modules/opencode-ai/bin/opencode"

    def setup(self, config: dict) -> None:
        self._setup_container_config(config)
        self._setup_controller_config(
            config,
            default_mode="docker",
            default_image=self._DEFAULT_CONTROLLER_IMAGE,
        )
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
        self._opencode_bin = str(config.get("opencode_bin", "opencode") or "opencode").strip()
        self._docker_opencode_entrypoint = str(
            config.get("docker_opencode_entrypoint", self._DEFAULT_DOCKER_ENTRYPOINT)
            or self._DEFAULT_DOCKER_ENTRYPOINT
        ).strip()
        self._streaming = config.get("streaming") if "streaming" in config else None

    @staticmethod
    def _resolve_setting(config: dict, key: str, env_var: str) -> str:
        value = str(config.get(key, "") or "").strip()
        if value and value.upper() != "EMPTY":
            return value
        return os.environ.get(env_var, "").strip()

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
            *self._opencode_command_prefix(),
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

        result = self._run_controller_process(
            cmd,
            cwd=workdir,
            env=env,
            timeout_sec=self._solver_timeout_sec,
        )
        return result.stdout, result.stderr, result.returncode

    def _opencode_command_prefix(self) -> list[str]:
        if self._controller_mode == "docker" and self._opencode_bin == "opencode":
            return ["node", self._docker_opencode_entrypoint]
        parts = shlex.split(self._opencode_bin)
        return parts or ["opencode"]

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        t_start = time.time()
        reward_content = ""
        stderr = ""
        returncode = 0
        raw_output = ""
        session_id = ""
        events: list[dict[str, Any]] = []
        test_output = ""
        task_note = self._task_runtime_note(task)
        task_text = task.problem if not task_note else f"{task.problem.rstrip()}\n\n{task_note}\n"
        prompt_text = f"{NATIVE_AGENT_PROMPT}\n\n--- Task ---\n{task_text}\n"
        runtime = self._prepare_runtime(
            task,
            temp_prefix="tb2-opencode-",
            prompt_text=prompt_text,
        )
        self._disable_test_helper(runtime.helper_paths)

        try:
            raw_output, stderr, returncode = self._run_opencode(runtime.workdir, task.task_id)
            (runtime.workdir / "opencode_stdout.log").write_text(
                raw_output,
                encoding="utf-8",
                errors="replace",
            )
            (runtime.workdir / "opencode_stderr.log").write_text(
                stderr,
                encoding="utf-8",
                errors="replace",
            )
            test_output, reward_content = self._run_verifier_and_read_reward(
                runtime,
                timeout_sec=self._test_timeout_sec,
            )
        finally:
            artifact_files = self._collect_text_artifacts({
                "/terminal_bench2/opencode/TASK.md": runtime.helper_paths["task"],
                "/terminal_bench2/opencode/TASK_HINTS.md": runtime.helper_paths["task_hints"],
                "/terminal_bench2/opencode/AGENTS.md": runtime.helper_paths["agents"],
                "/terminal_bench2/opencode/PROMPT.txt": runtime.helper_paths["prompt"],
                "/terminal_bench2/opencode/opencode_stdout.log": runtime.workdir / "opencode_stdout.log",
                "/terminal_bench2/opencode/opencode_stderr.log": runtime.workdir / "opencode_stderr.log",
                "/terminal_bench2/opencode/xdg-config/opencode/opencode.json": runtime.workdir / "xdg-config" / "opencode" / "opencode.json",
            })
            self._cleanup_runtime(runtime)

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
            metadata=self._build_metadata(
                runtime,
                reward=reward_content,
                rounds_used=1,
                runner="opencode",
                extra={
                    "returncode": returncode,
                    "stderr": stderr[:2000] if stderr else "",
                    "num_events": len(events),
                    "session_id": session_id,
                    "test_output": test_output,
                },
            ),
            request_messages=[{"role": "user", "content": prompt_text}],
            response_json={"events": events} if events else {},
            workspace_file_contents=artifact_files,
            system_prompt=NATIVE_AGENT_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_opencode", TerminalBench2OpenCodeAgent)
