"""Terminal-bench-2 native agent backed by ZeroClaw inside the task container."""

from __future__ import annotations

import logging
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.logprob_capture import finalize_logprob_capture
from alphadiana.agent.logprob_proxy import (
    LogprobCaptureProxy,
    resolve_logprob_proxy_advertise_host,
)
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
from alphadiana.agent.zeroclaw_runtime import _resolve_zeroclaw_provider
from alphadiana.agent.zeroclaw import (
    ZeroClawAgent,
    _classify_cli_error_output,
    _sanitize_cli_output,
    extract_zeroclaw_logprob_records,
)
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


def _config_bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
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
        merged_config.setdefault("runtime_trace_mode", "full")
        merged_config.setdefault("workspace_only", False)
        merged_config.setdefault("security_sandbox_enabled", False)
        super().setup(merged_config)
        self._solver_timeout_sec = solver_timeout_sec
        self._zeroclaw_bin = str(config.get("zeroclaw_bin", "zeroclaw") or "zeroclaw").strip()
        self._runtime_source_image = self._resolve_runtime_source_image(
            config,
            agent_type="zeroclaw",
        )
        normalize_configured = _config_bool(
            config.get("normalize_provider_system_messages"),
            default=False,
        )
        summary_configured = _config_bool(
            config.get("provider_proxy_capture_request_summary"),
            default=False,
        )
        inject_logprobs_configured = _config_bool(
            config.get("provider_proxy_inject_logprobs"),
            default=_config_bool(config.get("capture_logprobs"), default=False),
        )
        upstream_stream_configured = _config_bool(
            config.get("provider_proxy_upstream_stream"),
            default=False,
        )
        proxy_default = (
            normalize_configured
            or summary_configured
            or inject_logprobs_configured
            or upstream_stream_configured
        )
        self._provider_proxy_enabled = _config_bool(
            config.get("provider_proxy_enabled"),
            default=proxy_default,
        )
        self._provider_proxy_normalize_system_messages = _config_bool(
            config.get("normalize_provider_system_messages"),
            default=self._provider_proxy_enabled,
        )
        self._provider_proxy_capture_request_summary = _config_bool(
            config.get("provider_proxy_capture_request_summary"),
            default=self._provider_proxy_enabled,
        )
        self._provider_proxy_inject_logprobs = _config_bool(
            config.get("provider_proxy_inject_logprobs"),
            default=False,
        )
        self._provider_proxy_upstream_stream = _config_bool(
            config.get("provider_proxy_upstream_stream"),
            default=False,
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
        provider_proxy: LogprobCaptureProxy | None = None
        provider_proxy_api_base = self._provider_api_base
        provider_proxy_api_key = self._provider_api_key
        provider_proxy_provider = self._provider
        provider_proxy_metadata: dict[str, Any] = {}
        provider_request_summaries: list[dict[str, Any]] = []
        provider_response_summaries: list[dict[str, Any]] = []
        provider_proxy_logprob_records: list[dict] = []
        provider_request_summary_present = False
        provider_response_summary_present = False

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
        provider_request_summary_path = runtime.workdir / "provider_request_summary.jsonl"
        provider_response_summary_path = runtime.workdir / "provider_response_summary.jsonl"
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
            if self._provider_proxy_enabled:
                proxy_api_key = secrets.token_urlsafe(24)
                advertise_host = resolve_logprob_proxy_advertise_host(
                    self._provider_api_base,
                    self._logprob_proxy_advertise_host,
                )
                provider_proxy = LogprobCaptureProxy(
                    self._provider_api_base,
                    self._logprob_capture["top_logprobs"],
                    bind_host=self._logprob_proxy_bind_host,
                    advertise_host=advertise_host,
                    client_timeout=max(120.0, float(self._solver_timeout_sec + 60)),
                    upstream_api_key=self._provider_api_key,
                    proxy_api_key=proxy_api_key,
                    request_overrides=self._logprob_request_overrides(),
                    inject_logprobs=self._provider_proxy_inject_logprobs,
                    normalize_system_messages=self._provider_proxy_normalize_system_messages,
                    capture_request_summary=self._provider_proxy_capture_request_summary,
                    upstream_stream=self._provider_proxy_upstream_stream,
                )
                provider_proxy.start()
                provider_proxy_api_base = f"{provider_proxy.proxy_url.rstrip('/')}/v1"
                provider_proxy_api_key = proxy_api_key
                provider_proxy_provider = _resolve_zeroclaw_provider(
                    "",
                    provider_proxy_api_base,
                )
                provider_proxy_metadata = {
                    "provider_proxy_enabled": True,
                    "provider_proxy_url": provider_proxy_api_base,
                    "provider_proxy_upstream": provider_proxy.upstream,
                    "provider_proxy_normalize_system_messages": (
                        self._provider_proxy_normalize_system_messages
                    ),
                    "provider_proxy_capture_request_summary": (
                        self._provider_proxy_capture_request_summary
                    ),
                    "provider_proxy_inject_logprobs": self._provider_proxy_inject_logprobs,
                    "provider_proxy_upstream_stream": self._provider_proxy_upstream_stream,
                    "provider_proxy_request_overrides": self._logprob_request_overrides(),
                }
            local_config_path = runtime.workdir / ".zeroclaw-home" / ".zeroclaw" / "config.toml"
            local_config_path.parent.mkdir(parents=True, exist_ok=True)
            local_config_path.write_text(
                self._build_config_toml(provider_api_base=provider_proxy_api_base),
                encoding="utf-8",
            )
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
            if provider_proxy is not None:
                env = dict(env)
                env["OPENAI_BASE_URL"] = provider_proxy_api_base
                env["OPENAI_API_KEY"] = provider_proxy_api_key
                env["OPENROUTER_API_KEY"] = provider_proxy_api_key
                env["ZEROCLAW_API_KEY"] = provider_proxy_api_key
                env["ZEROCLAW_PROVIDER"] = provider_proxy_provider
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
            if provider_proxy is not None:
                provider_proxy_logprob_records.extend(provider_proxy.drain_records())
                provider_proxy_metadata["provider_proxy_logprob_record_count"] = len(
                    provider_proxy_logprob_records
                )
            if provider_proxy is not None and self._provider_proxy_capture_request_summary:
                provider_request_summaries = provider_proxy.drain_request_summaries()
                provider_response_summaries = provider_proxy.drain_response_summaries()
                _write_jsonl(provider_request_summary_path, provider_request_summaries)
                _write_jsonl(provider_response_summary_path, provider_response_summaries)
                provider_proxy_metadata["provider_proxy_request_count"] = len(
                    provider_request_summaries
                )
                provider_proxy_metadata["provider_proxy_response_count"] = len(
                    provider_response_summaries
                )
                provider_proxy_metadata["provider_proxy_normalized_request_count"] = sum(
                    1
                    for summary in provider_request_summaries
                    if summary.get("normalized_system_messages")
                )
                provider_proxy_metadata["provider_proxy_inserted_user_message_count"] = sum(
                    1
                    for summary in provider_request_summaries
                    if summary.get("inserted_user_message")
                )
                provider_proxy_metadata["provider_proxy_empty_content_response_count"] = sum(
                    int(summary.get("empty_content_choice_count") or 0)
                    for summary in provider_response_summaries
                )
                provider_proxy_metadata["provider_proxy_error_response_count"] = sum(
                    1 for summary in provider_response_summaries if summary.get("error_response")
                )
                provider_proxy_metadata[
                    "provider_proxy_empty_content_and_reasoning_response_count"
                ] = sum(
                    int(summary.get("empty_content_and_reasoning_choice_count") or 0)
                    for summary in provider_response_summaries
                )
                provider_proxy_metadata[
                    "provider_proxy_empty_content_reasoning_and_tool_response_count"
                ] = sum(
                    int(summary.get("empty_content_reasoning_and_tool_choice_count") or 0)
                    for summary in provider_response_summaries
                )
            returncode = exec_result.returncode
            raw_output = self._read_container_text(runtime.container_id, remote_stdout_path).strip()
            raw_stderr = (
                self._read_container_text(runtime.container_id, remote_stderr_path).strip()
                or exec_result.stderr.strip()
            )
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
            sanitized_partial_output, _ = _sanitize_cli_output(raw_output)
            solver_failure_reason = ""
            if returncode == 124:
                solver_failure_reason = "timeout"
            elif returncode != 0:
                solver_failure_reason = _classify_cli_error_output(
                    raw_stderr or sanitized_partial_output or raw_output
                )
            partial_extra = {
                "returncode": returncode,
                "container_workdir": container_workdir,
                **runtime_metadata,
                **provider_proxy_metadata,
            }
            if solver_failure_reason:
                partial_extra["failure_reason"] = solver_failure_reason
                partial_extra["zeroclaw_selected_classification"] = solver_failure_reason
            partial_response = self._build_cli_response(
                prompt=prompt_text,
                raw_output=raw_output,
                raw_stderr=raw_stderr,
                runtime_trace=runtime_trace,
                attachment_items=[],
                metadata=self._build_metadata(
                    runtime,
                    reward="",
                    rounds_used=1,
                    runner="zeroclaw",
                    extra=partial_extra,
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
            if provider_proxy is not None:
                try:
                    remaining_logprob_records = provider_proxy.drain_records()
                    if remaining_logprob_records:
                        provider_proxy_logprob_records.extend(remaining_logprob_records)
                    provider_proxy_metadata["provider_proxy_logprob_record_count"] = len(
                        provider_proxy_logprob_records
                    )
                    if (
                        self._provider_proxy_capture_request_summary
                        and not provider_request_summary_path.exists()
                    ):
                        provider_request_summaries = provider_proxy.drain_request_summaries()
                        _write_jsonl(provider_request_summary_path, provider_request_summaries)
                    if (
                        self._provider_proxy_capture_request_summary
                        and not provider_response_summary_path.exists()
                    ):
                        provider_response_summaries = provider_proxy.drain_response_summaries()
                        _write_jsonl(provider_response_summary_path, provider_response_summaries)
                finally:
                    provider_proxy.stop()
            provider_request_summary_present = provider_request_summary_path.exists()
            provider_response_summary_present = provider_response_summary_path.exists()
            artifact_files = self._collect_text_artifacts({
                "/terminal_bench2/zeroclaw/TASK.md": runtime.workdir / "TASK.md",
                "/terminal_bench2/zeroclaw/TASK_HINTS.md": hints_path or runtime.workdir / "TASK_HINTS.md",
                "/terminal_bench2/zeroclaw/PROMPT.txt": runtime.workdir / "PROMPT.txt",
                "/terminal_bench2/zeroclaw/config.toml": runtime.workdir / ".zeroclaw-home" / ".zeroclaw" / "config.toml",
                "/terminal_bench2/zeroclaw/provider_request_summary.jsonl": provider_request_summary_path,
                "/terminal_bench2/zeroclaw/provider_response_summary.jsonl": provider_response_summary_path,
                "/terminal_bench2/zeroclaw/zeroclaw_output.txt": runtime.workdir / "zeroclaw_output.txt",
                "/terminal_bench2/zeroclaw/zeroclaw_stderr.log": runtime.workdir / "zeroclaw_stderr.log",
                "/terminal_bench2/zeroclaw/runtime_trace.jsonl": runtime.workdir / "state" / "runtime-trace.jsonl",
            })
            self._cleanup_runtime(runtime)

        sanitized_output, dropped_runtime_logs = _sanitize_cli_output(raw_output)
        assistant_text = sanitized_output or raw_stderr or runtime_trace
        runtime_records = parse_jsonl_records(runtime_trace)
        empty_assistant_output = not assistant_text.strip()
        solver_failure_reason = ""
        timeout_returncode = returncode == 124
        if timeout_returncode:
            solver_failure_reason = "timeout"
        elif returncode != 0:
            solver_failure_reason = _classify_cli_error_output(
                raw_stderr or sanitized_output or raw_output
            )
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
            provider_request_summary=(
                "provider_request_summary.jsonl"
                if provider_request_summary_present
                else None
            ),
            provider_response_summary=(
                "provider_response_summary.jsonl"
                if provider_response_summary_present
                else None
            ),
        )
        metadata_extra = {
            "returncode": returncode,
            "stderr": raw_stderr[:2000] if raw_stderr else "",
            "test_output": test_output,
            "verifier_status": verifier_result.status,
            "verifier_reward_observed": verifier_result.reward is not None,
            "timed_out": timeout_returncode or returncode == -1,
            "container_workdir": container_workdir,
            **runtime_metadata,
            **provider_proxy_metadata,
        }
        finish_reason = ""
        if timeout_returncode:
            metadata_extra["failure_reason"] = "timeout"
            metadata_extra["zeroclaw_timeout_scored_zero"] = True
            metadata_extra["zeroclaw_timeout_seconds"] = self._request_timeout
            metadata_extra["trajectory_status"] = "abnormal"
            metadata_extra["trajectory_failure_reason"] = "timeout"
            finish_reason = "timeout"
        elif empty_assistant_output:
            metadata_extra["failure_reason"] = "zeroclaw_empty_assistant_output"
            metadata_extra["zeroclaw_empty_assistant_output"] = True
            metadata_extra["trajectory_status"] = "abnormal"
            metadata_extra["trajectory_failure_reason"] = "zeroclaw_empty_assistant_output"
            finish_reason = "agent_empty_output"
        elif solver_failure_reason:
            metadata_extra["failure_reason"] = solver_failure_reason
            metadata_extra["zeroclaw_selected_classification"] = solver_failure_reason
            metadata_extra["trajectory_status"] = "abnormal"
            metadata_extra["trajectory_failure_reason"] = solver_failure_reason

        if dropped_runtime_logs:
            metadata_extra["runtime_logs_dropped_from_output"] = dropped_runtime_logs
        if sanitized_output != raw_output:
            metadata_extra["raw_output_sanitized"] = True
        if returncode != 0:
            metadata_extra["solver_error"] = raw_stderr or f"exit code {returncode}"

        if provider_proxy_logprob_records:
            logprob_records = list(provider_proxy_logprob_records)
            metadata_extra["logprob_source"] = "provider_proxy"
        else:
            logprob_records = extract_zeroclaw_logprob_records(
                runtime_records=runtime_records,
                runtime_trace=runtime_trace,
                raw_output=raw_output,
                raw_stderr=raw_stderr,
            )
            metadata_extra["logprob_source"] = "zeroclaw_artifacts" if logprob_records else ""
        token_entropy_stats, response_metadata = finalize_logprob_capture(
            harness="zeroclaw",
            enabled=self._logprob_capture["enabled"],
            records=logprob_records,
            metadata=metadata_extra,
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
                extra=response_metadata,
            ),
            token_entropy_stats=token_entropy_stats,
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
            finish_reason=finish_reason,
        )
        return response

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_zeroclaw", TerminalBench2ZeroClawAgent)
