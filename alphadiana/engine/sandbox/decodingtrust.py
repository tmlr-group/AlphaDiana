"""Sandbox bridge for DecodingTrust Agent Platform tasks."""

from __future__ import annotations

import asyncio
import copy
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from alphadiana.benchmarks.decodingtrust.benchmark import ensure_dtap_on_path, resolve_dt_root
from alphadiana.engine.sandbox.base import ExecutionResult, Sandbox, SandboxSession
from alphadiana.engine.sandbox.registry import register_sandbox


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


class _AsyncLoopThread:
    """Run loop-affine DTAP async objects on one long-lived event loop."""

    def __init__(self, *, name: str) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run_loop, name=name, daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def run(self, coro):
        with self._lock:
            if self._closed or self._loop is None:
                coro.close()
                raise RuntimeError("DecodingTrust async loop is closed")
            loop = self._loop
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=5)


def _safe_runtime_fragment(value: Any, *, fallback: str = "task", max_len: int = 80) -> str:
    fragment = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("_.-")
    if not fragment:
        fragment = fallback
    return fragment[:max_len]


def _build_runtime_task_id(task: Any) -> str:
    """Return a DTAP resource key unique to this AlphaDiana task execution."""
    metadata = getattr(task, "metadata", {}) or {}
    task_fragment = _safe_runtime_fragment(getattr(task, "task_id", ""), fallback="task")
    sample_fragment = _safe_runtime_fragment(metadata.get("sample_index", 0), fallback="0", max_len=16)
    execution_fragment = _safe_runtime_fragment(
        metadata.get("execution_id") or uuid4().hex,
        fallback=uuid4().hex,
        max_len=32,
    )
    return f"alphadiana_{task_fragment}_s{sample_fragment}_{execution_fragment}"


def _tool_to_dict(server_name: str, tool: Any, exposed_name: str) -> dict[str, Any]:
    input_schema = getattr(tool, "inputSchema", None)
    if input_schema is None:
        input_schema = getattr(tool, "input_schema", None)
    if input_schema is None and isinstance(tool, dict):
        input_schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else exposed_name)
    description = getattr(tool, "description", None) or (tool.get("description") if isinstance(tool, dict) else "") or ""
    return {
        "name": exposed_name,
        "description": description,
        "input_schema": input_schema or {},
        "server_name": server_name,
        "tool_name": name,
    }


def _serialize_mcp_result(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        return result.model_dump()
    if hasattr(result, "dict"):
        return result.dict()
    if isinstance(result, (str, int, float, bool)) or result is None:
        return result
    if isinstance(result, list):
        return [_serialize_mcp_result(item) for item in result]
    if isinstance(result, dict):
        return {str(key): _serialize_mcp_result(value) for key, value in result.items()}
    content = getattr(result, "content", None)
    if content is not None:
        return [_serialize_mcp_result(item) for item in content]
    text = getattr(result, "text", None)
    if text is not None:
        return str(text)
    return str(result)


class DecodingTrustSandboxSession(SandboxSession):
    """A DTAP task runtime session exposing MCP tools to AlphaDiana agents."""

    def __init__(
        self,
        *,
        dt_root: Path,
        task: Any,
        executor: Any,
        executor_loop: _AsyncLoopThread,
        session_id: str,
        setup_timeout: float = 900.0,
    ) -> None:
        self._dt_root = dt_root
        self._task = task
        self._task_dir = Path(task.metadata["dt_task_dir"]).resolve()
        self._executor = executor
        self._executor_loop = executor_loop
        self._session_id = session_id
        self._setup_timeout = setup_timeout
        self._instances: list[Any] = []
        self._mcp_manager: Any = None
        self._injection_manager: Any = None
        self._injection_server_urls: dict[str, str] = {}
        self._tool_map: dict[str, tuple[str, str, str, str]] = {}
        self._server_configs: list[Any] = []
        self._agent_config: Any = None
        self._tool_cache: list[dict[str, Any]] | None = None
        self._trajectory: list[dict[str, Any]] = []
        self._env_restore: dict[str, str | None] = {}
        self._closed = False

        self._start()

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def sandbox_id(self) -> str:
        return self._session_id

    def _set_env(self, key: str, value: str) -> None:
        if key not in self._env_restore:
            self._env_restore[key] = os.environ.get(key)
        os.environ[key] = value

    def _start(self) -> None:
        ensure_dtap_on_path(self._dt_root)
        from dt_arena.src.types.agent import AgentConfig
        from dt_arena.src.types.task import AttackConfig
        from utils import (
            apply_environment_injections_async,
            build_tool_injections_from_config,
            get_env_injections_from_attack,
            get_required_injection_servers,
            start_injection_mcp_servers,
            start_task_mcp_servers,
            task_setup,
            wait_for_injection_mcp_ready,
        )
        from utils.resource_manager import ResourceManager
        from utils.task_executor import ScheduledTask, get_task_environments

        self._apply_environment_injections_async = apply_environment_injections_async
        self._get_env_injections_from_attack = get_env_injections_from_attack
        self._build_tool_injections_from_config = build_tool_injections_from_config

        started_at = time.monotonic()
        self._dt_task_runtime_id = _build_runtime_task_id(self._task)
        environments = frozenset(get_task_environments(self._task_dir))
        scheduled = ScheduledTask(
            task_dir=self._task_dir,
            environments=environments,
            original_index=0,
            domain=self._task.metadata.get("domain"),
            task_type=self._task.metadata.get("task_type"),
            threat_model=self._task.metadata.get("threat_model"),
            risk_category=self._task.metadata.get("risk_category"),
            task_id=str(self._task.metadata.get("dt_task_id") or self._task.task_id),
        )

        async def _acquire():
            async with self._executor._lock:
                return await self._executor._acquire_instances_for_task(
                    scheduled,
                    self._dt_task_runtime_id,
                )

        if environments:
            self._instances = self._executor_loop.run(_acquire()) or []
            if not self._instances:
                raise RuntimeError(f"Failed to acquire DTAP environments for {self._task.task_id}")
            for instance in self._instances:
                for var_name, port in instance.ports.items():
                    self._set_env(var_name, str(port))
                env_name_upper = instance.env_name.upper().replace("-", "_")
                self._set_env(f"{env_name_upper}_PROJECT_NAME", instance.project_name)

        task_setup(self._task_dir, task_id=self._dt_task_runtime_id)
        if time.monotonic() - started_at > self._setup_timeout:
            raise TimeoutError(f"DTAP setup exceeded {self._setup_timeout:.0f}s")

        agent_cfg = AgentConfig.from_yaml(str(self._task_dir / "config.yaml"))
        self._agent_config = agent_cfg
        resource_mgr = ResourceManager.instance()
        self._mcp_manager = start_task_mcp_servers(
            agent_cfg,
            self._dt_task_runtime_id,
            self._task_dir,
            resource_mgr,
        )
        self._server_configs = [
            server for server in (agent_cfg.mcp_servers or []) if getattr(server, "enabled", True)
        ]

        try:
            self._attack_cfg = AttackConfig.from_yaml(str(self._task_dir / "config.yaml"))
        except Exception:
            self._attack_cfg = None
        all_env_injections = get_env_injections_from_attack(self._attack_cfg)
        if all_env_injections:
            required_servers = get_required_injection_servers(all_env_injections)
            if required_servers:
                injection_config = {
                    "environment_enabled": True,
                    "environment_servers": required_servers,
                }
                self._injection_manager, injection_config = start_injection_mcp_servers(
                    injection_config,
                    resource_manager=resource_mgr,
                    task_id=self._dt_task_runtime_id,
                )
                if injection_config.get("environment_servers"):
                    wait_for_injection_mcp_ready(injection_config)
                    for server_name, server_info in injection_config["environment_servers"].items():
                        if isinstance(server_info, dict) and "url" in server_info:
                            self._injection_server_urls[server_name] = server_info["url"]

    def execute(self, command: str) -> ExecutionResult:
        started = time.monotonic()
        import subprocess

        proc = subprocess.run(
            command,
            cwd=str(self._task_dir),
            shell=True,
            text=True,
            capture_output=True,
            check=False,
        )
        return ExecutionResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            wall_time_sec=time.monotonic() - started,
        )

    def upload(self, filename: str, content: bytes) -> None:
        target = self._task_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def download(self, filename: str) -> bytes:
        return (self._task_dir / filename).read_bytes()

    async def _list_tools_async(self) -> list[dict[str, Any]]:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
        from mcp.client.streamable_http import streamablehttp_client

        if self._tool_cache is not None:
            return self._tool_cache
        if self._mcp_manager is None:
            self._tool_cache = []
            return []

        raw_tools: list[dict[str, Any]] = []
        name_counts: dict[str, int] = {}
        for cfg in self._server_configs:
            server_name = str(getattr(cfg, "name", "") or "")
            url = str(getattr(cfg, "url", "") or "")
            transport = str(getattr(cfg, "transport", "http") or "http")
            if not server_name or not url:
                continue
            if transport == "sse":
                async with sse_client(url) as (read, write):
                    session = ClientSession(read, write)
                    async with session:
                        await session.initialize()
                        response = await session.list_tools()
            else:
                async with streamablehttp_client(url) as (read, write, _):
                    session = ClientSession(read, write)
                    async with session:
                        await session.initialize()
                        response = await session.list_tools()
            for tool in response.tools:
                base_name = str(getattr(tool, "name", ""))
                exposed_name = base_name
                name_counts[base_name] = name_counts.get(base_name, 0) + 1
                if name_counts[base_name] > 1:
                    exposed_name = f"{server_name}__{base_name}"
                self._tool_map[exposed_name] = (server_name, base_name, url, transport)
                raw_tools.append(_tool_to_dict(server_name, tool, exposed_name))

        injections = self._build_tool_injections_from_config(self._attack_cfg)
        if injections:
            for tool in raw_tools:
                server_injections = injections.get(tool["server_name"], {})
                injection = server_injections.get(tool["tool_name"])
                if not injection:
                    continue
                mode = getattr(injection, "type", "suffix")
                content = getattr(injection, "content", "")
                if mode == "override":
                    tool["description"] = content
                else:
                    tool["description"] = f"{tool['description']}\n{content}".strip()

        self._tool_cache = raw_tools
        return raw_tools

    def list_tools(self) -> list[dict[str, Any]]:
        return _run_async(self._list_tools_async())

    async def _call_tool_async(self, name: str, arguments: dict[str, Any]) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        if not self._tool_map:
            await self._list_tools_async()
        if name not in self._tool_map:
            raise KeyError(f"DTAP MCP tool not found: {name}")
        server_name, tool_name, url, transport = self._tool_map[name]
        started = time.monotonic()
        if transport == "sse":
            from mcp.client.sse import sse_client

            async with sse_client(url) as (read, write):
                session = ClientSession(read, write)
                async with session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments or {})
        else:
            async with streamablehttp_client(url) as (read, write, _):
                session = ClientSession(read, write)
                async with session:
                    await session.initialize()
                    result = await session.call_tool(tool_name, arguments=arguments or {})
        serialized = _serialize_mcp_result(result)
        event = {
            "type": "tool_call",
            "server_name": server_name,
            "tool_name": tool_name,
            "name": name,
            "arguments": arguments or {},
            "result": serialized,
            "wall_time_sec": time.monotonic() - started,
        }
        self._trajectory.append(event)
        return serialized

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        return _run_async(self._call_tool_async(name, arguments or {}))

    def apply_turn_injections(self, turn_id: int) -> list[dict[str, Any]]:
        injections = self._get_env_injections_from_attack(self._attack_cfg, turn_id=turn_id)
        if not injections:
            return []
        results = _run_async(
            self._apply_environment_injections_async(injections, self._injection_server_urls)
        )
        self._trajectory.append(
            {
                "type": "environment_injection",
                "turn_id": turn_id,
                "results": results,
            }
        )
        return results

    def get_dt_agent_config(self) -> Any:
        """Return a copy of the DTAP AgentConfig with live MCP server URLs."""
        if self._agent_config is None:
            raise RuntimeError("DecodingTrust sandbox has no active AgentConfig")
        return copy.deepcopy(self._agent_config)

    def get_dt_attack_config(self) -> Any:
        """Return the parsed DTAP AttackConfig for this task, if present."""
        return self._attack_cfg

    def get_injection_server_urls(self) -> dict[str, str]:
        """Return active environment-injection server URLs."""
        return dict(self._injection_server_urls)

    def metadata(self) -> dict:
        return {
            "session_id": self.session_id,
            "sandbox_id": self.session_id,
            "sandbox_backend": "decodingtrust",
            "dt_root": str(self._dt_root),
            "dt_task_dir": str(self._task_dir),
            "dt_runtime_task_id": getattr(self, "_dt_task_runtime_id", ""),
            "dt_environments": [getattr(instance, "env_name", "") for instance in self._instances],
            "dt_tool_trajectory": list(self._trajectory),
        }

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._mcp_manager is not None:
                self._mcp_manager.stop_all()
        finally:
            try:
                if self._injection_manager is not None:
                    self._injection_manager.stop_all()
            finally:
                async def _release():
                    async with self._executor._lock:
                        released = await self._executor._release_instances_for_task(self._dt_task_runtime_id)
                        await self._executor._cleanup_unused_instances(released)

                if self._instances:
                    try:
                        self._executor_loop.run(_release())
                    except Exception:
                        pass
                for key, value in self._env_restore.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


@register_sandbox("decodingtrust")
class DecodingTrustSandbox(Sandbox):
    """DTAP native Docker Compose sandbox provider."""

    @property
    def name(self) -> str:
        return "decodingtrust"

    def setup(self, config: dict) -> None:
        self._config = dict(config or {})
        self._dt_root = resolve_dt_root(self._config)
        ensure_dtap_on_path(self._dt_root)
        from utils.task_executor import TaskExecutor

        self._executor_loop = _AsyncLoopThread(name="alphadiana-dtap-executor")

        async def _create_executor():
            return TaskExecutor(max_parallel=1)

        self._executor = self._executor_loop.run(_create_executor())

    def supports_pooling(self) -> bool:
        return False

    def supports_shared_session(self) -> bool:
        return False

    def create_session(self, task: Any | None = None) -> DecodingTrustSandboxSession:
        if task is None:
            raise ValueError("DecodingTrustSandbox.create_session requires task=BenchmarkTask")
        return DecodingTrustSandboxSession(
            dt_root=self._dt_root,
            task=task,
            executor=self._executor,
            executor_loop=self._executor_loop,
            session_id=f"dtap-{uuid4().hex[:12]}",
            setup_timeout=float(self._config.get("setup_timeout", 900)),
        )

    def teardown(self) -> None:
        executor = getattr(self, "_executor", None)
        executor_loop = getattr(self, "_executor_loop", None)
        if executor is not None and executor_loop is not None:
            try:
                executor_loop.run(executor.shutdown())
            except Exception:
                pass
            finally:
                executor_loop.stop()
