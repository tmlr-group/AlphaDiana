"""Manage ROCK sandboxes for OpenClaw deployment."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from alphadiana.utils.rock_ports import resolve_rock_ports_from_env
from alphadiana.utils.rock_runtime import (
    DEFAULT_SANDBOX_IMAGE,
    PREBUILT_SANDBOX_IMAGE,
    configure_rock_runtime_for_image,
    get_custom_install_cmd,
    is_prebuilt_image,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEPLOY_DIR = PROJECT_ROOT / "openclaw_deploy"


ROCK_PORTS_ENV_FILE = PROJECT_ROOT / "dev" / ".rock_ports.env"

# Cache for resolved working ROCK URLs
_resolved_rock_urls: tuple[str, str] | None = None


def _load_rock_ports_env() -> None:
    """Source dev/.rock_ports.env into os.environ if present and not already set."""
    if not ROCK_PORTS_ENV_FILE.exists():
        return
    for line in ROCK_PORTS_ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if "${" in value:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        if key and not os.environ.get(key):
            os.environ[key] = value


# Load ROCK port config on module import
_load_rock_ports_env()


def _probe_url(url: str, timeout: float = 3.0, check_body: bool = False) -> bool:
    """Synchronously check if a URL is reachable and returns a successful response.

    If check_body is True, also verify the JSON body doesn't contain
    a ROCK-style failure (status=Failed).
    """
    import json as _json
    import urllib.request
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not check_body:
                return True
            body = resp.read()
            data = _json.loads(body)
            if isinstance(data, dict) and data.get("status") == "Failed":
                return False
            return True
    except Exception:
        return False


def _get_rock_urls() -> tuple[str, str]:
    """Return (base_url, proxy_url), probing paired admin/proxy candidates.

    ROCK admin and proxy MUST be from the same cluster (sharing one Redis).
    By convention, proxy port = admin port + 1 (e.g., admin 9020 ↔ proxy 9021).
    We probe (admin, proxy) pairs together to ensure they're from the same cluster.
    """
    global _resolved_rock_urls
    if _resolved_rock_urls is not None:
        return _resolved_rock_urls

    ports = resolve_rock_ports_from_env()

    # Build candidate (admin_port, proxy_port) pairs.
    # Each ROCK cluster has admin=N, proxy=N+1.
    seen_admin_ports: set[int] = set()
    pairs: list[tuple[str, str]] = []

    def _add_pair(admin_url: str, proxy_api_url: str) -> None:
        """Add a pair if not already seen."""
        import re
        m = re.search(r":(\d+)", admin_url)
        if m:
            port = int(m.group(1))
            if port in seen_admin_ports:
                return
            seen_admin_ports.add(port)
        pairs.append((admin_url, proxy_api_url))

    # Priority 1: env-configured pair
    env_base = os.environ.get("ROCK_BASE_URL", "")
    env_proxy = os.environ.get("ROCK_PROXY_URL", "")
    if env_base and env_proxy:
        _add_pair(env_base, env_proxy)

    # Priority 2: resolved from .rock_ports.env
    _add_pair(ports.base_url, ports.proxy_api_url)

    # Priority 3: common port pairs (admin, proxy=admin+1)
    # Honour ROCK_ADMIN_PORT from env if set (prepend it so it takes precedence).
    _env_admin_port = os.environ.get("ROCK_ADMIN_PORT")
    _fallback_ports = [int(_env_admin_port)] + [9020, 9010, 9000] if _env_admin_port else [9020, 9010, 9000]
    for admin_port in _fallback_ports:
        _add_pair(
            f"http://127.0.0.1:{admin_port}",
            f"http://127.0.0.1:{admin_port + 1}/apis/envs/sandbox/v1",
        )

    # Find first pair where BOTH admin and proxy are reachable and proxy returns Success
    for admin_url, proxy_url in pairs:
        if not _probe_url(admin_url):
            continue
        if not _probe_url(f"{proxy_url}/sandboxes", check_body=True):
            continue
        logger.info("ROCK cluster found: admin=%s proxy=%s", admin_url, proxy_url)
        _resolved_rock_urls = (admin_url, proxy_url)
        return _resolved_rock_urls

    # Fallback: use first reachable admin with its paired proxy
    for admin_url, proxy_url in pairs:
        if _probe_url(admin_url):
            logger.warning("Using admin %s (proxy %s may not be working)", admin_url, proxy_url)
            _resolved_rock_urls = (admin_url, proxy_url)
            return _resolved_rock_urls

    # Last resort: return first candidates
    _resolved_rock_urls = (pairs[0][0], pairs[0][1]) if pairs else (
        "http://127.0.0.1:9020",
        "http://127.0.0.1:9021/apis/envs/sandbox/v1",
    )
    return _resolved_rock_urls


class SandboxInfo:
    def __init__(
        self,
        sandbox_id: str,
        status: str = "unknown",
        api_base: str = "",
        model: str = "",
        created_at: str = "",
        gateway_ready: bool = False,
    ):
        self.sandbox_id = sandbox_id
        self.status = status
        self.api_base = api_base
        self.model = model
        self.created_at = created_at
        self.gateway_ready = gateway_ready

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "status": self.status,
            "api_base": self.api_base,
            "model": self.model,
            "created_at": self.created_at,
            "gateway_ready": self.gateway_ready,
        }


class DeployJob:
    def __init__(self, deploy_id: str):
        self.deploy_id = deploy_id
        self.status: str = "pending"  # pending, deploying, completed, failed
        self.sandbox_id: str = ""
        self.api_base: str = ""
        self.gateway_pool: list[str] = []  # api_bases for all sandboxes when num_sandboxes > 1
        self.error: str = ""
        self.created_at: str = datetime.now(timezone.utc).isoformat()
        self.logs: io.StringIO = io.StringIO()
        self.model_name: str = ""
        self.model_api_base: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "deploy_id": self.deploy_id,
            "status": self.status,
            "sandbox_id": self.sandbox_id,
            "api_base": self.api_base,
            "gateway_pool": self.gateway_pool,
            "error": self.error,
            "created_at": self.created_at,
            "model_name": self.model_name,
            "model_api_base": self.model_api_base,
        }


class SandboxManager:
    """Manages ROCK sandbox lifecycle for OpenClaw."""

    def __init__(self) -> None:
        self._deploy_jobs: dict[str, DeployJob] = {}
        self._lock = threading.Lock()

    async def list_sandboxes(self) -> list[SandboxInfo]:
        """List existing sandboxes from ROCK proxy API, with gateway health check."""
        _, proxy_url = _get_rock_urls()
        sandboxes: list[SandboxInfo] = []
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{proxy_url}/sandboxes")
                resp.raise_for_status()
                data = resp.json()

                # ROCK response format: {status, result: {items: [...]}}
                items: list = []
                if isinstance(data, dict):
                    result = data.get("result")
                    if isinstance(result, dict):
                        items = result.get("items", [])
                    elif isinstance(result, list):
                        items = result
                    else:
                        # Fallback: try data directly or data.data
                        items = data.get("data", data.get("items", []))
                if isinstance(data, list):
                    items = data
                if not isinstance(items, list):
                    items = []

                for item in items:
                    sid = item.get("sandbox_id", item.get("id", ""))
                    if not sid:
                        continue
                    # ROCK uses "state" (running/pending), not "status"
                    state = item.get("state", item.get("status", "unknown"))
                    if isinstance(state, dict):
                        state = "unknown"
                    api_base = f"{proxy_url}/sandboxes/{sid}/proxy/v1"
                    sandboxes.append(SandboxInfo(
                        sandbox_id=sid,
                        status=state,
                        api_base=api_base,
                        model=item.get("model", ""),
                        created_at=item.get("created_at", ""),
                    ))

                # Enrich with model info from deploy jobs (in-memory, instant)
                self._enrich_sandbox_model_info(sandboxes)

        except Exception as exc:
            logger.warning("Failed to list sandboxes from ROCK: %s", exc)
        return sandboxes

    async def probe_sandbox(self, sandbox_id: str) -> dict[str, Any]:
        """Probe a single sandbox for gateway health and model info.

        Returns {"gateway_ready": bool, "model": str}.
        Uses subprocess curl for gateway probe to avoid event-loop contention
        with long-running evaluation tasks.
        """
        import json as _json

        _, proxy_url = _get_rock_urls()
        api_base = f"{proxy_url}/sandboxes/{sandbox_id}/proxy/v1"
        result: dict[str, Any] = {"gateway_ready": False, "model": ""}

        # Read model info from container
        sb_for_model = SandboxInfo(sandbox_id=sandbox_id, status="running")
        await self._read_model_from_containers([sb_for_model])
        result["model"] = sb_for_model.model

        # Probe gateway via curl subprocess (avoids httpx event-loop issues)
        try:
            proc = await asyncio.create_subprocess_exec(
                "curl", "-s", "--max-time", "15",
                "-X", "POST",
                f"{api_base}/chat/completions",
                "-H", "Authorization: bearer OPENCLAW",
                "-H", "Content-Type: application/json",
                "-d", _json.dumps({
                    "model": "openclaw",
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=20.0)
            if proc.returncode == 0 and stdout:
                body = _json.loads(stdout)
                if isinstance(body, dict) and body.get("status") == "Failed":
                    result["gateway_ready"] = False
                elif isinstance(body, dict) and body.get("choices"):
                    result["gateway_ready"] = True
        except Exception as exc:
            logger.debug("Gateway probe %s via curl failed: %s", sandbox_id, exc)

        return result

    def _enrich_sandbox_model_info(self, sandboxes: list[SandboxInfo]) -> None:
        """Fill in model name from deploy job records for each sandbox."""
        with self._lock:
            # Build sandbox_id -> deploy job lookup
            sid_to_deploy: dict[str, DeployJob] = {}
            for dj in self._deploy_jobs.values():
                if dj.sandbox_id:
                    sid_to_deploy[dj.sandbox_id] = dj

        for sb in sandboxes:
            dj = sid_to_deploy.get(sb.sandbox_id)
            if dj:
                sb.model = dj.model_name or sb.model
                if not sb.model and dj.model_api_base:
                    sb.model = dj.model_api_base

    @staticmethod
    async def _read_model_from_containers(sandboxes: list[SandboxInfo]) -> None:
        """Read model info from ~/.openclaw/agents/main/agent/models.json inside containers."""
        import json as _json

        async def _read_one(sb: SandboxInfo) -> None:
            if sb.model or sb.status != "running":
                return
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "exec", sb.sandbox_id,
                    "cat", "/root/.openclaw/agents/main/agent/models.json",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
                if proc.returncode != 0 or not stdout:
                    return
                data = _json.loads(stdout)
                # Extract first model name from providers
                for provider in data.get("providers", {}).values():
                    models = provider.get("models", [])
                    if models:
                        sb.model = models[0].get("name") or models[0].get("id", "")
                        return
            except Exception:
                pass

        await asyncio.gather(*[_read_one(sb) for sb in sandboxes])

    async def _probe_sandbox_gateways(
        self,
        client: httpx.AsyncClient,
        sandboxes: list[SandboxInfo],
    ) -> None:
        """Probe gateway /chat/completions for each sandbox to check if it's ready."""

        async def _probe_one(sb: SandboxInfo) -> None:
            if sb.status != "running":
                return
            try:
                resp = await client.post(
                    f"{sb.api_base}/chat/completions",
                    headers={
                        "Authorization": "bearer OPENCLAW",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "openclaw",
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 1,
                    },
                    timeout=30.0,
                )
                body = resp.json() if resp.status_code == 200 else {}
                if isinstance(body, dict) and body.get("status") == "Failed":
                    sb.gateway_ready = False
                    logger.debug("Gateway probe %s: ROCK returned Failed: %s", sb.sandbox_id, body.get("error", ""))
                elif resp.status_code == 200 and body.get("choices"):
                    sb.gateway_ready = True
                else:
                    logger.debug("Gateway probe %s: unexpected response status=%s body=%s", sb.sandbox_id, resp.status_code, body)
            except Exception as exc:
                logger.debug("Gateway probe %s: exception: %s", sb.sandbox_id, exc)
                sb.gateway_ready = False

        await asyncio.gather(*[_probe_one(sb) for sb in sandboxes])

    def list_deploy_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.to_dict() for j in reversed(self._deploy_jobs.values())]

    def get_deploy_job(self, deploy_id: str) -> DeployJob | None:
        with self._lock:
            return self._deploy_jobs.get(deploy_id)

    def get_deploy_logs(self, deploy_id: str) -> str | None:
        with self._lock:
            job = self._deploy_jobs.get(deploy_id)
            if job is None:
                return None
            return job.logs.getvalue()

    def deploy_sandbox(
        self,
        agent_config_path: str | None = None,
        model_api_base: str = "",
        model_api_key: str = "",
        model_name: str = "",
        auto_clear_seconds: int = 28800,
        num_sandboxes: int = 1,
    ) -> DeployJob:
        """Start sandbox deployment in a background thread."""
        deploy_id = uuid.uuid4().hex[:12]
        job = DeployJob(deploy_id)
        job.model_name = model_name
        job.model_api_base = model_api_base
        with self._lock:
            self._deploy_jobs[deploy_id] = job

        thread = threading.Thread(
            target=self._run_deploy,
            args=(deploy_id, agent_config_path, model_api_base, model_api_key, model_name, auto_clear_seconds, num_sandboxes),
            daemon=True,
        )
        thread.start()
        return job

    def _run_deploy(
        self,
        deploy_id: str,
        agent_config_path: str | None,
        model_api_base: str,
        model_api_key: str,
        model_name: str,
        auto_clear_seconds: int,
        num_sandboxes: int = 1,
    ) -> None:
        """Execute deploy in background thread."""
        with self._lock:
            job = self._deploy_jobs[deploy_id]
            job.status = "deploying"

        def log(msg: str) -> None:
            ts = datetime.now().strftime("%H:%M:%S")
            line = f"{ts} {msg}"
            logger.info("[deploy:%s] %s", deploy_id, msg)
            with self._lock:
                job.logs.write(line + "\n")

        try:
            asyncio.run(self._async_deploy(
                deploy_id, agent_config_path, model_api_base, model_api_key,
                model_name, auto_clear_seconds, log, num_sandboxes,
            ))
        except Exception as exc:
            logger.exception("Deploy %s failed", deploy_id)
            with self._lock:
                job.status = "failed"
                job.error = str(exc)
            log(f"[ERROR] Deploy failed: {exc}")

    async def _async_deploy(
        self,
        deploy_id: str,
        agent_config_path: str | None,
        model_api_base: str,
        model_api_key: str,
        model_name: str,
        auto_clear_seconds: int,
        log,
        num_sandboxes: int = 1,
    ) -> None:
        import sys
        import tempfile
        import time

        import yaml
        from rock.sdk.sandbox.client import Sandbox
        from rock.sdk.sandbox.config import SandboxConfig

        # Clear cached ROCK URLs to re-probe the correct admin/proxy pair.
        global _resolved_rock_urls
        _resolved_rock_urls = None

        base_url, proxy_url = _get_rock_urls()

        with self._lock:
            job = self._deploy_jobs[deploy_id]

        # run_cmd: use ${bin_dir} placeholder so openclaw is on PATH in both image types.
        # IMPORTANT: OPENCLAW_CONFIG_PATH must prefix the gateway command, not mkdir,
        # so the env var is visible to the openclaw process.
        _run_cmd = (
            "export PATH=${bin_dir}:$PATH && "
            "mkdir -p /tmp/empty-bundled /tmp/oc_home && "
            "OPENCLAW_CONFIG_PATH=${working_dir}/openclaw.json "
            "OPENCLAW_HOME=/tmp/oc_home OPENCLAW_BUNDLED_PLUGINS_DIR=/tmp/empty-bundled "
            "nohup openclaw gateway >> /tmp/gateway.log 2>&1 &"
        )

        def _make_config(image: str) -> Path:
            """Build a temporary agent config YAML for *image*, validating model params."""
            if agent_config_path:
                p = Path(agent_config_path)
                if not p.is_absolute():
                    for candidate in [PROJECT_ROOT / p, DEPLOY_DIR / p]:
                        if candidate.exists():
                            p = candidate
                            break
                return p

            template_name = (
                "rock_agent_config.prebuilt.yaml"
                if is_prebuilt_image(image)
                else "rock_agent_config.yaml"
            )
            template_path = DEPLOY_DIR / template_name
            config_data = yaml.safe_load(template_path.read_text()) if template_path.exists() else {}

            config_data["working_dir"] = str(DEPLOY_DIR)
            config_data["run_cmd"] = _run_cmd
            config_data.setdefault("runtime_env_config", {})
            install_timeout = 120 if is_prebuilt_image(image) else 1200
            config_data["runtime_env_config"].update({
                "type": "node",
                "npm_registry": "https://registry.npmmirror.com",
                "custom_install_cmd": get_custom_install_cmd(image),
                "install_timeout": install_timeout,
            })

            env = config_data.setdefault("env", {})
            if model_api_base:
                env["OPENAI_BASE_URL"] = model_api_base
            if model_api_key:
                env["OPENAI_API_KEY"] = model_api_key
            if model_name:
                env["OPENAI_MODEL_NAME"] = model_name
            env.setdefault("OPENCLAW_GATEWAY_TOKEN", "OPENCLAW")

            import re as _re
            for _key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME"):
                _val = str(env.get(_key, ""))
                if not _val or _re.match(r"^\$\{.+\}$", _val):
                    raise RuntimeError(
                        f"Model config '{_key}' is not set. "
                        f"Provide it via the dashboard model settings or "
                        f"export {_key}=... in the server environment."
                    )

            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".yaml", delete=False, prefix="agent_config_"
            )
            yaml.safe_dump(config_data, tmp)
            tmp.close()
            return Path(tmp.name)

        # Validate model params early (fast-fail before any sandbox creation).
        _make_config(DEFAULT_SANDBOX_IMAGE)

        log(f"ROCK base_url: {base_url}")
        log(f"ROCK proxy_url: {proxy_url}")
        log("Running preflight checks...")

        # Check ROCK admin reachability
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                resp = await client.get(base_url)
                resp.raise_for_status()
                log(f"  ROCK admin OK ({base_url})")
            except Exception as exc:
                raise RuntimeError(
                    f"ROCK admin is not reachable at {base_url}: {exc}\n"
                    "Ensure ROCK admin is running and ROCK_BASE_URL / ROCK_ADMIN_PORT are correct."
                ) from exc

            proxy_root = proxy_url.removesuffix("/apis/envs/sandbox/v1")
            try:
                resp = await client.get(proxy_root)
                resp.raise_for_status()
                log(f"  ROCK proxy OK ({proxy_root})")
            except Exception as exc:
                raise RuntimeError(
                    f"ROCK proxy is not reachable at {proxy_root}: {exc}\n"
                    "Ensure ROCK proxy is running and ROCK_PROXY_URL / ROCK_PROXY_PORT are correct."
                ) from exc

        # Check Redis
        import socket as _socket
        redis_port = int(os.environ.get("ROCK_REDIS_PORT", "6379"))
        try:
            with _socket.create_connection(("127.0.0.1", redis_port), timeout=5):
                log(f"  Redis OK (port {redis_port})")
        except OSError:
            _conda_env = os.environ.get("CONDA_DEFAULT_ENV", "alphadiana")
            _rock_env = PROJECT_ROOT / "dev" / "rock_env.sh"
            _rock_ports = PROJECT_ROOT / "dev" / ".rock_ports.env"
            raise RuntimeError(
                f"Redis is not reachable on port {redis_port}.\n"
                f"Please ensure all ROCK services are running. In a terminal:\n"
                f"\n"
                f"  conda activate {_conda_env}\n"
                f"  source {_rock_env}\n"
                f"  source {_rock_ports}\n"
                f"  bash dev/quickstart.sh   # if services are not started yet"
            )

        # Ensure ROCK .venv symlink
        rock_root = PROJECT_ROOT / "ref" / "ROCK"
        venv_link = rock_root / ".venv"
        expected_target = Path(sys.prefix).resolve()
        if venv_link.exists() or venv_link.is_symlink():
            if venv_link.is_symlink() and venv_link.resolve() != expected_target:
                log(f"  Fixing .venv symlink: {venv_link.resolve()} -> {expected_target}")
                venv_link.unlink()
                venv_link.symlink_to(expected_target, target_is_directory=True)
        elif rock_root.exists():
            log(f"  Creating .venv symlink -> {expected_target}")
            venv_link.symlink_to(expected_target, target_is_directory=True)
        log("Preflight checks passed.")

        # ------------------------------------------------------------------
        # Sandbox creation: try prebuilt image first, fall back to configured image.
        # This is transparent to the user; progress is reflected only in logs.
        # ------------------------------------------------------------------
        import subprocess as _subprocess

        # SandboxConfig.base_url must be the admin URL: the SDK appends
        # /apis/envs/sandbox/v1 and calls start_async, which is served by the admin.
        # After startup, sandbox.url is overridden to the proxy URL for gateway access.
        proxy_root = proxy_url.removesuffix("/apis/envs/sandbox/v1")  # used for preflight only

        def _image_available_locally(image: str) -> bool:
            """Return True if *image* is already present in the local Docker cache."""
            try:
                r = _subprocess.run(
                    ["docker", "image", "inspect", image],
                    capture_output=True, timeout=5,
                )
                return r.returncode == 0
            except Exception:
                return False

        sandbox: Sandbox | None = None
        active_image = DEFAULT_SANDBOX_IMAGE

        if not is_prebuilt_image(DEFAULT_SANDBOX_IMAGE):
            if not _image_available_locally(PREBUILT_SANDBOX_IMAGE):
                log(f"Prebuilt image not in local Docker cache, skipping (run 'docker pull {PREBUILT_SANDBOX_IMAGE}' to enable).")
                configure_rock_runtime_for_image(DEFAULT_SANDBOX_IMAGE)
            else:
                configure_rock_runtime_for_image(PREBUILT_SANDBOX_IMAGE)
                log(f"Trying prebuilt image ({PREBUILT_SANDBOX_IMAGE}) for faster startup...")
                try:
                    _pb_config = SandboxConfig(
                        base_url=base_url,
                        image=PREBUILT_SANDBOX_IMAGE,
                        memory="4g",
                        cpus=1,
                        auto_clear_seconds=auto_clear_seconds,
                        startup_timeout=180,
                    )
                    _pb_sb = Sandbox(_pb_config)
                    await _pb_sb.start()

                    _pb_deadline = time.monotonic() + 180
                    while time.monotonic() < _pb_deadline:
                        try:
                            _pb_status = await _pb_sb.get_status()
                            if getattr(_pb_status, "is_alive", False):
                                _pb_sb.url = proxy_url
                                sandbox = _pb_sb
                                active_image = PREBUILT_SANDBOX_IMAGE
                                log(f"Prebuilt sandbox ready: {_pb_sb.sandbox_id}")
                                break
                            log(f"  [prebuilt] Waiting... (status={_pb_status.status})")
                        except Exception as _exc:
                            log(f"  [prebuilt] Status check: {_exc}")
                        await asyncio.sleep(3)

                    if sandbox is None:
                        log("Prebuilt sandbox did not become ready within 180s, falling back to standard image...")
                        configure_rock_runtime_for_image(DEFAULT_SANDBOX_IMAGE)
                except Exception as exc:
                    log(f"Prebuilt image failed ({exc}), falling back to standard image ({DEFAULT_SANDBOX_IMAGE})...")
                    configure_rock_runtime_for_image(DEFAULT_SANDBOX_IMAGE)
        else:
            # DEFAULT_SANDBOX_IMAGE is itself a pre-built image (e.g. reasoning image).
            # Require it to be present in the local Docker cache.
            if (
                DEFAULT_SANDBOX_IMAGE != PREBUILT_SANDBOX_IMAGE
                and not _image_available_locally(DEFAULT_SANDBOX_IMAGE)
            ):
                raise RuntimeError(
                    f"Required image '{DEFAULT_SANDBOX_IMAGE}' is not in the local Docker cache.\n"
                    f"Pull it first:  docker pull {DEFAULT_SANDBOX_IMAGE}"
                )
            configure_rock_runtime_for_image(active_image)

        # ------------------------------------------------------------------
        # Per-sandbox deploy helper: create, install, start gateway, wait ready.
        # Run once for a single sandbox, or N times in parallel for multi-sandbox.
        # ------------------------------------------------------------------

        # If num_sandboxes > 1 we always create fresh containers; the one sandbox
        # already started via the prebuilt-image probe path is used as sandbox #0.
        prebuilt_sandbox = sandbox  # may be None if prebuilt path was skipped

        async def _deploy_one_sandbox(sb_idx: int) -> str:
            """Deploy a single sandbox and return its api_base URL."""
            prefix = f"[sandbox-{sb_idx + 1}/{num_sandboxes}] " if num_sandboxes > 1 else ""

            if sb_idx == 0 and prebuilt_sandbox is not None:
                # Reuse the sandbox already created via the prebuilt image path.
                sb = prebuilt_sandbox
            else:
                log(f"{prefix}Creating sandbox ({active_image})...")
                _cfg = SandboxConfig(
                    base_url=base_url,
                    image=active_image,
                    memory="4g",
                    cpus=1,
                    auto_clear_seconds=auto_clear_seconds,
                    startup_timeout=180,
                )
                sb = Sandbox(_cfg)
                try:
                    await sb.start()
                except Exception as exc:
                    raise RuntimeError(f"{prefix}Failed to create sandbox: {exc}") from exc

                log(f"{prefix}Waiting for sandbox to be ready...")
                _deadline = time.monotonic() + 180
                while time.monotonic() < _deadline:
                    try:
                        _st = await sb.get_status()
                        if getattr(_st, "is_alive", False):
                            sb.url = proxy_url
                            log(f"{prefix}Sandbox is running (status={_st.status})")
                            break
                        log(f"{prefix}  Waiting... (status={_st.status})")
                    except Exception as exc:
                        log(f"{prefix}  Status check: {exc}")
                    await asyncio.sleep(3)
                else:
                    raise RuntimeError(f"{prefix}Sandbox did not reach running state within 180s")

            sb_id = sb.sandbox_id

            # Build agent config for the image that was actually used
            _config_file = _make_config(active_image)
            log(f"{prefix}Using agent config: {_config_file} (image: {active_image})")

            # Install agent
            log(f"{prefix}Installing OpenClaw agent...")
            _install_max_attempts = 5
            _install_delay = 10
            for _attempt in range(1, _install_max_attempts + 1):
                try:
                    await sb.agent.install(config=str(_config_file))
                    log(f"{prefix}Agent installed successfully")
                    break
                except Exception as exc:
                    if _attempt == _install_max_attempts:
                        raise
                    log(f"{prefix}  Sandbox session not ready yet, waiting {_install_delay}s... (attempt {_attempt}/{_install_max_attempts})")
                    await asyncio.sleep(_install_delay)

            # Start gateway
            log(f"{prefix}Starting OpenClaw gateway...")
            await sb.agent.run("")
            log(f"{prefix}OpenClaw gateway command sent")

            _api_base = f"{proxy_url}/sandboxes/{sb_id}/proxy/v1"

            # Wait for gateway to be ready
            await asyncio.sleep(10)
            gw_timeout = 900
            log(f"{prefix}Waiting for OpenClaw gateway to be ready (timeout: {gw_timeout}s)...")
            gw_deadline = time.monotonic() + gw_timeout
            gateway_ready = False
            last_log_time = 0.0
            async with httpx.AsyncClient(timeout=10.0) as _client:
                while time.monotonic() < gw_deadline:
                    try:
                        resp = await _client.post(
                            f"{_api_base}/chat/completions",
                            headers={
                                "Authorization": "bearer OPENCLAW",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": "openclaw",
                                "messages": [{"role": "user", "content": "ping"}],
                                "max_tokens": 5,
                            },
                            timeout=30.0,
                        )
                        body = resp.json() if resp.status_code == 200 else {}
                        if isinstance(body, dict) and body.get("status") == "Failed":
                            err = body.get("error", "")
                            now = time.monotonic()
                            if now - last_log_time > 15:
                                log(f"{prefix}  Gateway not ready: {err}")
                                last_log_time = now
                        elif resp.status_code == 200 and body.get("choices"):
                            log(f"{prefix}OpenClaw gateway is ready!")
                            gateway_ready = True
                            break
                        else:
                            now = time.monotonic()
                            if now - last_log_time > 15:
                                log(f"{prefix}  Gateway probe: status={resp.status_code}")
                                last_log_time = now
                    except Exception as exc:
                        now = time.monotonic()
                        if now - last_log_time > 15:
                            log(f"{prefix}  Gateway probe: {exc}")
                            last_log_time = now
                    await asyncio.sleep(10)

            if not gateway_ready:
                raise RuntimeError(
                    f"{prefix}OpenClaw gateway did not become ready within {gw_timeout}s. "
                    "Check sandbox logs for npm install or gateway startup errors."
                )

            log(f"{prefix}Deploy complete! Sandbox ID: {sb_id}, API base: {_api_base}")
            return sb_id, _api_base

        # Deploy sandboxes (single or parallel)
        n = max(1, num_sandboxes)
        if n == 1:
            first_sb_id, api_base = await _deploy_one_sandbox(0)
            api_bases = [api_base]
        else:
            log(f"Deploying {n} sandboxes in parallel...")
            results = list(await asyncio.gather(*[_deploy_one_sandbox(i) for i in range(n)]))
            first_sb_id = results[0][0]
            api_bases = [r[1] for r in results]
            api_base = api_bases[0]
            log(f"All {n} sandboxes ready.")

        log(f"Auto-clear: {auto_clear_seconds}s ({auto_clear_seconds // 60} min)")

        with self._lock:
            job.status = "completed"
            job.sandbox_id = first_sb_id
            job.api_base = api_base
            if n > 1:
                job.gateway_pool = api_bases
