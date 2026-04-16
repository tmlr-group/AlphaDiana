"""Terminal-bench-2 native agent backed by the OpenClaw CLI."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.openclaw import (
    _extract_trajectory_error,
    _parse_openclaw_session,
    _recover_partial_output_from_trajectory,
)
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_common import (
    AGENT_GUIDANCE,
    NATIVE_AGENT_PROMPT,
    TerminalBench2ContainerMixin,
)
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)

_OPENCLAW_AGENT_GUIDANCE = (
    f"{AGENT_GUIDANCE}\n"
    "For this benchmark, run the helper scripts from the local control workspace.\n"
    "Do not use any direct docker commands even if they are available.\n"
)


class TerminalBench2OpenClawAgent(TerminalBench2ContainerMixin, Agent):
    """Control-side OpenClaw runner for terminal-bench-2."""

    name = "terminal_bench2_openclaw"
    version = "1.0"
    _DEFAULT_CONTROLLER_IMAGE = "alphadiana/tb2-openclaw-controller:latest"

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
        if not self._model_name:
            fallback_model = str(config.get("model", "") or "").strip()
            if fallback_model and fallback_model.lower() != "openclaw":
                self._model_name = fallback_model
        self._solver_timeout_sec = int(config.get("solver_timeout_sec", config.get("timeout", 1800)))
        self._onboard_timeout_sec = int(config.get("onboard_timeout_sec", min(self._solver_timeout_sec, 180)))
        self._openclaw_bin = str(config.get("openclaw_bin", "openclaw") or "openclaw").strip()
        self._thinking = str(config.get("thinking", "") or "").strip()
        verbose = str(config.get("verbose", "on") or "on").strip().lower()
        verbose_aliases = {
            "normal": "on",
            "debug": "full",
            "quiet": "off",
        }
        self._verbose = verbose_aliases.get(verbose, verbose)
        if self._verbose not in {"", "off", "on", "full"}:
            self._verbose = "on"

    @staticmethod
    def _resolve_setting(config: dict, key: str, env_var: str) -> str:
        value = str(config.get(key, "") or "").strip()
        if value and value.upper() != "EMPTY":
            return value
        return os.environ.get(env_var, "").strip()

    def _build_env(self, workdir: Path) -> tuple[dict[str, str], Path]:
        openclaw_home = workdir / ".openclaw-home"
        plugins_dir = workdir / ".openclaw-empty-bundled"
        openclaw_home.mkdir(parents=True, exist_ok=True)
        plugins_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["OPENCLAW_HOME"] = str(openclaw_home)
        env["OPENCLAW_BUNDLED_PLUGINS_DIR"] = str(plugins_dir)
        env["OPENAI_API_KEY"] = self._api_key
        env["OPENAI_BASE_URL"] = self._api_base
        env["OPENAI_MODEL_NAME"] = self._model_name
        env["CUSTOM_API_KEY"] = self._api_key
        for var in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
            env.pop(var, None)
        return env, openclaw_home

    def _run_onboard(self, workdir: Path, env: dict[str, str]) -> tuple[str, str, int]:
        cmd = [
            self._openclaw_bin,
            "onboard",
            "--non-interactive",
            "--flow",
            "quickstart",
            "--mode",
            "local",
            "--workspace",
            str(workdir),
            "--auth-choice",
            "custom-api-key",
            "--custom-base-url",
            self._api_base,
            "--custom-model-id",
            self._model_name,
            "--custom-api-key",
            self._api_key,
            "--secret-input-mode",
            "plaintext",
            "--custom-compatibility",
            "openai",
            "--skip-health",
            "--accept-risk",
            "--json",
        ]
        result = self._run_controller_process(
            cmd,
            cwd=workdir,
            env=env,
            timeout_sec=self._onboard_timeout_sec,
        )
        return result.stdout, result.stderr, result.returncode

    def _run_openclaw_agent(
        self,
        workdir: Path,
        env: dict[str, str],
        prompt: str,
    ) -> tuple[str, str, int]:
        cmd = [
            self._openclaw_bin,
            "agent",
            "--agent",
            "main",
            "--local",
            "--json",
            "--timeout",
            str(self._solver_timeout_sec),
            "--message",
            prompt,
        ]
        if self._thinking:
            cmd.extend(["--thinking", self._thinking])
        if self._verbose:
            cmd.extend(["--verbose", self._verbose])
        result = self._run_controller_process(
            cmd,
            cwd=workdir,
            env=env,
            timeout_sec=self._solver_timeout_sec + 30,
        )
        return result.stdout, result.stderr, result.returncode

    @staticmethod
    def _load_json_output(raw_output: str) -> dict[str, Any]:
        for line in reversed([line.strip() for line in raw_output.splitlines() if line.strip()]):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    @staticmethod
    def _latest_session_file(openclaw_home: Path) -> Path | None:
        session_files = sorted(
            openclaw_home.glob("agents/*/sessions/*.jsonl"),
            key=lambda path: path.stat().st_mtime,
        )
        return session_files[-1] if session_files else None

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        del sandbox
        t_start = time.time()
        reward_content = ""
        task_note = self._task_runtime_note(task)
        task_text = task.problem if not task_note else f"{task.problem.rstrip()}\n\n{task_note}\n"
        prompt_text = f"{NATIVE_AGENT_PROMPT}\n\n--- Task ---\n{task_text}\n"
        test_output = ""
        onboard_stdout = ""
        onboard_stderr = ""
        onboard_returncode = 0
        agent_stdout = ""
        agent_stderr = ""
        agent_returncode = 0
        session_id = ""
        trajectory_error = ""
        response_json: dict[str, Any] = {}
        assistant_text = ""
        session_file: Path | None = None

        runtime = self._prepare_runtime(
            task,
            temp_prefix="tb2-openclaw-",
            prompt_text=prompt_text,
            agent_guidance=_OPENCLAW_AGENT_GUIDANCE,
        )
        self._disable_test_helper(runtime.helper_paths)

        try:
            env, openclaw_home = self._build_env(runtime.workdir)
            onboard_stdout, onboard_stderr, onboard_returncode = self._run_onboard(runtime.workdir, env)
            (runtime.workdir / "openclaw_onboard_stdout.log").write_text(
                onboard_stdout,
                encoding="utf-8",
                errors="replace",
            )
            (runtime.workdir / "openclaw_onboard_stderr.log").write_text(
                onboard_stderr,
                encoding="utf-8",
                errors="replace",
            )
            if onboard_returncode != 0:
                detail = onboard_stderr.strip() or onboard_stdout.strip() or "unknown error"
                raise RuntimeError(f"OpenClaw onboard failed: {detail}")

            self._write_helper_scripts(
                runtime.workdir,
                runtime.container_id,
                task,
                prompt_text=prompt_text,
                agent_guidance=_OPENCLAW_AGENT_GUIDANCE,
                task_note=self._task_runtime_note(task, runtime.workdir),
            )
            self._disable_test_helper(runtime.helper_paths)

            agent_stdout, agent_stderr, agent_returncode = self._run_openclaw_agent(
                runtime.workdir,
                env,
                prompt_text,
            )
            (runtime.workdir / "openclaw_stdout.log").write_text(
                agent_stdout,
                encoding="utf-8",
                errors="replace",
            )
            (runtime.workdir / "openclaw_stderr.log").write_text(
                agent_stderr,
                encoding="utf-8",
                errors="replace",
            )

            response_json = self._load_json_output(agent_stdout)
            session_file = self._latest_session_file(openclaw_home)
            trajectory: list[dict[str, Any]] = []
            if session_file is not None:
                session_id = session_file.stem
                session_text = session_file.read_text(encoding="utf-8", errors="replace")
                trajectory = _parse_openclaw_session(session_text)
                assistant_text, _ = _recover_partial_output_from_trajectory(trajectory)
                trajectory_error = _extract_trajectory_error(trajectory)
            if not assistant_text:
                assistant_text = agent_stdout.strip()

            test_output, reward_content = self._run_verifier_and_read_reward(
                runtime,
                timeout_sec=self._test_timeout_sec,
            )
        finally:
            artifact_paths = {
                "/terminal_bench2/openclaw/TASK.md": runtime.helper_paths["task"],
                "/terminal_bench2/openclaw/TASK_HINTS.md": runtime.helper_paths["task_hints"],
                "/terminal_bench2/openclaw/AGENTS.md": runtime.helper_paths["agents"],
                "/terminal_bench2/openclaw/PROMPT.txt": runtime.helper_paths["prompt"],
                "/terminal_bench2/openclaw/openclaw_onboard_stdout.log": runtime.workdir / "openclaw_onboard_stdout.log",
                "/terminal_bench2/openclaw/openclaw_onboard_stderr.log": runtime.workdir / "openclaw_onboard_stderr.log",
                "/terminal_bench2/openclaw/openclaw_stdout.log": runtime.workdir / "openclaw_stdout.log",
                "/terminal_bench2/openclaw/openclaw_stderr.log": runtime.workdir / "openclaw_stderr.log",
            }
            if session_file is not None:
                artifact_paths["/terminal_bench2/openclaw/session.jsonl"] = session_file
            artifact_files = self._collect_text_artifacts(artifact_paths)
            self._cleanup_runtime(runtime)

        trajectory = []
        if session_file is not None and "/terminal_bench2/openclaw/session.jsonl" in artifact_files:
            trajectory = _parse_openclaw_session(
                artifact_files["/terminal_bench2/openclaw/session.jsonl"]
            )
        if not trajectory:
            trajectory = [
                {"role": "user", "content": prompt_text},
                {"role": "assistant", "content": assistant_text},
            ]

        return AgentResponse(
            answer=reward_content,
            trajectory=trajectory,
            raw_output=assistant_text or agent_stdout,
            wall_time_sec=time.time() - t_start,
            metadata=self._build_metadata(
                runtime,
                reward=reward_content,
                rounds_used=1,
                runner="openclaw",
                extra={
                    "returncode": agent_returncode,
                    "onboard_returncode": onboard_returncode,
                    "stderr": agent_stderr[:2000] if agent_stderr else "",
                    "session_id": session_id,
                    "trajectory_error": trajectory_error,
                    "test_output": test_output,
                },
            ),
            request_messages=[{"role": "user", "content": prompt_text}],
            response_json=response_json,
            workspace_file_contents=artifact_files,
            system_prompt=NATIVE_AGENT_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_openclaw", TerminalBench2OpenClawAgent)
