"""ROCK sandbox adapter built on top of the official ROCK SDK.

Uses the official rock-sdk package for full async/sync compatibility,
session management, file I/O, agent install/run, and resource profiling
with automatic fallback to smaller profiles.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alphadiana.sandbox.base import ExecutionResult, Sandbox, SandboxSession
from alphadiana.sandbox.registry import register_sandbox
from alphadiana.utils.rock_runtime import DEFAULT_SANDBOX_IMAGE, configure_rock_runtime_for_image

logger = logging.getLogger(__name__)
_PROFILE_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_chdir_lock = threading.Lock()

CLEANUP_PATHS = [
    "/root/.openclaw/workspace",
    "/root/.openclaw/agents/main/sessions",
]


def _progress(message: str) -> None:
    print(f"[ROCK] {message}", flush=True)


try:
    from rock.actions import BashAction, Command, CreateBashSessionRequest
    from rock.actions.sandbox.request import WriteFileRequest
    from rock.config import ProxyServiceConfig
    from rock.sdk.sandbox.client import Sandbox as ROCKClientSandbox
    from rock.sdk.sandbox.config import SandboxConfig
except ImportError:
    BashAction = None  # type: ignore[assignment]
    Command = None  # type: ignore[assignment]
    CreateBashSessionRequest = None  # type: ignore[assignment]
    WriteFileRequest = None  # type: ignore[assignment]
    ProxyServiceConfig = None  # type: ignore[assignment]
    ROCKClientSandbox = None  # type: ignore[assignment]
    SandboxConfig = None  # type: ignore[assignment]


def _require_rock_sdk() -> None:
    if ROCKClientSandbox is None or SandboxConfig is None:
        raise RuntimeError(
            "The 'rock-sdk' package is required for ROCKSandbox. "
            "Install it in the current environment before using the ROCK backend."
        )


def _run_async(coro: Any) -> Any:
    """Run a coroutine from sync code, even if another loop already exists."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if "value" in error:
        raise error["value"]
    return result.get("value")


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_session_execute(session: Any, command: str) -> Any:
    method = getattr(session, "execute", None)
    if method is not None:
        try:
            return await _maybe_await(method(command))
        except TypeError:
            return await _maybe_await(method(cmd=command))
    method = getattr(session, "run", None)
    if method is not None:
        return await _maybe_await(method(command))
    raise AttributeError(f"{type(session).__name__} does not expose execute/run")


class _SandboxSessionRunner:
    """Compatibility wrapper that exposes execute() over SDK session APIs."""

    def __init__(self, sandbox: Any, session_name: str = "default") -> None:
        self._sandbox = sandbox
        self._session_name = session_name

    async def execute(self, command: str) -> Any:
        arun = getattr(self._sandbox, "arun", None)
        if arun is not None:
            return await _maybe_await(arun(command, session=self._session_name))

        for method_name in ("run_in_session", "_run_in_session"):
            method = getattr(self._sandbox, method_name, None)
            if method is not None:
                return await _maybe_await(
                    method(BashAction(command=command, session=self._session_name))
                )

        raise AttributeError(f"{type(self._sandbox).__name__} does not expose run_in_session")


def _is_session_upstream_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "upstream server is not reachable" in message
        or "failed to run in session" in message
        or "run in session failed" in message
    )


async def _wait_for_session_ready(sandbox: Any, session_name: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            arun = getattr(sandbox, "arun", None)
            if arun is not None:
                result = await _maybe_await(arun("true", session=session_name))
            else:
                run_method = getattr(sandbox, "_run_in_session", None) or getattr(sandbox, "run_in_session", None)
                if run_method is None:
                    return
                result = await _maybe_await(
                    run_method(BashAction(command="true", session=session_name, timeout=5))
                )
            if int(getattr(result, "exit_code", 0)) == 0:
                return
        except Exception as exc:
            last_error = exc
            if not _is_session_upstream_error(exc):
                raise
        await asyncio.sleep(0.5)
    if last_error is not None:
        raise RuntimeError(f"ROCK session '{session_name}' did not become ready: {last_error}") from last_error
    raise RuntimeError(f"ROCK session '{session_name}' did not become ready within {timeout:.1f}s")


async def _resolve_runtime_session(sandbox: Any) -> Any:
    session_name = "default"
    create_session = getattr(sandbox, "create_session", None)
    if create_session is None:
        raise AttributeError(f"{type(sandbox).__name__} does not expose create_session")
    await _maybe_await(create_session(CreateBashSessionRequest(session=session_name)))
    await _wait_for_session_ready(sandbox, session_name)
    return _SandboxSessionRunner(sandbox, session_name=session_name)


def _install_create_session_waiter(sandbox: Any) -> None:
    if getattr(sandbox, "_alphadiana_create_session_waiter_installed", False):
        return

    original = getattr(sandbox, "create_session", None)
    if original is None:
        return

    async def _wrapped_create_session(request: Any) -> Any:
        response = await _maybe_await(original(request))
        session_name = getattr(request, "session", None)
        if session_name:
            await _wait_for_session_ready(sandbox, session_name)
        return response

    sandbox.create_session = _wrapped_create_session
    sandbox._alphadiana_create_session_waiter_installed = True


def _install_run_in_session_retry(sandbox: Any) -> None:
    if getattr(sandbox, "_alphadiana_run_in_session_retry_installed", False):
        return

    for method_name in ("run_in_session", "_run_in_session"):
        original = getattr(sandbox, method_name, None)
        if original is None:
            continue

        async def _wrapped_run_in_session(action: Any, _original=original, _name=method_name) -> Any:
            max_attempts = 4
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await _maybe_await(_original(action))
                except Exception as exc:
                    last_exc = exc
                    if not _is_session_upstream_error(exc) or attempt >= max_attempts:
                        raise
                    logger.warning(
                        "ROCK sandbox.%s transient upstream error attempt=%d/%d: %s",
                        _name,
                        attempt,
                        max_attempts,
                        exc,
                    )
                    await asyncio.sleep(min(0.5 * attempt, 2.0))

            assert last_exc is not None
            raise last_exc

        setattr(sandbox, method_name, _wrapped_run_in_session)

    sandbox._alphadiana_run_in_session_retry_installed = True


async def _upload_file(sandbox: Any, remote_path: str, content: bytes) -> None:
    text_content = content.decode("utf-8")

    write_file_by_path = getattr(sandbox, "write_file_by_path", None)
    if write_file_by_path is not None:
        response = await _maybe_await(write_file_by_path(text_content, remote_path))
        if getattr(response, "success", True):
            return

    write_file = getattr(sandbox, "write_file", None)
    if write_file is not None:
        response = await _maybe_await(write_file(WriteFileRequest(content=text_content, path=remote_path)))
        if getattr(response, "success", True):
            return

    session = await _resolve_runtime_session(sandbox)
    execute = f"python3 - <<'PY'\nfrom pathlib import Path\nPath({remote_path!r}).parent.mkdir(parents=True, exist_ok=True)\nPath({remote_path!r}).write_bytes({content!r})\nPY"
    await _call_session_execute(session, execute)


async def _read_file(sandbox: Any, remote_path: str) -> str:
    for name in ("read_file", "read_text", "download"):
        # Try sandbox.file.<method> first
        file_api = getattr(sandbox, "file", None)
        if file_api is not None:
            method = getattr(file_api, name, None)
            if method is not None:
                try:
                    value = await _maybe_await(method(remote_path))
                    if isinstance(value, bytes):
                        return value.decode("utf-8", errors="replace")
                    return str(value)
                except TypeError:
                    try:
                        value = await _maybe_await(method(path=remote_path))
                        if isinstance(value, bytes):
                            return value.decode("utf-8", errors="replace")
                        return str(value)
                    except TypeError:
                        continue

        # Try sandbox.<method> directly
        method = getattr(sandbox, name, None)
        if method is not None:
            try:
                value = await _maybe_await(method(remote_path))
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value)
            except TypeError:
                try:
                    value = await _maybe_await(method(path=remote_path))
                    if isinstance(value, bytes):
                        return value.decode("utf-8", errors="replace")
                    return str(value)
                except TypeError:
                    continue

    session = await _resolve_runtime_session(sandbox)
    result = await _call_session_execute(session, f"cat {remote_path}")
    return getattr(result, "stdout", str(result))


async def _read_file_range(sandbox: Any, remote_path: str, start: int, end: int) -> str:
    file_api = getattr(sandbox, "file", None)
    if file_api is not None:
        method = getattr(file_api, "read_file_by_line_range", None)
        if method is not None:
            try:
                value = await _maybe_await(method(remote_path, start, end))
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="replace")
                return str(value)
            except TypeError:
                pass
    return await _read_file(sandbox, remote_path)


def _normalize_proxy_base(proxy_base_url: str) -> str:
    trimmed = proxy_base_url.rstrip("/")
    suffix = "/apis/envs/sandbox/v1"
    if trimmed.endswith(suffix):
        return trimmed
    return f"{trimmed}{suffix}"


_MEMORY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]i?|b)?\s*$", re.IGNORECASE)


def _memory_to_bytes(value: str) -> int:
    match = _MEMORY_RE.match(value)
    if not match:
        raise ValueError(f"Unsupported memory format: {value}")
    number = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    factors = {
        "b": 1, "k": 1000, "m": 1000**2, "g": 1000**3, "t": 1000**4,
        "ki": 1024, "mi": 1024**2, "gi": 1024**3, "ti": 1024**4,
    }
    return int(number * factors[unit])


def _canonical_memory(value: str) -> str:
    match = _MEMORY_RE.match(value)
    if not match:
        return value.strip().lower()
    number = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    if unit == "g" and number.is_integer():
        return f"{int(number)}g"
    if unit == "m" and number.is_integer():
        return f"{int(number)}m"
    if unit == "gi" and number.is_integer():
        return f"{int(number * 1024)}m"
    if unit == "mi" and number.is_integer():
        return f"{int(number)}m"
    return value.strip().lower()


def _resource_profiles(memory: str, cpus: float) -> list[tuple[str, float]]:
    requested_memory = _memory_to_bytes(memory)
    profiles: list[tuple[str, float]] = [(_canonical_memory(memory), float(cpus))]
    candidates = [
        ("3g", 3 * 1000**3, min(cpus, 1.0)),
        ("2g", 2 * 1000**3, min(cpus, 1.0)),
        ("1536m", 1536 * 1000**2, min(cpus, 0.75)),
        ("1g", 1000**3, min(cpus, 0.5)),
        ("768m", 768 * 1000**2, min(cpus, 0.5)),
        ("768m", 768 * 1000**2, min(cpus, 0.25)),
        ("512m", 512 * 1000**2, min(cpus, 0.25)),
        ("384m", 384 * 1000**2, min(cpus, 0.25)),
        ("256m", 256 * 1000**2, min(cpus, 0.125)),
    ]
    for memory_label, memory_bytes, candidate_cpu in candidates:
        if requested_memory < memory_bytes:
            continue
        profile = (memory_label, float(candidate_cpu))
        if profile not in profiles:
            profiles.append(profile)
    return profiles


def _ordered_resource_profiles(
    admin_base_url: str, image: str, memory: str, cpus: float,
) -> list[tuple[str, float]]:
    profiles = _resource_profiles(memory, cpus)
    cached = _PROFILE_CACHE.get((admin_base_url, image))
    if cached is not None and cached in profiles:
        return [cached, *[profile for profile in profiles if profile != cached]]
    return profiles


@dataclass
class ROCKSandboxMetadata:
    sandbox_id: str
    admin_base_url: str
    proxy_base_url: str
    image: str
    memory: str
    cpus: float
    startup_timeout: int
    auto_clear_seconds: int


class ROCKSession(SandboxSession):
    """Lifecycle wrapper around a live ROCK sandbox."""

    def __init__(
        self,
        *,
        admin_base_url: str,
        proxy_base_url: str,
        image: str,
        memory: str,
        cpus: float,
        startup_timeout: int,
        auto_clear_seconds: int,
        start_retries: int = 1,
        fallback_startup_timeout: int = 180,
        reset_between_tasks: bool = True,
        proxy_timeout: int = 1800,
        network_mode: str | None = None,
    ) -> None:
        _require_rock_sdk()
        self._id = str(uuid.uuid4())
        self._admin_base_url = admin_base_url.rstrip("/")
        self._proxy_base_url = _normalize_proxy_base(proxy_base_url)
        self._image = image
        self._memory = memory
        self._cpus = cpus
        self._startup_timeout = startup_timeout
        self._fallback_startup_timeout = max(30, int(fallback_startup_timeout))
        self._auto_clear_seconds = auto_clear_seconds
        self._start_retries = max(1, start_retries)
        self._reset_between_tasks = reset_between_tasks
        self._proxy_timeout = proxy_timeout
        self._network_mode = network_mode
        self._command_history: list[dict[str, Any]] = []
        _progress(
            f"create_session requested image={self._image} memory={self._memory} "
            f"cpus={self._cpus} startup_timeout={self._startup_timeout}s"
        )
        self._sandbox = self._start_sandbox()
        self._session_name = "default"
        _progress(f"creating bash session for sandbox_id={self.sandbox_id}")
        _run_async(_resolve_runtime_session(self._sandbox))
        _progress(f"bash session ready for sandbox_id={self.sandbox_id}")

    def _start_sandbox(self) -> Any:
        """Start a ROCK sandbox, trying resource profiles with fallback."""
        last_error: Exception | None = None
        requested_profile = (_canonical_memory(self._memory), float(self._cpus))
        for memory, cpus in _ordered_resource_profiles(
            self._admin_base_url, self._image, self._memory, self._cpus,
        ):
            is_requested_profile = (_canonical_memory(memory), float(cpus)) == requested_profile
            # Give the requested profile the full configured startup timeout. Smaller
            # fallback profiles usually need less than the requested profile but often
            # still need more than 60s to initialize in a contended host.
            attempt_timeout = (
                self._startup_timeout
                if is_requested_profile
                else min(self._startup_timeout, self._fallback_startup_timeout)
            )
            for attempt in range(1, self._start_retries + 1):
                _progress(
                    f"starting sandbox profile memory={memory} cpus={cpus} "
                    f"attempt={attempt}/{self._start_retries} timeout={attempt_timeout}s"
                )
                config = SandboxConfig(
                    base_url=self._admin_base_url,
                    image=self._image,
                    memory=memory,
                    cpus=cpus,
                    auto_clear_seconds=self._auto_clear_seconds,
                    startup_timeout=float(attempt_timeout),
                )
                sandbox = ROCKClientSandbox(config)
                if ProxyServiceConfig is not None and hasattr(sandbox, "config"):
                    try:
                        setattr(sandbox.config, "proxy_service", ProxyServiceConfig(timeout=float(self._proxy_timeout)))
                        logger.info(
                            "proxy_timeout=%ds set on SDK client. Ensure ROCK server "
                            "post_proxy also uses a compatible timeout (see ROCK "
                            "deployment config: proxy_service.timeout).",
                            self._proxy_timeout,
                        )
                    except Exception:
                        logger.debug("Failed to apply proxy timeout to ROCK sandbox config", exc_info=True)
                try:
                    # Guard against SDK-level hangs where sandbox.start() does not
                    # return even though startup_timeout is configured server-side.
                    # A small grace window avoids false positives during teardown.
                    _run_async(
                        asyncio.wait_for(
                            _maybe_await(sandbox.start()),
                            timeout=float(attempt_timeout) + 10.0,
                        )
                    )
                    self._memory = memory
                    self._cpus = cpus
                    # Only cache the requested profile. Caching fallback tiny
                    # profiles can poison later sessions for the same image.
                    if is_requested_profile:
                        _PROFILE_CACHE[(self._admin_base_url, self._image)] = (memory, cpus)
                    sandbox.url = self._proxy_base_url
                    # When network_mode is "host", resolve the host IP so that
                    # sandbox-internal URLs can reach the host machine.
                    if self._network_mode == "host":
                        host_ip = getattr(sandbox, "host_ip", None)
                        if host_ip:
                            _progress(f"network_mode=host, host_ip={host_ip}")
                    _install_create_session_waiter(sandbox)
                    _install_run_in_session_retry(sandbox)
                    _progress(
                        f"sandbox started sandbox_id={getattr(sandbox, 'sandbox_id', '')} "
                        f"memory={memory} cpus={cpus}"
                    )
                    return sandbox
                except Exception as exc:
                    last_error = exc
                    _progress(f"start failed for memory={memory} cpus={cpus}: {exc}")
                    logger.warning(
                        "ROCK sandbox start failed: image=%s memory=%s cpus=%s attempt=%s/%s: %s",
                        self._image, memory, cpus, attempt, self._start_retries, exc,
                    )
                    stop = getattr(sandbox, "stop", None)
                    if stop is not None:
                        try:
                            _run_async(_maybe_await(stop()))
                        except Exception:
                            pass
                    if attempt < self._start_retries:
                        time.sleep(min(5, attempt))
            _progress(f"trying smaller profile after memory={memory} cpus={cpus}")

        assert last_error is not None
        raise last_error

    @property
    def session_id(self) -> str:
        return self._id

    @property
    def sandbox_id(self) -> str:
        return str(getattr(self._sandbox, "sandbox_id", ""))

    @property
    def sandbox(self) -> Any:
        return self._sandbox

    @property
    def command_history(self) -> list[dict[str, Any]]:
        return list(self._command_history)

    def proxy_v1_base(self) -> str:
        return f"{self._proxy_base_url}/sandboxes/{self.sandbox_id}/proxy/v1"

    def execute(self, command: str) -> ExecutionResult:
        start = time.monotonic()
        result = _run_async(self._execute_command(command))
        elapsed = time.monotonic() - start
        execution_result = ExecutionResult(
            exit_code=int(getattr(result, "exit_code", 0)),
            stdout=str(getattr(result, "stdout", getattr(result, "output", ""))),
            stderr=str(getattr(result, "stderr", getattr(result, "failure_reason", ""))),
            wall_time_sec=float(getattr(result, "wall_time_sec", elapsed)),
        )
        self._command_history.append({
            "command": command,
            "exit_code": execution_result.exit_code,
            "stdout": execution_result.stdout,
            "stderr": execution_result.stderr,
            "wall_time_sec": execution_result.wall_time_sec,
        })
        return execution_result

    async def _execute_command(self, command: str) -> Any:
        arun = getattr(self._sandbox, "arun", None)
        if arun is not None:
            return await _maybe_await(arun(command, session=self._session_name))

        for method_name in ("run_in_session", "_run_in_session"):
            method = getattr(self._sandbox, method_name, None)
            if method is not None:
                return await _maybe_await(
                    method(BashAction(command=command, session=self._session_name))
                )

        execute = getattr(self._sandbox, "execute", None)
        if execute is not None:
            shell_command = f"bash -lc {command!r}"
            try:
                return await _maybe_await(execute(Command(command=shell_command)))
            except TypeError:
                return await _maybe_await(execute(shell_command))

        raise RuntimeError("ROCK sandbox does not expose an execute or run_in_session API")

    def upload(self, filename: str, content: bytes) -> None:
        _run_async(_upload_file(self._sandbox, filename, content))

    def download(self, filename: str) -> bytes:
        return self.read_text(filename).encode("utf-8")

    def read_text(self, filename: str) -> str:
        return _run_async(_read_file(self._sandbox, filename))

    def read_text_range(self, filename: str, start_line: int, end_line: int) -> str:
        return _run_async(_read_file_range(self._sandbox, filename, start_line, end_line))

    def install_agent(self, config_dir: str | Path | None = None) -> None:
        config_dir = Path(config_dir) if config_dir else None
        self._call_agent_method("install", config_dir=config_dir)

    def run_agent(self, command: str = "", config_dir: str | Path | None = None) -> None:
        config_dir = Path(config_dir) if config_dir else None
        self._call_agent_method("run", command, config_dir=config_dir)

    def _call_agent_method(
        self, method_name: str, *args: Any, config_dir: Path | None = None,
    ) -> Any:
        agent = getattr(self._sandbox, "agent", None)
        if agent is None:
            raise RuntimeError("ROCK sandbox does not expose an agent helper")
        method = getattr(agent, method_name, None)
        if method is None:
            raise RuntimeError(f"ROCK sandbox agent helper does not support '{method_name}'")
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            with _chdir_lock:
                old_cwd = Path.cwd()
                try:
                    if config_dir is not None:
                        Path(config_dir).mkdir(parents=True, exist_ok=True)
                        os.chdir(str(config_dir))
                    return _run_async(_maybe_await(method(*args)))
                except Exception as exc:
                    if not _is_session_upstream_error(exc) or attempt >= max_attempts:
                        raise
                    logger.warning(
                        "ROCK agent.%s transient upstream error for sandbox_id=%s "
                        "attempt=%d/%d: %s",
                        method_name,
                        self.sandbox_id,
                        attempt,
                        max_attempts,
                        exc,
                    )
                finally:
                    if config_dir is not None:
                        os.chdir(old_cwd)

            # Probe and wait for the default shell session to recover before retrying.
            try:
                _run_async(_wait_for_session_ready(self._sandbox, self._session_name, timeout=20.0))
            except Exception as wait_exc:
                logger.debug(
                    "session readiness probe after agent.%s failure did not fully recover "
                    "sandbox_id=%s: %s",
                    method_name,
                    self.sandbox_id,
                    wait_exc,
                    exc_info=True,
                )
            time.sleep(min(2 * attempt, 5))

        raise RuntimeError(
            f"ROCK agent helper exhausted retries for '{method_name}' on sandbox_id={self.sandbox_id}"
        )

    def metadata(self) -> dict:
        info = ROCKSandboxMetadata(
            sandbox_id=self.sandbox_id,
            admin_base_url=self._admin_base_url,
            proxy_base_url=self._proxy_base_url,
            image=self._image,
            memory=self._memory,
            cpus=self._cpus,
            startup_timeout=self._startup_timeout,
            auto_clear_seconds=self._auto_clear_seconds,
        )
        return {
            **info.__dict__,
            "session_id": self.session_id,
            "command_history": self.command_history,
        }

    def _cleanup_workspace(self) -> None:
        """Remove workspace and session files between tasks."""
        if not getattr(self, "_reset_between_tasks", True):
            return
        paths = " ".join(CLEANUP_PATHS)
        self.execute(f"rm -rf {paths}")

    def reset(self) -> None:
        """Reset the reusable sandbox session to a clean state."""
        self._cleanup_workspace()

    def close(self) -> None:
        try:
            self._cleanup_workspace()
        except Exception:
            logger.debug("Workspace cleanup failed for sandbox_id=%s", self.sandbox_id, exc_info=True)
        stop = getattr(self._sandbox, "stop", None)
        if stop is None:
            return
        self._sandbox.url = f"{self._admin_base_url}/apis/envs/sandbox/v1"
        max_stop_attempts = 3
        for attempt in range(1, max_stop_attempts + 1):
            try:
                _run_async(_maybe_await(stop()))
                _progress(f"sandbox stopped sandbox_id={self.sandbox_id}")
                return
            except Exception as exc:
                logger.warning(
                    "sandbox.stop() attempt %d/%d failed for sandbox_id=%s: %s",
                    attempt, max_stop_attempts, self.sandbox_id, exc,
                )
                if attempt < max_stop_attempts:
                    time.sleep(1)
        logger.error(
            "Failed to stop sandbox %s after %d attempts — container may be leaked",
            self.sandbox_id, max_stop_attempts,
        )


@register_sandbox("rock")
class ROCKSandbox(Sandbox):
    """Sandbox provider that manages live ROCK sandboxes."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}

    @property
    def name(self) -> str:
        return "rock"

    def setup(self, config: dict) -> None:
        _require_rock_sdk()
        self._reset_between_tasks = config.get("reset_between_tasks", True)
        self._proxy_timeout = int(config.get("proxy_timeout", 1800))
        self._network_mode = config.get("network_mode", None)
        self._config = {
            "admin_base_url": config["admin_base_url"],
            "proxy_base_url": config["proxy_base_url"],
            "image": config.get("image", DEFAULT_SANDBOX_IMAGE),
            "memory": config.get("memory", "2g"),
            "cpus": float(config.get("cpus", 0.5)),
            "startup_timeout": int(config.get("startup_timeout", 300)),
            "fallback_startup_timeout": int(config.get("fallback_startup_timeout", 180)),
            "auto_clear_seconds": int(config.get("auto_clear_seconds", 3600)),
            "start_retries": int(config.get("start_retries", 1)),
            "reset_between_tasks": bool(self._reset_between_tasks),
            "proxy_timeout": self._proxy_timeout,
            "network_mode": self._network_mode,
        }
        configure_rock_runtime_for_image(self._config["image"])

    def create_session(self) -> ROCKSession:
        return ROCKSession(**self._config)

    def metadata(self) -> dict:
        return {"name": self.name, **self._config}
