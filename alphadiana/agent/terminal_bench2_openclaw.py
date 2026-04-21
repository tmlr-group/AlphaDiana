"""Terminal-bench-2 native agent backed by the OpenClaw CLI inside the task container."""

from __future__ import annotations

import json
import logging
import os
import shlex
import time
from pathlib import Path
from typing import Any, Optional

from alphadiana.agent.base import Agent, AgentResponse
from alphadiana.agent.openclaw import (
    _extract_trajectory_error,
    _extract_reasoning_trajectory_from_payload,
    _parse_openclaw_session,
    _recover_partial_output_from_trajectory,
)
from alphadiana.agent.preservation import add_artifact_file_refs
from alphadiana.agent.registry import AgentRegistry
from alphadiana.agent.terminal_bench2_incontainer import (
    IN_CONTAINER_AGENT_PROMPT,
    TerminalBench2InContainerMixin,
)
from alphadiana.benchmark.base import BenchmarkTask

logger = logging.getLogger(__name__)


def _build_openclaw_reasoning_trajectory(trajectory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract reasoning/tool events from a parsed OpenClaw session trajectory."""
    reasoning: list[dict[str, Any]] = []
    for entry in trajectory:
        if not isinstance(entry, dict):
            continue
        thinking = str(entry.get("thinking", "") or "").strip()
        if thinking:
            reasoning.append(
                {
                    "role": "assistant",
                    "type": "reasoning",
                    "content": thinking,
                }
            )
        role = str(entry.get("role", "") or "").strip()
        if role in {"tool_use", "tool_result"}:
            reasoning.append(
                {
                    "role": role,
                    "type": role,
                    "content": json.dumps(entry, ensure_ascii=False, sort_keys=True),
                }
            )
            continue
        for key, step_type in (("tool_calls", "tool_call"), ("tool_results", "tool_result")):
            value = entry.get(key)
            if isinstance(value, list) and value:
                reasoning.append(
                    {
                        "role": role or "assistant",
                        "type": step_type,
                        "content": json.dumps(value, ensure_ascii=False, sort_keys=True),
                    }
                )
    return reasoning


class TerminalBench2OpenClawAgent(TerminalBench2InContainerMixin, Agent):
    """In-container OpenClaw runner for terminal-bench-2."""

    name = "terminal_bench2_openclaw"
    version = "1.0"

    def setup(self, config: dict) -> None:
        self._setup_container_config(config)
        self._api_base = self._resolve_setting(config, "api_base", "OPENAI_BASE_URL")
        self._api_key = self._resolve_setting(config, "api_key", "OPENAI_API_KEY")
        self._model_name = self._resolve_setting(config, "model_name", "OPENAI_MODEL_NAME")
        if not self._model_name:
            fallback_model = str(config.get("model", "") or "").strip()
            if fallback_model and fallback_model.lower() != "openclaw":
                self._model_name = fallback_model
        self._solver_timeout_sec = int(config.get("solver_timeout_sec", config.get("timeout", 1800)))
        self._onboard_timeout_sec = int(config.get("onboard_timeout_sec", min(self._solver_timeout_sec, 180)))
        self._min_context_window = int(config.get("min_context_window", 32768))
        self._fail_on_small_context_window = bool(config.get("fail_on_small_context_window", True))
        self._context_probe_timeout_sec = int(config.get("context_probe_timeout_sec", 10))
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
        self._runtime_source_image = self._resolve_runtime_source_image(
            config,
            agent_type="openclaw",
        )

    @staticmethod
    def _resolve_setting(config: dict, key: str, env_var: str) -> str:
        value = str(config.get(key, "") or "").strip()
        if value and value.upper() != "EMPTY":
            return value
        return os.environ.get(env_var, "").strip()

    @staticmethod
    def _normalize_model_alias(value: Any) -> str:
        return str(value or "").strip().lower()

    @classmethod
    def _context_window_from_model(cls, model: dict[str, Any]) -> int | None:
        candidates = [
            model.get("max_model_len"),
            model.get("context_length"),
            model.get("top_provider", {}).get("context_length")
            if isinstance(model.get("top_provider"), dict)
            else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, int) and candidate > 0:
                return candidate
        return None

    def _resolve_context_window(self) -> tuple[int | None, str]:
        if not self._api_base:
            return None, "missing_api_base"
        try:
            import httpx

            headers = {}
            if self._api_key and self._api_key.upper() != "EMPTY":
                headers["Authorization"] = f"Bearer {self._api_key}"
            response = httpx.get(
                f"{self._api_base.rstrip('/')}/models",
                headers=headers,
                timeout=self._context_probe_timeout_sec,
                trust_env=False,
            )
            if response.status_code != 200:
                return None, f"http_{response.status_code}"
            payload = response.json()
        except Exception as exc:
            logger.warning("OpenClaw context preflight failed for %s: %s", self._api_base, exc)
            return None, exc.__class__.__name__

        for model in payload.get("data", []):
            if not isinstance(model, dict):
                continue
            max_len = self._context_window_from_model(model)
            if not isinstance(max_len, int) or max_len <= 0:
                continue
            aliases = {
                self._normalize_model_alias(model.get("id")),
                self._normalize_model_alias(model.get("name")),
                self._normalize_model_alias(model.get("model")),
                self._normalize_model_alias(model.get("canonical_slug")),
                self._normalize_model_alias(model.get("hugging_face_id")),
            }
            aliases.discard("")
            requested_model = self._normalize_model_alias(self._model_name)
            if requested_model and requested_model in aliases:
                return max_len, "models_endpoint"
            if requested_model and any(
                alias.endswith(requested_model) or requested_model.endswith(alias)
                for alias in aliases
            ):
                return max_len, "models_endpoint_suffix"
            if not requested_model:
                return max_len, "models_endpoint_first"
        return None, "missing_model_match"

    def _write_context_preflight(self, workdir: Path) -> dict[str, Any]:
        resolved_context_window, source = self._resolve_context_window()
        payload = {
            "api_base": self._api_base,
            "model_name": self._model_name,
            "required_context_window": self._min_context_window,
            "resolved_context_window": resolved_context_window,
            "source": source,
            "pass": (
                resolved_context_window is not None
                and resolved_context_window >= self._min_context_window
            ),
        }
        (workdir / "openclaw_context_preflight.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if (
            self._fail_on_small_context_window
            and resolved_context_window is not None
            and resolved_context_window < self._min_context_window
        ):
            raise RuntimeError(
                "OpenClaw provider context window is too small for terminal-bench-2: "
                f"resolved={resolved_context_window}, required={self._min_context_window}, "
                f"model={self._model_name or '<unset>'}"
            )
        return payload

    def _patch_runtime_model_contract(
        self,
        config_path: Path,
        *,
        resolved_context_window: int | None,
    ) -> dict[str, Any]:
        if not config_path.exists():
            return {"config_path": str(config_path), "patched": False, "reason": "missing_config"}

        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "config_path": str(config_path),
                "patched": False,
                "reason": f"invalid_json:{exc.__class__.__name__}",
            }

        target_context = int(resolved_context_window or self._min_context_window)
        requested_model = self._normalize_model_alias(self._model_name)
        patched = False
        matched = False

        providers = payload.setdefault("models", {}).setdefault("providers", {})
        for provider_cfg in providers.values():
            if not isinstance(provider_cfg, dict):
                continue
            models = provider_cfg.get("models")
            if not isinstance(models, list):
                continue
            for model in models:
                if not isinstance(model, dict):
                    continue
                aliases = {
                    self._normalize_model_alias(model.get("id")),
                    self._normalize_model_alias(model.get("name")),
                    self._normalize_model_alias(model.get("model")),
                }
                aliases.discard("")
                if requested_model and not (
                    requested_model in aliases
                    or any(
                        alias.endswith(requested_model) or requested_model.endswith(alias)
                        for alias in aliases
                    )
                ):
                    continue
                matched = True
                current_context = model.get("contextWindow")
                if not isinstance(current_context, int) or current_context < target_context:
                    model["contextWindow"] = target_context
                    patched = True

        if patched:
            config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        return {
            "config_path": str(config_path),
            "patched": patched,
            "matched": matched,
            "resolved_context_window": target_context,
        }

    def _build_env(self, remote_home: str, remote_plugins_dir: str) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = remote_home
        env["OPENCLAW_HOME"] = remote_home
        env["OPENCLAW_BUNDLED_PLUGINS_DIR"] = remote_plugins_dir
        env["OPENAI_API_KEY"] = self._api_key
        env["OPENAI_BASE_URL"] = self._api_base
        env["OPENAI_MODEL_NAME"] = self._model_name
        env["CUSTOM_API_KEY"] = self._api_key
        for var in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "http_proxy", "https_proxy"):
            env.pop(var, None)
        return env

    def _build_onboard_command(self, workspace_root: str) -> list[str]:
        return [
            self._openclaw_bin,
            "onboard",
            "--non-interactive",
            "--flow",
            "quickstart",
            "--mode",
            "local",
            "--workspace",
            workspace_root,
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

    def _build_agent_command(self) -> list[str]:
        cmd = [
            self._openclaw_bin,
            "agent",
            "--agent",
            "main",
            "--local",
            "--json",
            "--timeout",
            str(self._solver_timeout_sec),
        ]
        if self._thinking:
            cmd.extend(["--thinking", self._thinking])
        if self._verbose:
            cmd.extend(["--verbose", self._verbose])
        cmd.extend(["--message", '$(cat "$ALPHADIANA_PROMPT_FILE")'])
        return cmd

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

    def _find_latest_remote_session_file(
        self,
        container_id: str,
        remote_agents_root: str,
    ) -> str:
        result = self._docker_exec_capture(
            container_id,
            (
                f"find {shlex.quote(remote_agents_root)} -type f -name '*.jsonl' "
                "-printf '%T@ %p\\n' | sort -n | tail -n 1 | cut -d' ' -f2-"
            ),
            timeout_sec=30,
        )
        return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""

    def solve(self, task: BenchmarkTask, sandbox: Optional[Any] = None) -> AgentResponse:
        del sandbox
        t_start = time.time()
        reward_content = ""
        task_text, prompt_text = self._build_incontainer_prompt(task)
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
        reasoning_trajectory: list[dict[str, Any]] = []
        session_file: Path | None = None
        context_preflight: dict[str, Any] = {}
        runtime_model_patch: dict[str, Any] = {}
        task_path = Path()
        prompt_path = Path()
        hints_path: Path | None = None
        container_workdir = "/"

        runtime, runtime_metadata = self._prepare_incontainer_runtime(
            task,
            agent_type="openclaw",
            runtime_source_image=self._runtime_source_image,
            temp_prefix="tb2-openclaw-",
        )
        remote_root = self._build_remote_root(task, agent_name="openclaw")
        remote_home = f"{remote_root}/home"
        remote_plugins_dir = f"{remote_root}/empty-bundled"
        remote_prompt_path = f"{remote_root}/prompt.txt"
        remote_config_path = f"{remote_home}/.openclaw/openclaw.json"
        remote_agents_root = f"{remote_home}/.openclaw/agents"

        try:
            task_path = runtime.workdir / "TASK.md"
            task_path.write_text(task_text, encoding="utf-8")
            prompt_path = runtime.workdir / "PROMPT.txt"
            prompt_path.write_text(prompt_text, encoding="utf-8")
            task_note = self._build_incontainer_task_note(task)
            if task_note:
                hints_path = runtime.workdir / "TASK_HINTS.md"
                hints_path.write_text(task_note + "\n", encoding="utf-8")

            container_workdir = self._detect_container_workspace(runtime.container_id)
            env = self._build_env(remote_home, remote_plugins_dir)
            context_preflight = self._write_context_preflight(runtime.workdir)
            self._stage_file_into_container(
                runtime.container_id,
                local_path=prompt_path,
                remote_path=remote_prompt_path,
            )

            onboard_result = self._docker_exec_capture(
                runtime.container_id,
                shlex.join(self._build_onboard_command(container_workdir)),
                env=env,
                cwd=container_workdir,
                timeout_sec=self._onboard_timeout_sec,
            )
            onboard_stdout = onboard_result.stdout
            onboard_stderr = onboard_result.stderr
            onboard_returncode = onboard_result.returncode
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

            local_config_path = runtime.workdir / "runtime_openclaw.json"
            self._copy_file_from_container(
                runtime.container_id,
                remote_path=remote_config_path,
                local_path=local_config_path,
            )
            runtime_model_patch = self._patch_runtime_model_contract(
                local_config_path,
                resolved_context_window=context_preflight.get("resolved_context_window"),
            )
            if runtime_model_patch.get("patched"):
                self._stage_file_into_container(
                    runtime.container_id,
                    local_path=local_config_path,
                    remote_path=remote_config_path,
                )

            agent_cmd = self._build_agent_command()
            agent_result = self._docker_exec_capture(
                runtime.container_id,
                (
                    f"export ALPHADIANA_PROMPT_FILE={shlex.quote(remote_prompt_path)}\n"
                    f"prompt=$(cat {shlex.quote(remote_prompt_path)})\n"
                    f"{shlex.join(agent_cmd[:-1])} \"$prompt\""
                ),
                env=env,
                cwd=container_workdir,
                timeout_sec=self._solver_timeout_sec + 30,
            )
            agent_stdout = agent_result.stdout
            agent_stderr = agent_result.stderr
            agent_returncode = agent_result.returncode
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
            remote_session_file = self._find_latest_remote_session_file(
                runtime.container_id,
                remote_agents_root,
            )
            trajectory: list[dict[str, Any]] = []
            if remote_session_file:
                session_file = runtime.workdir / "session.jsonl"
                self._copy_file_from_container(
                    runtime.container_id,
                    remote_path=remote_session_file,
                    local_path=session_file,
                )
                session_id = Path(remote_session_file).stem
                session_text = session_file.read_text(encoding="utf-8", errors="replace")
                trajectory = _parse_openclaw_session(session_text)
                assistant_text, _ = _recover_partial_output_from_trajectory(trajectory)
                trajectory_error = _extract_trajectory_error(trajectory)
                reasoning_trajectory = _build_openclaw_reasoning_trajectory(trajectory)
            if not assistant_text:
                assistant_text = agent_stdout.strip()

            test_output, reward_content = self._run_verifier_and_read_reward(
                runtime,
                timeout_sec=self._test_timeout_sec,
            )
        finally:
            artifact_paths = {
                "/terminal_bench2/openclaw/TASK.md": runtime.workdir / "TASK.md",
                "/terminal_bench2/openclaw/PROMPT.txt": runtime.workdir / "PROMPT.txt",
                "/terminal_bench2/openclaw/openclaw_context_preflight.json": runtime.workdir / "openclaw_context_preflight.json",
                "/terminal_bench2/openclaw/openclaw_onboard_stdout.log": runtime.workdir / "openclaw_onboard_stdout.log",
                "/terminal_bench2/openclaw/openclaw_onboard_stderr.log": runtime.workdir / "openclaw_onboard_stderr.log",
                "/terminal_bench2/openclaw/runtime_openclaw.json": runtime.workdir / "runtime_openclaw.json",
                "/terminal_bench2/openclaw/openclaw_stdout.log": runtime.workdir / "openclaw_stdout.log",
                "/terminal_bench2/openclaw/openclaw_stderr.log": runtime.workdir / "openclaw_stderr.log",
            }
            if hints_path is not None:
                artifact_paths["/terminal_bench2/openclaw/TASK_HINTS.md"] = hints_path
            if session_file is not None:
                artifact_paths["/terminal_bench2/openclaw/session.jsonl"] = session_file
            artifact_files = self._collect_text_artifacts(artifact_paths)
            self._cleanup_runtime(runtime)

        trajectory = []
        if session_file is not None and "/terminal_bench2/openclaw/session.jsonl" in artifact_files:
            trajectory = _parse_openclaw_session(
                artifact_files["/terminal_bench2/openclaw/session.jsonl"]
            )
            reasoning_trajectory = _build_openclaw_reasoning_trajectory(trajectory)
        if not trajectory:
            trajectory = [
                {"role": "user", "content": prompt_text},
                {"role": "assistant", "content": assistant_text},
            ]
        if not reasoning_trajectory:
            reasoning_trajectory = _extract_reasoning_trajectory_from_payload(response_json)

        response_summary = dict(response_json) if isinstance(response_json, dict) else {}
        if assistant_text or agent_stdout.strip():
            response_summary.setdefault("output_text", assistant_text or agent_stdout.strip())
        if agent_stderr.strip():
            response_summary.setdefault("stderr_text", agent_stderr.strip())
        response_summary.setdefault("returncode", agent_returncode)
        response_summary.setdefault("onboard_returncode", onboard_returncode)
        if session_id:
            response_summary.setdefault("session_id", session_id)
        if trajectory_error:
            response_summary.setdefault("trajectory_error", trajectory_error)
        if context_preflight:
            response_summary.setdefault("context_preflight", context_preflight)
        if runtime_model_patch:
            response_summary.setdefault("runtime_model_patch", runtime_model_patch)

        artifact_manifest = add_artifact_file_refs(
            {},
            response_stream="openclaw_stdout.log" if agent_stdout else None,
            session_trace="session.jsonl" if session_file is not None else None,
            stderr_log="openclaw_stderr.log" if agent_stderr else None,
            prompt_text="PROMPT.txt",
            runtime_config="runtime_openclaw.json",
            context_preflight="openclaw_context_preflight.json",
            onboard_stdout="openclaw_onboard_stdout.log" if onboard_stdout else None,
            onboard_stderr="openclaw_onboard_stderr.log" if onboard_stderr else None,
        )

        return AgentResponse(
            answer=reward_content,
            trajectory=trajectory,
            reasoning_trajectory=reasoning_trajectory,
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
                    "context_window": context_preflight.get("resolved_context_window"),
                    "context_window_source": context_preflight.get("source", ""),
                    "context_window_required": context_preflight.get(
                        "required_context_window",
                        self._min_context_window,
                    ),
                    "runtime_model_patch": runtime_model_patch,
                    "container_workdir": container_workdir,
                    **runtime_metadata,
                },
            ),
            request_messages=[{"role": "user", "content": prompt_text}],
            response_json=response_summary,
            artifact_manifest=artifact_manifest,
            workspace_file_contents=artifact_files,
            system_prompt=IN_CONTAINER_AGENT_PROMPT,
        )

    def teardown(self) -> None:
        pass


AgentRegistry.register("terminal_bench2_openclaw", TerminalBench2OpenClawAgent)
