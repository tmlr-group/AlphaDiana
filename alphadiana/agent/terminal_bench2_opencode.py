"""Terminal-bench-2 native agent backed by OpenCode inside the task container."""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.logprob_capture import (
    apply_openai_logprob_request,
    finalize_logprob_capture,
    resolve_logprob_capture_config,
)
from alphadiana.agent.logprob_proxy import (
    LogprobCaptureProxy,
    normalize_openai_proxy_upstream,
    resolve_logprob_proxy_advertise_host,
)
from alphadiana.agent.opencode import (
    _count_json_objects,
    _extract_event_texts,
    extract_opencode_logprob_records,
)
from alphadiana.agent.preservation import (
    add_artifact_file_refs,
    build_event_trajectories,
    build_runtime_trace_summary,
    parse_jsonl_records,
)
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_incontainer import (
    IN_CONTAINER_AGENT_PROMPT,
    TerminalBench2InContainerMixin,
)
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)


def _jsonl_from_stdout(raw_output: str) -> str:
    lines: list[str] = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            continue
        lines.append(stripped)
    return "".join(f"{line}\n" for line in lines)


class TerminalBench2OpenCodeAgent(TerminalBench2InContainerMixin, Agent):
    """In-container OpenCode runner for terminal-bench-2."""

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
        self._opencode_bin = str(config.get("opencode_bin", "opencode") or "opencode").strip()
        raw_temperature = config.get("temperature", None)
        self._temperature = (
            None if raw_temperature in (None, "") else float(raw_temperature)
        )
        raw_top_p = config.get("top_p", None)
        self._top_p = None if raw_top_p in (None, "") else float(raw_top_p)
        raw_max_tokens = config.get("max_tokens", None)
        self._max_tokens = None if raw_max_tokens in (None, "") else int(raw_max_tokens)
        self._streaming = config.get("streaming") if "streaming" in config else None
        self._logprob_capture = resolve_logprob_capture_config(config)
        self._logprob_proxy_bind_host = str(
            config.get("logprob_proxy_bind_host", "0.0.0.0") or "0.0.0.0"
        ).strip()
        self._logprob_proxy_advertise_host = str(
            config.get("logprob_proxy_advertise_host", config.get("logprob_proxy_host", ""))
            or ""
        ).strip()
        self._runtime_source_image = self._resolve_runtime_source_image(
            config,
            agent_type="opencode",
        )

    @staticmethod
    def _resolve_setting(config: dict, key: str, env_var: str) -> str:
        value = str(config.get(key, "") or "").strip()
        if value and value.upper() != "EMPTY":
            return value
        return os.environ.get(env_var, "").strip()

    def _opencode_command_prefix(self) -> list[str]:
        parts = shlex.split(self._opencode_bin)
        return parts or ["opencode"]

    def _provider_request_overrides(self) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        if self._temperature is not None:
            overrides["temperature"] = self._temperature
        if self._top_p is not None:
            overrides["top_p"] = self._top_p
        if self._max_tokens is not None:
            overrides["max_tokens"] = self._max_tokens
        return overrides

    def _write_provider_config(
        self,
        config_path: Path,
        *,
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        provider_options: dict[str, Any] = {
            "apiKey": self._api_key if api_key is None else api_key,
            "baseURL": self._api_base if api_base is None else api_base,
            "timeout": self._solver_timeout_sec * 1000,
        }
        provider_options.update(self._provider_request_overrides())
        if self._streaming is not None:
            provider_options["streaming"] = bool(self._streaming)
        apply_openai_logprob_request(provider_options, self._logprob_capture)
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
        config_path.write_text(
            json.dumps(provider_config, indent=2),
            encoding="utf-8",
        )

    def _build_run_command(
        self,
        *,
        task_id: str,
        container_workdir: str,
    ) -> list[str]:
        cmd = [
            *self._opencode_command_prefix(),
            "run",
            "--format",
            "json",
            "--dir",
            container_workdir,
            "--title",
            task_id,
        ]
        if self._model:
            cmd.extend(["--model", self._model])
        if self._variant:
            cmd.extend(["--variant", self._variant])
        if self._print_logs:
            cmd.append("--print-logs")
        if self._log_level:
            cmd.extend(["--log-level", self._log_level])
        cmd.append('$(cat "$ALPHADIANA_PROMPT_FILE")')
        return cmd

    def _collect_session_trace(
        self,
        container_id: str,
        remote_opencode_home: str,
    ) -> str:
        result = self._docker_exec_capture(
            container_id,
            (
                f"if [ -d {shlex.quote(remote_opencode_home)} ]; then "
                f"find {shlex.quote(remote_opencode_home)} -type f -name '*.jsonl' -print | sort | "
                "while IFS= read -r path; do "
                "printf '# file: %s\\n' \"$path\"; "
                "cat \"$path\"; "
                "printf '\\n'; "
                "done; "
                "fi"
            ),
            timeout_sec=30,
        )
        return result.stdout

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        del sandbox
        t_start = time.time()
        reward_content = ""
        stderr = ""
        returncode = 0
        raw_output = ""
        session_id = ""
        events: list[dict[str, Any]] = []
        session_trace = ""
        session_trace_source = ""
        test_output = ""
        verifier_result: Any | None = None
        logprob_proxy: LogprobCaptureProxy | None = None
        logprob_proxy_records: list[dict] = []
        logprob_proxy_metadata: dict[str, Any] = {}
        task_text, prompt_text = self._build_incontainer_prompt(task)
        runtime, runtime_metadata = self._prepare_incontainer_runtime(
            task,
            agent_type="opencode",
            runtime_source_image=self._runtime_source_image,
            temp_prefix="tb2-opencode-",
        )
        remote_root = self._build_remote_root(task, agent_name="opencode")
        remote_prompt_path = f"{remote_root}/prompt.txt"
        remote_config_path = f"{remote_root}/xdg-config/opencode/opencode.json"
        remote_home = f"{remote_root}/home"
        prompt_path = runtime.workdir / "PROMPT.txt"
        prompt_path.write_text(prompt_text, encoding="utf-8")
        (runtime.workdir / "TASK.md").write_text(task_text, encoding="utf-8")
        container_workdir = "/"

        try:
            container_workdir = self._detect_container_workspace(runtime.container_id)
            config_path = runtime.workdir / "xdg-config" / "opencode" / "opencode.json"
            effective_api_base = self._api_base
            effective_api_key = self._api_key
            if self._logprob_capture["enabled"]:
                upstream = normalize_openai_proxy_upstream(self._api_base)
                advertise_host = resolve_logprob_proxy_advertise_host(
                    self._api_base,
                    self._logprob_proxy_advertise_host,
                )
                logprob_proxy = LogprobCaptureProxy(
                    upstream,
                    self._logprob_capture["top_logprobs"],
                    bind_host=self._logprob_proxy_bind_host,
                    advertise_host=advertise_host,
                    client_timeout=max(120.0, float(self._solver_timeout_sec)),
                    upstream_api_key=self._api_key,
                    request_overrides=self._provider_request_overrides() or None,
                )
                logprob_proxy.start()
                effective_api_base = f"{logprob_proxy.proxy_url.rstrip('/')}/v1"
                logprob_proxy_metadata = {
                    "logprob_proxy_enabled": True,
                    "logprob_proxy_url": effective_api_base,
                    "logprob_proxy_upstream": logprob_proxy.upstream,
                }
            self._write_provider_config(
                config_path,
                api_base=effective_api_base,
                api_key=effective_api_key,
            )
            self._stage_file_into_container(
                runtime.container_id,
                local_path=prompt_path,
                remote_path=remote_prompt_path,
            )
            self._stage_file_into_container(
                runtime.container_id,
                local_path=config_path,
                remote_path=remote_config_path,
            )
            env = {
                "HOME": remote_home,
                "XDG_CONFIG_HOME": f"{remote_root}/xdg-config",
                "OPENAI_API_KEY": effective_api_key,
                "OPENAI_BASE_URL": effective_api_base,
            }
            cmd = self._build_run_command(
                task_id=task.task_id,
                container_workdir=container_workdir,
            )
            runner = shlex.join(cmd[:-1])
            exec_result = self._docker_exec_capture(
                runtime.container_id,
                (
                    f"mkdir -p {shlex.quote(remote_home)}\n"
                    "unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy 2>/dev/null || true\n"
                    f"export ALPHADIANA_PROMPT_FILE={shlex.quote(remote_prompt_path)}\n"
                    f"prompt=$(cat {shlex.quote(remote_prompt_path)})\n"
                    f"timeout --kill-after=15s {self._solver_timeout_sec}s {runner} \"$prompt\""
                ),
                env=env,
                cwd=container_workdir,
                timeout_sec=self._solver_timeout_sec + 30,
            )
            raw_output = exec_result.stdout
            stderr = exec_result.stderr
            returncode = exec_result.returncode
            if logprob_proxy is not None:
                logprob_proxy_records = logprob_proxy.drain_records()
            session_trace = self._collect_session_trace(
                runtime.container_id,
                f"{remote_home}/.opencode",
            )
            if session_trace.strip():
                session_trace_source = "opencode_session_files"
            else:
                session_trace = _jsonl_from_stdout(raw_output)
                if session_trace.strip():
                    session_trace_source = "stdout_jsonl_fallback"
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
            (runtime.workdir / "opencode_session.jsonl").write_text(
                session_trace,
                encoding="utf-8",
                errors="replace",
            )
            verifier_result = self._run_verifier_and_read_reward(
                runtime,
                timeout_sec=self._test_timeout_sec,
            )
            test_output = verifier_result.test_output
            reward_content = verifier_result.reward
        finally:
            if logprob_proxy is not None:
                logprob_proxy.stop()
            artifact_files = self._collect_text_artifacts({
                "/terminal_bench2/opencode/TASK.md": runtime.workdir / "TASK.md",
                "/terminal_bench2/opencode/PROMPT.txt": runtime.workdir / "PROMPT.txt",
                "/terminal_bench2/opencode/opencode_stdout.log": runtime.workdir / "opencode_stdout.log",
                "/terminal_bench2/opencode/opencode_stderr.log": runtime.workdir / "opencode_stderr.log",
                "/terminal_bench2/opencode/opencode_session.jsonl": runtime.workdir / "opencode_session.jsonl",
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
        session_records = parse_jsonl_records(session_trace)
        request_messages = [{"role": "user", "content": prompt_text}]
        trajectory_records = session_records or events
        trajectory, reasoning_trajectory = build_event_trajectories(
            request_messages,
            trajectory_records,
            final_output=full_content,
        )
        artifact_manifest = add_artifact_file_refs(
            {},
            response_stream="opencode_stdout.log" if raw_output else None,
            session_trace="opencode_session.jsonl" if session_trace else None,
            stderr_log="opencode_stderr.log" if stderr else None,
            prompt_text="PROMPT.txt",
            runtime_config="opencode.json",
        )
        logprob_records = extract_opencode_logprob_records(
            events=events,
            session_trace=session_trace,
            stdout=raw_output,
        )
        logprob_source = "opencode_artifacts" if logprob_records else ""
        if not logprob_records and logprob_proxy_records:
            logprob_records = logprob_proxy_records
            logprob_source = "provider_proxy"

        response_metadata = self._build_metadata(
            runtime,
            reward=reward_content,
            rounds_used=1,
            runner="opencode",
            extra={
                "returncode": returncode,
                "stderr": stderr[:2000] if stderr else "",
                "num_events": len(events),
                "logprob_probe_event_count": len(events),
                "logprob_probe_session_json_count": _count_json_objects(session_trace),
                "logprob_probe_stdout_json_count": _count_json_objects(raw_output),
                "logprob_probe_proxy_count": len(logprob_proxy_records),
                "logprob_probe_record_count": len(logprob_records),
                "logprob_source": logprob_source,
                "session_trace_source": session_trace_source,
                "session_id": session_id,
                "test_output": test_output,
                "verifier_status": verifier_result.status if verifier_result else "",
                "verifier_reward_observed": (
                    verifier_result.reward is not None if verifier_result else False
                ),
                "container_workdir": container_workdir,
                **logprob_proxy_metadata,
                **runtime_metadata,
            },
        )
        token_entropy_stats, response_metadata = finalize_logprob_capture(
            harness="opencode",
            enabled=self._logprob_capture["enabled"],
            records=logprob_records,
            metadata=response_metadata,
        )

        return AgentResponse(
            answer=reward_content,
            trajectory=trajectory,
            reasoning_trajectory=reasoning_trajectory,
            raw_output=full_content,
            wall_time_sec=time.time() - t_start,
            token_entropy_stats=token_entropy_stats,
            metadata=response_metadata,
            request_messages=request_messages,
            response_json=build_runtime_trace_summary(
                output_text=full_content,
                stderr_text=stderr.strip(),
                records=trajectory_records,
                extra={
                    "returncode": returncode,
                    "session_id": session_id,
                    "num_events": len(events),
                    "session_trace_present": bool(session_trace.strip()),
                    "session_trace_source": session_trace_source,
                    "num_session_records": len(session_records),
                    "transport": "opencode_cli_container",
                },
            ),
            artifact_manifest=artifact_manifest,
            workspace_file_contents=artifact_files,
            system_prompt=IN_CONTAINER_AGENT_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_opencode", TerminalBench2OpenCodeAgent)
