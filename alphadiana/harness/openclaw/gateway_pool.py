"""OpenClaw gateway pool: startup, leases, replenishment, quarantine, and shutdown.

Runner owns work dispatch and evidence persistence. This owner retains all gateway
resources and applies the configured fresh/reused-session policy.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from alphadiana.engine.config.experiment_config import (
    coerce_config_bool as _coerce_config_bool,
)
from alphadiana.engine.sandbox.base import session_failure_reason
from alphadiana.engine.sandbox.rock_ports import resolve_rock_ports_from_env

if TYPE_CHECKING:
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.engine.sandbox.base import SandboxSession
    from alphadiana.harness.base import Agent

logger = logging.getLogger(__name__)
OPENCLAW_CONCURRENCY_PER_SANDBOX = 1
_OPENCLAW_PROFILE_CACHE_PATH = Path(".cache/openclaw_startup_profiles.json")


def _has_openclaw_direct_gateway(config: "ExperimentConfig") -> bool:
    """Return whether OpenClaw should use an already-running gateway."""
    if config.agent_name != "openclaw":
        return False
    api_base = str(config.agent_config.get("api_base", "") or "").strip()
    gateway_pool = config.agent_config.get("gateway_pool", []) or []
    return bool(api_base or gateway_pool)


def is_openclaw_autodeploy(config: "ExperimentConfig") -> bool:
    if config.agent_name == "openclaw":
        if (
            str(config.agent_config.get("runtime_backend", "") or "").strip().lower()
            == "podman"
        ):
            return False
        return bool(
            not _has_openclaw_direct_gateway(config)
            and config.agent_config.get("rock_agent_config_path")
            and config.agent_config.get("openclaw_config_path")
        )
    return False


def stabilize_gateway_token(config: "ExperimentConfig") -> None:
    """Resolve one strong auto-deploy token shared by every runtime instance."""
    if not is_openclaw_autodeploy(config):
        return
    from alphadiana.harness.openclaw.security import (
        is_weak_openclaw_gateway_token,
        resolve_openclaw_gateway_token,
    )

    token = str(config.agent_config.get("gateway_token", "") or "")
    if is_weak_openclaw_gateway_token(token):
        config.agent_config["gateway_token"] = resolve_openclaw_gateway_token(token)


def _should_predeploy_openclaw(config: "ExperimentConfig", memory_scope: str) -> bool:
    """Use the fresh gateway pool only when state does not span work items."""
    return is_openclaw_autodeploy(config) and memory_scope not in {
        "cross_sample",
        "cross_task",
    }


def _make_gateway_runtime_manager(config: "ExperimentConfig"):
    if config.agent_name == "openclaw":
        from alphadiana.harness.openclaw.runtime import OpenClawRuntimeManager

        return OpenClawRuntimeManager(config.agent_config)
    raise RuntimeError(f"Unsupported gateway auto-deploy agent: {config.agent_name}")


def _build_openclaw_profile_cache_key(
    config: "ExperimentConfig", admin_base_url: str
) -> str:
    dataset = str(config.benchmark_config.get("dataset", ""))
    split = str(config.benchmark_config.get("split", ""))
    image = str(config.agent_config.get("rock_image", ""))
    model_name = str(
        config.agent_config.get(
            "OPENAI_MODEL_NAME", config.agent_config.get("model", "")
        )
    )
    return "|".join(
        [
            config.agent_name,
            config.benchmark_name,
            image,
            model_name,
            dataset,
            split,
            admin_base_url,
        ]
    )


def _load_cached_openclaw_profile(cache_key: str) -> tuple[str, float] | None:
    try:
        if not _OPENCLAW_PROFILE_CACHE_PATH.exists():
            return None
        payload = json.loads(_OPENCLAW_PROFILE_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        item = payload.get(cache_key)
        if not isinstance(item, dict):
            return None
        memory = str(item.get("memory", "")).strip()
        cpus = float(item.get("cpus", 0))
        if not memory or cpus <= 0:
            return None
        return memory, cpus
    except Exception:
        logger.debug("Failed to load OpenClaw startup profile cache", exc_info=True)
        return None


def _should_load_cached_openclaw_profile(
    config: "ExperimentConfig", requested_cpus: float
) -> bool:
    """Return whether a persisted OpenClaw startup profile may override config."""
    if requested_cpus <= 0:
        return False
    return bool(config.agent_config.get("use_cached_predeploy_startup_profile", True))


def _save_cached_openclaw_profile(cache_key: str, memory: str, cpus: float) -> None:
    try:
        _OPENCLAW_PROFILE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, dict[str, object]] = {}
        if _OPENCLAW_PROFILE_CACHE_PATH.exists():
            existing = json.loads(
                _OPENCLAW_PROFILE_CACHE_PATH.read_text(encoding="utf-8")
            )
            if isinstance(existing, dict):
                payload.update(existing)
        payload[cache_key] = {
            "memory": str(memory),
            "cpus": float(cpus),
            "updated_at": int(time.time()),
        }
        _OPENCLAW_PROFILE_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        logger.debug("Failed to save OpenClaw startup profile cache", exc_info=True)


class OpenClawGatewayPool:
    """Own gateway resources for one Runner invocation."""

    def __init__(
        self,
        config: ExperimentConfig,
        agent: Agent,
        cancel_event: threading.Event | None,
        work_item_count: int,
    ) -> None:
        self.config = config
        self.agent = agent
        self.cancel_event = cancel_event
        self._work_item_count = work_item_count
        self._sessions: list[SandboxSession] = []
        self._queue: queue.Queue[SandboxSession] | None = None
        self._live_ids: set[str] = set()
        self._by_id: dict[str, object] = {}
        self._proxies: dict[str, object] = {}
        self._gateway_urls: dict[str, str] = {}
        self._in_use_ids: set[str] = set()
        self._reset_lock = threading.Lock()
        self._pool_lock = threading.Lock()
        self._replacement_lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._replacements_in_progress = 0
        self._shutdown = threading.Event()
        self._deployer = None
        self._replacement_count = 0
        self._work_start_lock = threading.Lock()
        self._started_work_items = 0
        self._partial = False
        self._target_size = 1
        self._active_target = 1
        self._replenish_concurrency = 1
        self._replacement_slots = threading.BoundedSemaphore(1)
        self._reuse = _coerce_config_bool(
            self.config.agent_config.get("reuse_predeployed_sandboxes"), default=False
        )
        self._standby = max(
            0, int(self.config.agent_config.get("standby_sandboxes", 0) or 0)
        )
        self._reset_between_tasks = _coerce_config_bool(
            self.config.agent_config.get("reset_predeployed_between_tasks"),
            default=True,
        )
        self._lease_probe_enabled = _coerce_config_bool(
            self.config.agent_config.get("predeployed_lease_probe"), default=True
        )
        self._lease_probe_timeout = float(
            self.config.agent_config.get("predeployed_lease_probe_timeout", 2.0) or 0.0
        )
        self._fresh = False

    def prepare(
        self,
        *,
        sandbox_configured: bool,
        effective_max_concurrent: int,
        memory_scope: str,
    ) -> int:
        if not sandbox_configured and _should_predeploy_openclaw(
            self.config, memory_scope
        ):
            explicit_num = int(self.config.agent_config.get("num_sandboxes", 0) or 0)
            auto_num = (
                math.ceil(effective_max_concurrent / OPENCLAW_CONCURRENCY_PER_SANDBOX)
                if effective_max_concurrent > 1
                else 1
            )
            self._active_target = max(1, explicit_num or auto_num)
            self._fresh = not self._reuse
            self._target_size = self._active_target
            if self._fresh:
                self._target_size += self._standby
            explicit_replenish_concurrency = self.config.agent_config.get(
                "predeploy_replenish_concurrency"
            )
            if explicit_replenish_concurrency is None:
                default_replenish_concurrency = min(
                    self._active_target, self._standby or self._active_target
                )
                self._replenish_concurrency = max(1, default_replenish_concurrency)
            else:
                self._replenish_concurrency = max(
                    1, int(explicit_replenish_concurrency or 1)
                )
            self._replacement_slots = threading.BoundedSemaphore(
                self._replenish_concurrency
            )
            if self._target_size > 1 or self._fresh:
                deployment_results = []
                try:
                    from alphadiana.engine.components import SandboxRegistry

                    auto_sandbox_config = {
                        "admin_base_url": self.config.agent_config.get(
                            "admin_base_url",
                            self.config.agent_config.get("rock_admin_url", ""),
                        )
                        or resolve_rock_ports_from_env().base_url,
                        "proxy_base_url": self.config.agent_config.get(
                            "proxy_base_url",
                            self.config.agent_config.get("rock_proxy_url", ""),
                        )
                        or resolve_rock_ports_from_env().proxy_api_url,
                        "image": self.config.agent_config.get(
                            "rock_image", "python:3.11"
                        ),
                        "memory": self.config.agent_config.get("rock_memory", "2g"),
                        "cpus": float(self.config.agent_config.get("rock_cpus", 0.5)),
                        "limit_cpus": (
                            float(
                                self.config.agent_config.get(
                                    "rock_limit_cpus",
                                    self.config.agent_config.get("limit_cpus"),
                                )
                            )
                            if self.config.agent_config.get(
                                "rock_limit_cpus",
                                self.config.agent_config.get("limit_cpus"),
                            )
                            is not None
                            else None
                        ),
                        "startup_timeout": int(
                            self.config.agent_config.get("rock_startup_timeout", 300)
                        ),
                        "auto_clear_seconds": int(
                            self.config.agent_config.get(
                                "rock_auto_clear_seconds", 7200
                            )
                        ),
                        "start_retries": int(
                            self.config.agent_config.get("rock_start_retries", 3)
                        ),
                        "reset_between_tasks": False,
                        "proxy_timeout": int(
                            self.config.agent_config.get("proxy_timeout", 1800)
                        ),
                        "network_mode": self.config.agent_config.get(
                            "network_mode", None
                        ),
                        "use_kata_runtime": bool(
                            self.config.agent_config.get(
                                "rock_use_kata_runtime",
                                self.config.agent_config.get("use_kata_runtime", False),
                            )
                        ),
                    }
                    cache_key = _build_openclaw_profile_cache_key(
                        self.config, str(auto_sandbox_config["admin_base_url"])
                    )
                    cached_profile = None
                    if _should_load_cached_openclaw_profile(
                        self.config, float(auto_sandbox_config["cpus"])
                    ):
                        cached_profile = _load_cached_openclaw_profile(cache_key)
                    if cached_profile is not None:
                        auto_sandbox_config["memory"] = cached_profile[0]
                        auto_sandbox_config["cpus"] = cached_profile[1]
                        logger.info(
                            "Loaded persisted OpenClaw startup profile memory=%s cpus=%s",
                            cached_profile[0],
                            cached_profile[1],
                        )
                    rock_cls = SandboxRegistry.get("rock")
                    share_startup_profile = bool(
                        self.config.agent_config.get(
                            "share_predeploy_startup_profile", True
                        )
                    )
                    self._startup_profile: tuple[str, float] | None = None

                    def _deploy_one_gateway(sb_idx: int, *, label: str = "Predeploy"):
                        max_attempts = 3
                        last_error = None
                        for attempt in range(1, max_attempts + 1):
                            session = None
                            logprob_proxy = None
                            try:
                                logger.info(
                                    "%s sandbox %d attempt %d/%d",
                                    label,
                                    sb_idx + 1,
                                    attempt,
                                    max_attempts,
                                )
                                sandbox_backend = rock_cls()
                                sandbox_config = dict(auto_sandbox_config)
                                if (
                                    share_startup_profile
                                    and self._startup_profile is not None
                                ):
                                    sandbox_config["memory"] = self._startup_profile[0]
                                    sandbox_config["cpus"] = self._startup_profile[1]
                                    logger.info(
                                        "%s sandbox %d reusing startup profile memory=%s cpus=%s",
                                        label,
                                        sb_idx + 1,
                                        self._startup_profile[0],
                                        self._startup_profile[1],
                                    )
                                sandbox_backend.setup(sandbox_config)
                                session = sandbox_backend.create_session()
                                runtime_config = self.config.agent_config
                                start_logprob_proxy = getattr(
                                    self.agent, "start_logprob_proxy_for_gateway", None
                                )
                                if callable(start_logprob_proxy):
                                    started_proxy = start_logprob_proxy()
                                    if started_proxy is not None:
                                        (
                                            logprob_proxy,
                                            proxy_api_base,
                                            proxy_api_key,
                                        ) = started_proxy
                                        runtime_config = dict(self.config.agent_config)
                                        runtime_config.update(
                                            {
                                                "OPENAI_BASE_URL": proxy_api_base,
                                                "openai_base_url": proxy_api_base,
                                                "OPENAI_API_KEY": proxy_api_key,
                                                "openai_api_key": proxy_api_key,
                                            }
                                        )
                                runtime_manager = _make_gateway_runtime_manager(
                                    replace(self.config, agent_config=runtime_config)
                                )
                                info = runtime_manager.ensure_ready(session)
                                md = (
                                    session.metadata()
                                    if hasattr(session, "metadata")
                                    else {}
                                )
                                profile_memory = str(
                                    md.get("memory", sandbox_config["memory"])
                                )
                                profile_cpus = float(
                                    md.get("cpus", sandbox_config["cpus"])
                                )
                                return (
                                    session,
                                    info,
                                    (profile_memory, profile_cpus),
                                    logprob_proxy,
                                )
                            except Exception as exc:
                                last_error = exc
                                logger.warning(
                                    "%s sandbox %d attempt %d/%d failed: %s",
                                    label,
                                    sb_idx + 1,
                                    attempt,
                                    max_attempts,
                                    exc,
                                )
                                if logprob_proxy is not None:
                                    try:
                                        logprob_proxy.stop()
                                    except Exception:
                                        pass
                                if session is not None:
                                    try:
                                        session.close()
                                    except Exception:
                                        pass
                                if attempt == max_attempts:
                                    break
                                time.sleep(5)
                        assert last_error is not None
                        raise last_error

                    self._deployer = _deploy_one_gateway
                    logger.info(
                        "Predeploying %d %s sandboxes for CLI concurrency (max_concurrent=%d, target=%d tasks/sandbox, reuse=%s, standby=%d, replenish_concurrency=%d)",
                        self._target_size,
                        self.config.agent_name,
                        effective_max_concurrent,
                        OPENCLAW_CONCURRENCY_PER_SANDBOX,
                        self._reuse,
                        self._standby if self._fresh else 0,
                        self._replenish_concurrency if self._fresh else 1,
                    )
                    stagger_sec = float(
                        self.config.agent_config.get("predeploy_stagger_seconds", 2.0)
                        or 0.0
                    )
                    for i in range(self._target_size):
                        try:
                            deployed = _deploy_one_gateway(i)
                            deployment_results.append(deployed)
                            self._startup_profile = deployed[2]
                        except Exception as exc:
                            if not deployment_results:
                                raise
                            if self.config.strict_isolation:
                                raise RuntimeError(
                                    f"strict_isolation=true: predeploy stopped early after {len(deployment_results)}/{self._target_size} sandbox(es): {exc}"
                                ) from exc
                            logger.warning(
                                "Predeploy stopped early at sandbox %d/%d: %s. Continuing with %d predeployed sandbox(es).",
                                i + 1,
                                self._target_size,
                                exc,
                                len(deployment_results),
                            )
                            self._partial = True
                            break
                        if stagger_sec > 0 and i + 1 < self._target_size:
                            time.sleep(stagger_sec)
                    self._sessions = [
                        session for (session, _, _, _) in deployment_results
                    ]
                    gateway_pool = [
                        info["api_base"] for (_, info, _, _) in deployment_results
                    ]
                    self._by_id = {
                        str(getattr(session, "sandbox_id", "")): session
                        for session in self._sessions
                        if str(getattr(session, "sandbox_id", ""))
                    }
                    self._gateway_urls = {
                        str(getattr(session, "sandbox_id", "")): str(info["api_base"])
                        for (session, info, _, _) in deployment_results
                        if str(getattr(session, "sandbox_id", ""))
                    }
                    self._proxies = {
                        str(getattr(session, "sandbox_id", "")): proxy
                        for (session, _, _, proxy) in deployment_results
                        if str(getattr(session, "sandbox_id", "")) and proxy is not None
                    }
                    if self._fresh:
                        effective_capacity = max(
                            1, min(self._active_target, len(gateway_pool))
                        )
                    else:
                        effective_capacity = max(
                            1, len(gateway_pool) * OPENCLAW_CONCURRENCY_PER_SANDBOX
                        )
                    if effective_max_concurrent > effective_capacity:
                        logger.warning(
                            "Lowering max_concurrent from %d to %d due to available sandbox capacity.",
                            effective_max_concurrent,
                            effective_capacity,
                        )
                        self.config.max_concurrent = effective_capacity
                        effective_max_concurrent = effective_capacity
                    self.config.agent_config["gateway_pool"] = gateway_pool
                    if self.config.agent_name == "openclaw":
                        self.config.agent_config["api_base"] = gateway_pool[0]
                    else:
                        self.config.agent_config["gateway_api_base"] = gateway_pool[0]
                    self.config.agent_config["sandbox_id"] = self._sessions[
                        0
                    ].sandbox_id
                    self.config.agent_config["rock_sandbox_url"] = (
                        self._sessions[0].metadata().get("proxy_base_url", "")
                    )
                    if self._gateway_urls:
                        self.config.agent_config[
                            "_predeployed_gateway_api_base_by_sandbox_id"
                        ] = self._gateway_urls
                    if self._proxies:
                        self.config.agent_config["_predeployed_logprob_proxies"] = (
                            self._proxies
                        )
                    if self._startup_profile is not None:
                        _save_cached_openclaw_profile(
                            cache_key,
                            self._startup_profile[0],
                            self._startup_profile[1],
                        )
                        logger.info(
                            "Persisted %s startup profile memory=%s cpus=%s",
                            self.config.agent_name,
                            self._startup_profile[0],
                            self._startup_profile[1],
                        )
                    self.agent.setup(self.config.agent_config)
                    logger.info(
                        "%s gateway_pool ready with %d sandboxes: %s",
                        self.config.agent_name,
                        len(gateway_pool),
                        gateway_pool,
                    )
                except Exception as exc:
                    for session, _, _, proxy in deployment_results:
                        if proxy is not None:
                            try:
                                proxy.stop()
                            except Exception:
                                pass
                        try:
                            session.close()
                        except Exception:
                            pass
                    if self.config.strict_isolation:
                        raise RuntimeError(
                            f"strict_isolation=true: failed to predeploy {self._target_size} {self.config.agent_name} sandbox(es): {exc}"
                        ) from exc
                    logger.warning(
                        "Failed to predeploy %d %s sandboxes: %s. Falling back to single-sandbox auto-deploy.",
                        self._target_size,
                        self.config.agent_name,
                        exc,
                    )
                    for session in self._sessions:
                        try:
                            session.close()
                        except Exception:
                            pass
                    self._sessions = []
        return effective_max_concurrent

    def activate(self) -> None:
        self._queue = None
        self._live_ids: set[str] = set()
        if self._sessions:
            self._queue = queue.Queue()
            for session in self._sessions:
                self._queue.put(session)
                sandbox_id = str(getattr(session, "sandbox_id", "") or "")
                if sandbox_id:
                    self._live_ids.add(sandbox_id)

    @property
    def active(self) -> bool:
        return bool(self._sessions)

    @property
    def enabled(self) -> bool:
        return self._queue is not None

    @property
    def isolation_mode(self) -> str:
        if self._fresh:
            return (
                "partial_fresh_predeployed_pool"
                if self._partial
                else "fresh_predeployed_pool"
            )
        return "partial_predeploy" if self._partial else "predeployed_pool"

    @property
    def metadata(self) -> dict:
        return {
            "reuse_predeployed_sandboxes": self._reuse,
            "standby_sandboxes": self._standby if self._fresh else 0,
            "predeploy_replenish_concurrency": (
                self._replenish_concurrency if self._fresh else 1
            ),
        }

    def task_started(self) -> None:
        if self._fresh and self.enabled:
            with self._work_start_lock:
                self._started_work_items += 1

    def reset(self, sandbox_id: str, task_id: str) -> bool:
        if self._fresh or not self._by_id or not sandbox_id:
            return True
        return self._reset_reused_session(sandbox_id, task_id)

    def release(
        self, session, sandbox_id: str, task_id: str, *, quarantine_reason: str
    ) -> None:
        if quarantine_reason:
            self._quarantine(session, sandbox_id, quarantine_reason, task_id)
        elif self._fresh:
            self._retire(session, sandbox_id, task_id)
        else:
            self._queue.put(session)

    def close(self) -> None:
        self._shutdown.set()
        for thread in list(self._threads):
            thread.join(timeout=0.1)
        for session in list(self._sessions):
            try:
                session.close()
            except Exception as exc:
                logger.warning("Predeployed sandbox close error: %s", exc)

    def _live_count(self) -> int:
        with self._pool_lock:
            return len(self._live_ids)

    def _snapshot(self) -> dict[str, int]:
        with self._work_start_lock:
            remaining_not_started = max(
                0, self._work_item_count - self._started_work_items
            )
        with self._pool_lock:
            live_count = len(self._live_ids)
            in_use_count = len(self._in_use_ids)
            ready_count = max(0, live_count - in_use_count)
        with self._replacement_lock:
            in_progress = self._replacements_in_progress
        target_live = min(self._target_size, remaining_not_started + in_use_count)
        deficit = max(0, target_live - live_count - in_progress)
        return {
            "remaining_not_started": remaining_not_started,
            "live": live_count,
            "in_use": in_use_count,
            "ready": ready_count,
            "in_progress": in_progress,
            "target_live": target_live,
            "deficit": deficit,
        }

    def _drop_agent_maps(self, sandbox_id: str, proxy: object | None) -> None:
        agent_gateway_map = getattr(
            self.agent, "_predeployed_gateway_api_base_by_sandbox_id", None
        )
        if isinstance(agent_gateway_map, dict):
            agent_gateway_map.pop(sandbox_id, None)
        agent_proxy_map = getattr(self.agent, "_predeployed_logprob_proxies", None)
        if isinstance(agent_proxy_map, dict):
            agent_proxy_map.pop(sandbox_id, None)
        agent_proxies = getattr(self.agent, "_logprob_proxies", None)
        if proxy is not None and isinstance(agent_proxies, list):
            try:
                agent_proxies.remove(proxy)
            except ValueError:
                pass

    def _drop_gateway_entry(self, api_base: str) -> None:
        api_base = str(api_base or "").strip()
        if not api_base:
            return
        gateway_pool_config = self.config.agent_config.get("gateway_pool")
        if isinstance(gateway_pool_config, list):
            gateway_pool_config[:] = [
                item for item in gateway_pool_config if str(item).strip() != api_base
            ]
        agent_gateway_pool = getattr(self.agent, "_gateway_pool", None)
        if agent_gateway_pool is not None:
            try:
                remaining = [
                    item
                    for item in list(agent_gateway_pool)
                    if str(item).strip() != api_base
                ]
                agent_gateway_pool.clear()
                agent_gateway_pool.extend(remaining)
            except Exception:
                logger.debug(
                    "Failed to drop stale predeployed gateway pool entry", exc_info=True
                )

    def _unregister(
        self, session: object, sandbox_id: str
    ) -> tuple[str, object | None]:
        resolved_sandbox_id = str(
            sandbox_id or getattr(session, "sandbox_id", "") or ""
        )
        proxy = None
        api_base = ""
        with self._pool_lock:
            if resolved_sandbox_id:
                self._live_ids.discard(resolved_sandbox_id)
                self._in_use_ids.discard(resolved_sandbox_id)
                self._by_id.pop(resolved_sandbox_id, None)
                api_base = self._gateway_urls.pop(resolved_sandbox_id, "")
                proxy = self._proxies.pop(resolved_sandbox_id, None)
                self._drop_agent_maps(resolved_sandbox_id, proxy)
                gateway_map = self.config.agent_config.get(
                    "_predeployed_gateway_api_base_by_sandbox_id"
                )
                if isinstance(gateway_map, dict):
                    gateway_map.pop(resolved_sandbox_id, None)
                proxy_map = self.config.agent_config.get("_predeployed_logprob_proxies")
                if isinstance(proxy_map, dict):
                    proxy_map.pop(resolved_sandbox_id, None)
            try:
                self._sessions.remove(session)
            except ValueError:
                pass
        self._drop_gateway_entry(api_base)
        return (resolved_sandbox_id, proxy)

    def _register_replacement(
        self,
        session: object,
        info: dict,
        proxy: object | None,
        reason: str,
        task_id: str,
    ) -> bool:
        if self._queue is None:
            return False
        sandbox_id = str(
            getattr(session, "sandbox_id", "") or info.get("sandbox_id", "") or ""
        )
        api_base = str(info.get("api_base", "") or "")
        if self._shutdown.is_set() or not sandbox_id or (not api_base):
            logger.warning(
                "Replacement predeployed sandbox for task %s is no longer needed or missing sandbox_id/api_base; closing it",
                task_id,
            )
            try:
                stop_proxy = getattr(proxy, "stop", None)
                if callable(stop_proxy):
                    stop_proxy()
            except Exception:
                logger.debug(
                    "Failed to stop incomplete replacement logprob proxy", exc_info=True
                )
            try:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            except Exception:
                logger.debug(
                    "Failed to close incomplete replacement session", exc_info=True
                )
            return False
        with self._pool_lock:
            self._sessions.append(session)
            self._by_id[sandbox_id] = session
            self._gateway_urls[sandbox_id] = api_base
            self._live_ids.add(sandbox_id)
            gateway_pool_config = self.config.agent_config.setdefault(
                "gateway_pool", []
            )
            if (
                isinstance(gateway_pool_config, list)
                and api_base not in gateway_pool_config
            ):
                gateway_pool_config.append(api_base)
            gateway_map = self.config.agent_config.setdefault(
                "_predeployed_gateway_api_base_by_sandbox_id", {}
            )
            if isinstance(gateway_map, dict):
                gateway_map[sandbox_id] = api_base
            if proxy is not None:
                self._proxies[sandbox_id] = proxy
                proxy_map = self.config.agent_config.setdefault(
                    "_predeployed_logprob_proxies", {}
                )
                if isinstance(proxy_map, dict):
                    proxy_map[sandbox_id] = proxy
            agent_gateway_map = getattr(
                self.agent, "_predeployed_gateway_api_base_by_sandbox_id", None
            )
            if isinstance(agent_gateway_map, dict):
                agent_gateway_map[sandbox_id] = api_base
            agent_gateway_pool = getattr(self.agent, "_gateway_pool", None)
            if agent_gateway_pool is not None and api_base not in list(
                agent_gateway_pool
            ):
                try:
                    agent_gateway_pool.append(api_base)
                except Exception:
                    logger.debug(
                        "Failed to append replacement gateway to agent pool",
                        exc_info=True,
                    )
            if proxy is not None:
                agent_proxy_map = getattr(
                    self.agent, "_predeployed_logprob_proxies", None
                )
                if isinstance(agent_proxy_map, dict):
                    agent_proxy_map[sandbox_id] = proxy
                agent_proxies = getattr(self.agent, "_logprob_proxies", None)
                if isinstance(agent_proxies, list) and proxy not in agent_proxies:
                    agent_proxies.append(proxy)
            remaining = len(self._live_ids)
        self._queue.put(session)
        logger.info(
            "Replacement predeployed sandbox ready for task %s sandbox_id=%s reason=%s live=%d",
            task_id,
            sandbox_id,
            reason,
            remaining,
        )
        return True

    def _replacement_in_progress_count(self) -> int:
        with self._replacement_lock:
            return self._replacements_in_progress

    def _create_replacement(
        self,
        reason: str,
        task_id: str,
        *,
        force: bool = False,
        require_fresh_need: bool = True,
    ) -> bool:
        if self._deployer is None or self._shutdown.is_set():
            return False
        if not self._replacement_slots.acquire(blocking=False):
            logger.info(
                "Skipping replacement predeploy for task %s reason=%s: replenish concurrency limit reached (%d)",
                task_id,
                reason,
                self._replenish_concurrency,
            )
            return False
        with self._replacement_lock:
            self._replacements_in_progress += 1
        try:
            if self._shutdown.is_set():
                return False
            if self._fresh and require_fresh_need and (not self._needs_replenishment()):
                return True
            if not self._fresh and (not force) and (self._live_count() > 0):
                return True
            with self._replacement_lock:
                self._replacement_count += 1
                replacement_idx = self._target_size + self._replacement_count - 1
            try:
                deployed = self._deployer(
                    replacement_idx, label="Replacement predeploy"
                )
            except Exception as exc:
                logger.error(
                    "Failed to create replacement predeployed sandbox for task %s reason=%s: %s",
                    task_id,
                    reason,
                    exc,
                )
                return False
            (session, info, profile, proxy) = deployed
            self._startup_profile = profile
            return self._register_replacement(session, info, proxy, reason, task_id)
        finally:
            with self._replacement_lock:
                self._replacements_in_progress = max(
                    0, self._replacements_in_progress - 1
                )
            try:
                self._replacement_slots.release()
            except ValueError:
                logger.debug(
                    "Replacement predeploy slot release overflow", exc_info=True
                )

    def _start_replacement(
        self,
        reason: str,
        task_id: str,
        *,
        force: bool = True,
        require_fresh_need: bool = True,
    ) -> bool:
        if self._deployer is None or self._shutdown.is_set():
            return False

        def _target() -> None:
            self._create_replacement(
                reason, task_id, force=force, require_fresh_need=require_fresh_need
            )

        thread = threading.Thread(
            target=_target, name=f"predeploy-replenish-{task_id}", daemon=True
        )
        self._threads.append(thread)
        thread.start()
        return True

    def _replenish(self, reason: str, task_id: str) -> int:
        if not self._fresh or self._deployer is None or self._shutdown.is_set():
            return 0
        snapshot = self._snapshot()
        in_progress = snapshot["in_progress"]
        available_slots = max(0, self._replenish_concurrency - in_progress)
        to_start = min(snapshot["deficit"], available_slots)
        if to_start <= 0:
            return 0
        logger.info(
            "Starting %d OpenClaw predeploy replenishment(s) for task %s reason=%s live=%d ready=%d in_use=%d in_progress=%d target=%d deficit=%d replenish_concurrency=%d",
            to_start,
            task_id,
            reason,
            snapshot["live"],
            snapshot["ready"],
            snapshot["in_use"],
            in_progress,
            snapshot["target_live"],
            snapshot["deficit"],
            self._replenish_concurrency,
        )
        started = 0
        for _ in range(to_start):
            if self._start_replacement(reason, task_id, force=True):
                started += 1
        return started

    def _probe_before_lease(self, session: object, task_id: str) -> str:
        if not self._lease_probe_enabled or self._lease_probe_timeout <= 0:
            return ""
        has_published_probe = callable(
            getattr(session, "published_base", None)
        ) or callable(getattr(session, "published_port", None))
        if not has_published_probe:
            return ""
        sandbox_id = str(getattr(session, "sandbox_id", "") or "")
        if not sandbox_id:
            return ""
        with self._pool_lock:
            api_base = str(self._gateway_urls.get(sandbox_id, "") or "")
        if not api_base:
            return "gateway_missing_api_base"
        probe_url = f"{api_base.rstrip('/')}/models"
        try:
            response = httpx.get(
                probe_url, timeout=self._lease_probe_timeout, trust_env=False
            )
            if response.status_code >= 500:
                return f"gateway_probe_http_{response.status_code}"
            return ""
        except Exception as exc:
            reason = session_failure_reason(exc)
            if reason:
                return reason
            return "gateway_probe_failed"

    def _discard_unhealthy(self, session: object, reason: str, task_id: str) -> None:
        sandbox_id = str(getattr(session, "sandbox_id", "") or "")
        (resolved_sandbox_id, proxy) = self._unregister(session, sandbox_id)
        logger.warning(
            "Discarding unhealthy predeployed sandbox before task %s sandbox_id=%s reason=%s",
            task_id,
            resolved_sandbox_id or "<unknown>",
            reason,
        )
        if proxy is not None:
            try:
                proxy.stop()
            except Exception:
                logger.debug(
                    "Failed to stop logprob proxy for unhealthy sandbox_id=%s",
                    resolved_sandbox_id,
                    exc_info=True,
                )
        try:
            close = getattr(session, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            logger.warning(
                "Failed to close unhealthy predeployed sandbox_id=%s: %s",
                resolved_sandbox_id or "<unknown>",
                exc,
            )
        if (
            self._fresh
            and (not self._shutdown.is_set())
            and (not (self.cancel_event is not None and self.cancel_event.is_set()))
        ):
            self._replenish(reason, task_id)

    def acquire(self, task_id: str) -> SandboxSession:
        if self._queue is None:
            raise RuntimeError("predeployed session queue is not configured")
        while True:
            try:
                session = self._queue.get(timeout=1.0)
                lease_probe_reason = self._probe_before_lease(session, task_id)
                if lease_probe_reason:
                    self._discard_unhealthy(session, lease_probe_reason, task_id)
                    continue
                if self._fresh:
                    sandbox_id = str(getattr(session, "sandbox_id", "") or "")
                    if sandbox_id:
                        with self._pool_lock:
                            self._in_use_ids.add(sandbox_id)
                return session
            except queue.Empty:
                if self._fresh:
                    self._replenish("predeployed_queue_wait", task_id)
                if self._fresh and self._replacement_in_progress_count() > 0:
                    continue
                if self._live_count() == 0:
                    if self._create_replacement(
                        "predeployed_pool_depleted",
                        task_id,
                        force=True,
                        require_fresh_need=False,
                    ):
                        continue
                    raise RuntimeError(
                        "No live predeployed sandbox sessions remain; restart the run to create a fresh gateway pool"
                    )
                if self.cancel_event is not None and self.cancel_event.is_set():
                    raise RuntimeError(
                        f"Cancelled while waiting for predeployed sandbox for task {task_id}"
                    )

    def _needs_replenishment(self, *, replacement_in_progress_offset: int = 0) -> bool:
        snapshot = self._snapshot()
        adjusted_deficit = (
            snapshot["target_live"]
            - snapshot["live"]
            - max(0, snapshot["in_progress"] - replacement_in_progress_offset)
        )
        return adjusted_deficit > 0

    def _quarantine(
        self, session: object, sandbox_id: str, reason: str, task_id: str
    ) -> None:
        (resolved_sandbox_id, proxy) = self._unregister(session, sandbox_id)
        remaining = self._live_count()
        logger.warning(
            "Quarantining predeployed sandbox for task %s sandbox_id=%s reason=%s remaining=%d",
            task_id,
            resolved_sandbox_id or "<unknown>",
            reason,
            remaining,
        )
        if proxy is not None:
            try:
                proxy.stop()
            except Exception:
                logger.debug(
                    "Failed to stop logprob proxy for quarantined sandbox_id=%s",
                    resolved_sandbox_id,
                    exc_info=True,
                )
        try:
            close = getattr(session, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            logger.warning(
                "Failed to close quarantined predeployed sandbox_id=%s: %s",
                resolved_sandbox_id or "<unknown>",
                exc,
            )
        if not (self.cancel_event is not None and self.cancel_event.is_set()):
            if self._fresh and self._needs_replenishment():
                self._replenish(reason, task_id)
            elif not self._fresh:
                self._create_replacement(reason, task_id, force=True)

    def _retire(self, session: object, sandbox_id: str, task_id: str) -> None:
        (resolved_sandbox_id, proxy) = self._unregister(session, sandbox_id)
        replenish = self._needs_replenishment()
        logger.info(
            "Closing fresh predeployed sandbox for completed task %s sandbox_id=%s replenish=%s",
            task_id,
            resolved_sandbox_id or "<unknown>",
            replenish,
        )
        if proxy is not None:
            try:
                proxy.stop()
            except Exception:
                logger.debug(
                    "Failed to stop logprob proxy for retired sandbox_id=%s",
                    resolved_sandbox_id,
                    exc_info=True,
                )
        try:
            close = getattr(session, "close", None)
            if callable(close):
                close()
        except Exception as exc:
            logger.warning(
                "Failed to close fresh predeployed sandbox_id=%s: %s",
                resolved_sandbox_id or "<unknown>",
                exc,
            )
        if (
            replenish
            and (not self._shutdown.is_set())
            and (not (self.cancel_event is not None and self.cancel_event.is_set()))
        ):
            self._replenish("fresh_predeployed_task_complete", task_id)

    def _reset_reused_session(self, sandbox_id: str, task_id: str) -> bool:
        if not self._reset_between_tasks or not sandbox_id:
            return True
        session = self._by_id.get(sandbox_id)
        if session is None:
            return True
        with self._reset_lock:
            try:
                reset = getattr(session, "reset", None)
                if callable(reset):
                    reset()
                return True
            except Exception as exc:
                logger.warning(
                    "Predeployed session reset failed for task %s sandbox_id=%s: %s",
                    task_id,
                    sandbox_id,
                    exc,
                )
                return False
