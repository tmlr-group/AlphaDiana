"""Terminal-bench-2 native agent backed by ZeroClaw."""

from __future__ import annotations

import logging
import os
import shlex
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.base import AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_common import (
    AGENT_GUIDANCE,
    NATIVE_AGENT_PROMPT,
    TerminalBench2ContainerMixin,
)
from alphadiana.agent.zeroclaw import ZeroClawAgent
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)

_ZEROCLAW_AGENT_GUIDANCE = (
    f"{AGENT_GUIDANCE}\n"
    "Use ZeroClaw from the local control workspace only.\n"
    "Do not invoke docker directly even if the controller image has it installed.\n"
)


class TerminalBench2ZeroClawAgent(TerminalBench2ContainerMixin, ZeroClawAgent):
    """Control-side ZeroClaw runner for terminal-bench-2."""

    name = "terminal_bench2_zeroclaw"
    version = "0.6.9"
    _DEFAULT_CONTROLLER_IMAGE = "alphadiana/tb2-zeroclaw-controller:latest"

    def setup(self, config: dict) -> None:
        self._setup_container_config(config)
        self._setup_controller_config(
            config,
            default_mode="docker",
            default_image=self._DEFAULT_CONTROLLER_IMAGE,
        )
        merged_config = dict(config)
        model_name = str(merged_config.get("model_name", "") or "").strip()
        if model_name and not str(merged_config.get("model", "") or "").strip():
            merged_config["model"] = model_name
        merged_config.setdefault("system_prompt", NATIVE_AGENT_PROMPT)
        super().setup(merged_config)
        self._solver_timeout_sec = int(config.get("solver_timeout_sec", config.get("timeout", 1800)))
        self._zeroclaw_bin = str(config.get("zeroclaw_bin", "zeroclaw") or "zeroclaw").strip()

    def _zeroclaw_command_prefix(self) -> list[str]:
        parts = shlex.split(self._zeroclaw_bin)
        return parts or ["zeroclaw"]

    def _build_runtime_prompt(self, task: BenchmarkTask) -> tuple[str, str]:
        task_note = self._task_runtime_note(task)
        task_text = task.problem if not task_note else f"{task.problem.rstrip()}\n\n{task_note}\n"
        prompt_text = f"{NATIVE_AGENT_PROMPT}\n\n--- Task ---\n{task_text}\n"
        return task_text, prompt_text

    def _prepare_zeroclaw_home(self, workdir: Path, prompt_text: str) -> tuple[dict[str, str], dict[str, str]]:
        home_dir = workdir / ".zeroclaw-home"
        zc_home = home_dir / ".zeroclaw"
        state_dir = workdir / "state"
        stdout_path = workdir / "zeroclaw_output.txt"
        stderr_path = workdir / "zeroclaw_stderr.log"
        controller_stdout_path = workdir / "controller_stdout.log"
        controller_stderr_path = workdir / "controller_stderr.log"
        home_dir.mkdir(parents=True, exist_ok=True)
        zc_home.mkdir(parents=True, exist_ok=True)
        state_dir.mkdir(parents=True, exist_ok=True)
        workspace_link = zc_home / "workspace"
        if workspace_link.exists() or workspace_link.is_symlink():
            workspace_link.unlink()
        workspace_link.symlink_to(workdir)
        (zc_home / "config.toml").write_text(self._build_config_toml(), encoding="utf-8")
        prompt_path = workdir / "PROMPT.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")

        env = os.environ.copy()
        env.update(self._build_env(str(home_dir)))
        for var in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
            env.pop(var, None)
        return env, {
            "home_dir": str(home_dir),
            "config_path": str(zc_home / "config.toml"),
            "prompt_path": str(prompt_path),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "runtime_trace_path": str(state_dir / "runtime-trace.jsonl"),
            "controller_stdout_path": str(controller_stdout_path),
            "controller_stderr_path": str(controller_stderr_path),
        }

    def _run_zeroclaw(self, workdir: Path, env: dict[str, str], paths: dict[str, str]) -> tuple[str, str, int]:
        run_script = (
            f"cd {shlex.quote(str(workdir))} && "
            f"prompt=$(cat {shlex.quote(paths['prompt_path'])}) && "
            f"{shlex.join(self._zeroclaw_command_prefix())} agent -m \"$prompt\" "
            f"> {shlex.quote(paths['stdout_path'])} "
            f"2> {shlex.quote(paths['stderr_path'])}"
        )
        result = self._run_controller_process(
            ["bash", "-lc", run_script],
            cwd=workdir,
            env=env,
            timeout_sec=self._solver_timeout_sec + 30,
        )
        return result.stdout, result.stderr, result.returncode

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        del sandbox
        t_start = time.time()
        reward_content = ""
        test_output = ""
        raw_output = ""
        raw_stderr = ""
        runtime_trace = ""
        controller_stdout = ""
        controller_stderr = ""
        returncode = 0

        task_text, prompt_text = self._build_runtime_prompt(task)
        request_messages = [
            {"role": "system", "content": NATIVE_AGENT_PROMPT},
            {"role": "user", "content": task_text},
        ]
        runtime = self._prepare_runtime(
            task,
            temp_prefix="tb2-zeroclaw-",
            prompt_text=prompt_text,
            agent_guidance=_ZEROCLAW_AGENT_GUIDANCE,
        )
        self._disable_test_helper(runtime.helper_paths)

        try:
            env, paths = self._prepare_zeroclaw_home(runtime.workdir, prompt_text)
            controller_stdout, controller_stderr, returncode = self._run_zeroclaw(
                runtime.workdir,
                env,
                paths,
            )
            Path(paths["controller_stdout_path"]).write_text(
                controller_stdout,
                encoding="utf-8",
                errors="replace",
            )
            Path(paths["controller_stderr_path"]).write_text(
                controller_stderr,
                encoding="utf-8",
                errors="replace",
            )
            raw_output = Path(paths["stdout_path"]).read_text(encoding="utf-8", errors="replace").strip() if Path(paths["stdout_path"]).exists() else ""
            raw_stderr = Path(paths["stderr_path"]).read_text(encoding="utf-8", errors="replace").strip() if Path(paths["stderr_path"]).exists() else ""
            runtime_trace = Path(paths["runtime_trace_path"]).read_text(encoding="utf-8", errors="replace").strip() if Path(paths["runtime_trace_path"]).exists() else ""
            partial_response = self._build_cli_response(
                prompt=prompt_text,
                raw_output=raw_output,
                raw_stderr=raw_stderr or controller_stderr,
                runtime_trace=runtime_trace,
                attachment_items=[],
                metadata=self._build_metadata(
                    runtime,
                    reward="",
                    rounds_used=1,
                    runner="zeroclaw",
                    extra={
                        "returncode": returncode,
                        "controller_stderr": controller_stderr[:2000] if controller_stderr else "",
                    },
                ),
                wall_time_sec=time.time() - t_start,
                system_prompt=NATIVE_AGENT_PROMPT,
                artifact_manifest={"files": {"runtime_trace_source": paths["runtime_trace_path"]}},
                request_messages=request_messages,
            )
            try:
                test_output, reward_content = self._run_verifier_and_read_reward(
                    runtime,
                    timeout_sec=self._test_timeout_sec,
                )
            except Exception as exc:
                partial_response.metadata["test_output"] = test_output
                self._raise_with_partial_response(f"terminal-bench-2 verifier failed: {exc}", partial_response)
        finally:
            artifact_files = self._collect_text_artifacts({
                "/terminal_bench2/zeroclaw/TASK.md": runtime.helper_paths["task"],
                "/terminal_bench2/zeroclaw/TASK_HINTS.md": runtime.helper_paths["task_hints"],
                "/terminal_bench2/zeroclaw/AGENTS.md": runtime.helper_paths["agents"],
                "/terminal_bench2/zeroclaw/PROMPT.txt": runtime.helper_paths["prompt"],
                "/terminal_bench2/zeroclaw/config.toml": runtime.workdir / ".zeroclaw-home" / ".zeroclaw" / "config.toml",
                "/terminal_bench2/zeroclaw/zeroclaw_output.txt": runtime.workdir / "zeroclaw_output.txt",
                "/terminal_bench2/zeroclaw/zeroclaw_stderr.log": runtime.workdir / "zeroclaw_stderr.log",
                "/terminal_bench2/zeroclaw/runtime_trace.jsonl": runtime.workdir / "state" / "runtime-trace.jsonl",
                "/terminal_bench2/zeroclaw/controller_stdout.log": runtime.workdir / "controller_stdout.log",
                "/terminal_bench2/zeroclaw/controller_stderr.log": runtime.workdir / "controller_stderr.log",
            })
            self._cleanup_runtime(runtime)

        assistant_text = raw_output or raw_stderr or runtime_trace or controller_stderr or controller_stdout
        response = AgentResponse(
            answer=reward_content,
            trajectory=[
                {"role": "user", "content": prompt_text},
                {"role": "assistant", "content": assistant_text},
            ],
            raw_output=assistant_text,
            wall_time_sec=time.time() - t_start,
            metadata=self._build_metadata(
                runtime,
                reward=reward_content,
                rounds_used=1,
                runner="zeroclaw",
                extra={
                    "returncode": returncode,
                    "stderr": (raw_stderr or controller_stderr)[:2000] if (raw_stderr or controller_stderr) else "",
                    "test_output": test_output,
                    "timed_out": returncode == -1,
                },
            ),
            request_messages=request_messages,
            workspace_file_contents=artifact_files,
            system_prompt=NATIVE_AGENT_PROMPT,
        )
        if returncode != 0:
            response.metadata["solver_error"] = raw_stderr or controller_stderr or f"exit code {returncode}"
        return response

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_zeroclaw", TerminalBench2ZeroClawAgent)
