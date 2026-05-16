"""Terminal-bench-2 native agent backed by ZeroClaw inside the task container."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.preservation import (
    add_artifact_file_refs,
    build_event_trajectories,
    build_text_step_trajectories,
    build_runtime_trace_summary,
    parse_jsonl_records,
)
from alphadiana.agent.base import AgentResponse
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_incontainer import (
    IN_CONTAINER_AGENT_PROMPT,
    TerminalBench2InContainerMixin,
)
from alphadiana.agent.zeroclaw import ZeroClawAgent, _sanitize_cli_output
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)

_ZEROCLAW_SYSTEM_PROMPT = (
    f"{IN_CONTAINER_AGENT_PROMPT}\n\n"
    "Important runtime contract:\n"
    "- You are already inside the target task container.\n"
    "- Work directly on the live filesystem visible in the current shell.\n"
    "- Paths like `/app/...` are real task paths, not proxy paths.\n"
    "- Do not wait for helper scripts or a separate control workspace.\n"
)


class TerminalBench2ZeroClawAgent(TerminalBench2InContainerMixin, ZeroClawAgent):
    """In-container ZeroClaw runner for terminal-bench-2."""

    name = "terminal_bench2_zeroclaw"
    version = "0.6.9"

    def setup(self, config: dict) -> None:
        self._setup_container_config(config)
        solver_timeout_sec = int(config.get("solver_timeout_sec", config.get("timeout", 1800)))
        merged_config = dict(config)
        model_name = str(merged_config.get("model_name", "") or "").strip()
        if model_name and not str(merged_config.get("model", "") or "").strip():
            merged_config["model"] = model_name
        if merged_config.get("request_timeout") in ("", None):
            merged_config["request_timeout"] = solver_timeout_sec
        merged_config.setdefault("system_prompt", _ZEROCLAW_SYSTEM_PROMPT)
        merged_config.setdefault("workspace_only", False)
        merged_config.setdefault("security_sandbox_enabled", False)
        super().setup(merged_config)
        self._solver_timeout_sec = solver_timeout_sec
        self._zeroclaw_bin = str(config.get("zeroclaw_bin", "zeroclaw") or "zeroclaw").strip()
        self._runtime_source_image = self._resolve_runtime_source_image(
            config,
            agent_type="zeroclaw",
        )

    def _build_tb2_env(self, remote_home: str, remote_zc_home: str) -> dict[str, str]:
        local_dir = f"{remote_home}/.local"
        return self._build_env({
            "home_dir": remote_home,
            "zc_home_dir": remote_zc_home,
            "xdg_config_home": f"{remote_home}/.config",
            "xdg_cache_home": f"{remote_home}/.cache",
            "xdg_data_home": f"{local_dir}/share",
            "xdg_state_home": f"{local_dir}/state",
        })

    def _build_runtime_prompt(self, task: BenchmarkTask) -> tuple[str, str]:
        return self._build_incontainer_prompt(task)

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        del sandbox
        t_start = time.time()
        reward_content = ""
        test_output = ""
        raw_output = ""
        raw_stderr = ""
        runtime_trace = ""
        returncode = 0

        task_text, prompt_text = self._build_runtime_prompt(task)
        request_messages = [
            {"role": "system", "content": _ZEROCLAW_SYSTEM_PROMPT},
            {"role": "user", "content": task_text},
        ]
        runtime, runtime_metadata = self._prepare_incontainer_runtime(
            task,
            agent_type="zeroclaw",
            runtime_source_image=self._runtime_source_image,
            temp_prefix="tb2-zeroclaw-",
        )
        remote_root = self._build_remote_root(task, agent_name="zeroclaw")
        remote_home = f"{remote_root}/home"
        remote_zc_home = f"{remote_home}/.zeroclaw"
        remote_state_dir = f"{remote_root}/state"
        remote_prompt_path = f"{remote_root}/task.txt"
        remote_config_path = f"{remote_zc_home}/config.toml"
        remote_stdout_path = f"{remote_root}/zeroclaw_output.txt"
        remote_stderr_path = f"{remote_root}/zeroclaw_stderr.log"
        remote_runtime_trace_path = f"{remote_state_dir}/runtime-trace.jsonl"
        prompt_path = runtime.workdir / "PROMPT.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        (runtime.workdir / "TASK.md").write_text(task_text, encoding="utf-8")
        task_note = self._build_incontainer_task_note(task)
        hints_path: Path | None = None
        if task_note:
            hints_path = runtime.workdir / "TASK_HINTS.md"
            hints_path.write_text(task_note + "\n", encoding="utf-8")
        container_workdir = "/"

        try:
            container_workdir = self._detect_container_workspace(runtime.container_id)
            local_config_path = runtime.workdir / ".zeroclaw-home" / ".zeroclaw" / "config.toml"
            local_config_path.parent.mkdir(parents=True, exist_ok=True)
            local_config_path.write_text(self._build_config_toml(), encoding="utf-8")
            os.chmod(local_config_path, 0o600)
            self._stage_file_into_container(
                runtime.container_id,
                local_path=prompt_path,
                remote_path=remote_prompt_path,
            )
            self._stage_file_into_container(
                runtime.container_id,
                local_path=local_config_path,
                remote_path=remote_config_path,
            )
            prep_result = self._docker_exec_capture(
                runtime.container_id,
                (
                    f"mkdir -p {remote_zc_home} {remote_state_dir}\n"
                    f"ln -sfn {container_workdir} {remote_zc_home}/workspace\n"
                    f"chmod 600 {remote_config_path}"
                ),
                timeout_sec=30,
            )
            if prep_result.returncode != 0:
                detail = prep_result.stderr.strip() or prep_result.stdout.strip() or "unknown error"
                raise RuntimeError(f"Failed to prepare ZeroClaw runtime: {detail}")
            env = self._build_tb2_env(remote_home, remote_zc_home)
            paths = {
                "workspace_dir": container_workdir,
                "zc_home_dir": remote_zc_home,
                "session_state_path": f"{remote_state_dir}/zeroclaw-session-state.json",
                "task_path": remote_prompt_path,
                "stdout_path": remote_stdout_path,
                "stderr_path": remote_stderr_path,
            }
            exec_result = self._docker_exec_capture(
                runtime.container_id,
                self._build_run_command(paths),
                env=env,
                cwd=container_workdir,
                timeout_sec=self._solver_timeout_sec + 60,
            )
            returncode = exec_result.returncode
            raw_output = self._read_container_text(runtime.container_id, remote_stdout_path).strip()
            raw_stderr = self._read_container_text(runtime.container_id, remote_stderr_path).strip()
            runtime_trace = self._read_container_text(
                runtime.container_id,
                remote_runtime_trace_path,
            ).strip()
            (runtime.workdir / "zeroclaw_output.txt").write_text(
                raw_output,
                encoding="utf-8",
                errors="replace",
            )
            (runtime.workdir / "zeroclaw_stderr.log").write_text(
                raw_stderr or exec_result.stderr,
                encoding="utf-8",
                errors="replace",
            )
            (runtime.workdir / "state").mkdir(parents=True, exist_ok=True)
            (runtime.workdir / "state" / "runtime-trace.jsonl").write_text(
                runtime_trace,
                encoding="utf-8",
                errors="replace",
            )
            partial_response = self._build_cli_response(
                prompt=prompt_text,
                raw_output=raw_output,
                raw_stderr=raw_stderr or exec_result.stderr,
                runtime_trace=runtime_trace,
                attachment_items=[],
                metadata=self._build_metadata(
                    runtime,
                    reward="",
                    rounds_used=1,
                    runner="zeroclaw",
                    extra={
                        "returncode": returncode,
                        "container_workdir": container_workdir,
                        **runtime_metadata,
                    },
                ),
                wall_time_sec=time.time() - t_start,
                system_prompt=_ZEROCLAW_SYSTEM_PROMPT,
                artifact_manifest={"files": {"runtime_trace_source": remote_runtime_trace_path}},
                request_messages=request_messages,
            )
            try:
                verifier_result = self._run_verifier_and_read_reward(
                    runtime,
                    timeout_sec=self._test_timeout_sec,
                )
                test_output = verifier_result.test_output
                reward_content = verifier_result.reward
            except Exception as exc:
                partial_response.metadata["test_output"] = test_output
                self._raise_with_partial_response(f"terminal-bench-2 verifier failed: {exc}", partial_response)
        finally:
            artifact_files = self._collect_text_artifacts({
                "/terminal_bench2/zeroclaw/TASK.md": runtime.workdir / "TASK.md",
                "/terminal_bench2/zeroclaw/TASK_HINTS.md": hints_path or runtime.workdir / "TASK_HINTS.md",
                "/terminal_bench2/zeroclaw/PROMPT.txt": runtime.workdir / "PROMPT.txt",
                "/terminal_bench2/zeroclaw/config.toml": runtime.workdir / ".zeroclaw-home" / ".zeroclaw" / "config.toml",
                "/terminal_bench2/zeroclaw/zeroclaw_output.txt": runtime.workdir / "zeroclaw_output.txt",
                "/terminal_bench2/zeroclaw/zeroclaw_stderr.log": runtime.workdir / "zeroclaw_stderr.log",
                "/terminal_bench2/zeroclaw/runtime_trace.jsonl": runtime.workdir / "state" / "runtime-trace.jsonl",
            })
            self._cleanup_runtime(runtime)

        sanitized_output, dropped_runtime_logs = _sanitize_cli_output(raw_output)
        assistant_text = sanitized_output or raw_stderr or runtime_trace
        runtime_records = parse_jsonl_records(runtime_trace)
        trajectory, reasoning_trajectory = build_event_trajectories(
            request_messages,
            runtime_records,
            final_output=assistant_text,
        )
        if not runtime_records:
            fallback_trajectory, fallback_reasoning = build_text_step_trajectories(
                request_messages,
                assistant_text,
            )
            if len(fallback_trajectory) > len(trajectory):
                trajectory = fallback_trajectory
            if len(fallback_reasoning) > len(reasoning_trajectory):
                reasoning_trajectory = fallback_reasoning
        if not trajectory:
            trajectory = [
                {"role": "user", "content": prompt_text},
                {"role": "assistant", "content": assistant_text},
            ]
        artifact_manifest = add_artifact_file_refs(
            {},
            response_stream=(
                "runtime_trace.jsonl"
                if runtime_trace
                else ("zeroclaw_output.txt" if raw_output else None)
            ),
            stdout_log="zeroclaw_output.txt" if raw_output else None,
            stderr_log="zeroclaw_stderr.log" if (raw_stderr or returncode != 0) else None,
            prompt_text="PROMPT.txt",
            config_path="config.toml",
        )
        response = AgentResponse(
            answer=reward_content,
            trajectory=trajectory,
            reasoning_trajectory=reasoning_trajectory,
            raw_output=assistant_text,
            wall_time_sec=time.time() - t_start,
            metadata=self._build_metadata(
                runtime,
                reward=reward_content,
                rounds_used=1,
                runner="zeroclaw",
                extra={
                    "returncode": returncode,
                    "stderr": raw_stderr[:2000] if raw_stderr else "",
                    "test_output": test_output,
                    "verifier_status": verifier_result.status,
                    "verifier_reward_observed": verifier_result.reward is not None,
                    "timed_out": returncode == -1,
                    "container_workdir": container_workdir,
                    **runtime_metadata,
                },
            ),
            request_messages=request_messages,
            response_json=build_runtime_trace_summary(
                output_text=sanitized_output or assistant_text,
                stderr_text=raw_stderr.strip(),
                records=runtime_records,
                extra={
                    "returncode": returncode,
                    "runtime_trace_present": bool(runtime_trace.strip()),
                    "runtime_trace_records": len(runtime_records),
                    "container_workdir": container_workdir,
                },
            ),
            artifact_manifest=artifact_manifest,
            workspace_file_contents=artifact_files,
            system_prompt=_ZEROCLAW_SYSTEM_PROMPT,
        )
        if dropped_runtime_logs:
            response.metadata["runtime_logs_dropped_from_output"] = dropped_runtime_logs
        if sanitized_output != raw_output:
            response.metadata["raw_output_sanitized"] = True
        if returncode != 0:
            response.metadata["solver_error"] = raw_stderr or f"exit code {returncode}"
        return response

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_zeroclaw", TerminalBench2ZeroClawAgent)
