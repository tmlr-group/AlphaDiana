"""OpenClaw runtime manager for task-local SWE-bench containers."""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

import logging as _logging

_logger = _logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
NODE_RUNTIME_ROOT = "/tmp/openclaw-node"
NODE_RUNTIME_BIN = f"{NODE_RUNTIME_ROOT}/bin"
NODE_TARBALL = "node-v22.18.0-linux-x64.tar.xz"
NODE_DOWNLOAD_URL = f"https://npmmirror.com/mirrors/node/v22.18.0/{NODE_TARBALL}"
NODE_SHA256 = "c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7"
OPENCLAW_VERSION = "2026.3.7"
NPM_REGISTRY = "https://registry.npmjs.org"
LIBSIGNAL_GIT_URL = "https://github.com/whiskeysockets/libsignal-node.git"
LIBSIGNAL_MIRROR_DIR_NAME = "libsignal-node-bare"
LIBSIGNAL_MIRROR_CACHE_DIR = PROJECT_ROOT / ".cache" / "openclaw" / LIBSIGNAL_MIRROR_DIR_NAME
LIBSIGNAL_MIRROR_REMOTE_ARCHIVE = f"/tmp/{LIBSIGNAL_MIRROR_DIR_NAME}.tar"
LIBSIGNAL_MIRROR_REMOTE_DIR = f"/tmp/{LIBSIGNAL_MIRROR_DIR_NAME}"
OPENCLAW_OFFLINE_DIR_NAME = "openclaw-offline"
OPENCLAW_OFFLINE_REMOTE_DIR = f"/tmp/{OPENCLAW_OFFLINE_DIR_NAME}"
OPENCLAW_OFFLINE_REMOTE_ENTRYPOINT = f"{OPENCLAW_OFFLINE_REMOTE_DIR}/dist/index.js"
OPENCLAW_OFFLINE_CACHE_DIR = PROJECT_ROOT / ".cache" / "openclaw" / OPENCLAW_OFFLINE_DIR_NAME
OPENCLAW_OFFLINE_SOURCE_IMAGE_ENV = "OPENCLAW_OFFLINE_SOURCE_IMAGE"
OPENCLAW_OFFLINE_SOURCE_IMAGE_PREFIXES = (
    "localhost/alphadiana-swebench-runtime:openclaw-",
    "localhost/alphadiana-tb2-runtime:openclaw-",
    "localhost/alphadiana-openclaw-swebench-runtime-source:",
    "alphadiana-swebench-runtime:openclaw-",
    "alphadiana-tb2-runtime:openclaw-",
    "ghcr.io/tsrigo/openclaw-reasoning:",
)
OPENCLAW_OFFLINE_SOURCE_PATH_CANDIDATES = (
    "/opt/openclaw",
    "/app",
)
_SECRET_FIELD_NAMES = frozenset({
    "api_key",
    "apikey",
    "authorization",
    "token",
})
TESTBED_PYTHON_BIN_CANDIDATES = (
    "/opt/miniconda3/envs/testbed/bin",
    "/opt/conda/envs/testbed/bin",
    "/root/miniconda3/envs/testbed/bin",
)
OPENCLAW_EXEC_PATH_PREPEND = TESTBED_PYTHON_BIN_CANDIDATES[0]
TESTBED_PATH_SEGMENTS = (
    *TESTBED_PYTHON_BIN_CANDIDATES,
    "/opt/miniconda3/bin",
    "/opt/conda/bin",
    "/root/miniconda3/bin",
    "/usr/local/sbin",
    "/usr/local/bin",
    "/usr/sbin",
    "/usr/bin",
    "/sbin",
    "/bin",
)
_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"^\$\{.+\}$")
TESTBED_TOOL_PATH = ":".join(dict.fromkeys(TESTBED_PATH_SEGMENTS))

DEFAULT_INSTALL_OPENCLAW_COMMAND = "\n".join(
    [
        "set -e",
        "if ! command -v wget >/dev/null 2>&1 || ! command -v xz >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then",
        "  apt-get update",
        "  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends wget ca-certificates git xz-utils",
        "  rm -rf /var/lib/apt/lists/*",
        "fi",
        f"if [ ! -x {NODE_RUNTIME_BIN}/node ] || ! {NODE_RUNTIME_BIN}/node -e \"process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)\"; then",
        f"  rm -rf {NODE_RUNTIME_ROOT} /tmp/{NODE_TARBALL}",
        f"  wget -q -O /tmp/{NODE_TARBALL} {NODE_DOWNLOAD_URL}",
        f"  echo \"{NODE_SHA256}  /tmp/{NODE_TARBALL}\" | sha256sum -c -",
        "  mkdir -p /tmp",
        f"  tar -xf /tmp/{NODE_TARBALL} -C /tmp",
        f"  mv /tmp/node-v22.18.0-linux-x64 {NODE_RUNTIME_ROOT}",
        f"  rm -f /tmp/{NODE_TARBALL}",
        "fi",
        f"export PATH=\"{NODE_RUNTIME_BIN}:$PATH\"",
        "git config --global --unset-all url.'https://github.com/'.insteadOf || true",
        "git config --global --add url.'https://github.com/'.insteadOf 'ssh://git@github.com/'",
        "git config --global --add url.'https://github.com/'.insteadOf 'git@github.com:'",
        "git config --global --add url.'https://github.com/'.insteadOf 'git+ssh://git@github.com/'",
        "git config --global http.version HTTP/1.1",
        "git config --global http.lowSpeedLimit 0",
        "git config --global http.lowSpeedTime 999999",
        f"if [ -f {LIBSIGNAL_MIRROR_REMOTE_ARCHIVE} ]; then",
        f"  rm -rf {LIBSIGNAL_MIRROR_REMOTE_DIR}",
        "  mkdir -p /tmp",
        f"  tar -xf {LIBSIGNAL_MIRROR_REMOTE_ARCHIVE} -C /tmp",
        f"  git config --global --add safe.directory {LIBSIGNAL_MIRROR_REMOTE_DIR}",
        f"  git config --global --add url.'file://{LIBSIGNAL_MIRROR_REMOTE_DIR}'.insteadOf '{LIBSIGNAL_GIT_URL}'",
        f"  git config --global --add url.'file://{LIBSIGNAL_MIRROR_REMOTE_DIR}'.insteadOf 'git+{LIBSIGNAL_GIT_URL}'",
        "fi",
        f"npm config set registry {NPM_REGISTRY}",
        f"npm install -g openclaw@{OPENCLAW_VERSION} --omit=optional",
        "npm cache clean --force || true",
    ]
)

DEFAULT_PREPARE_OPENCLAW_PREREQS_COMMAND = "\n".join(
    [
        "set -e",
        "if ! command -v wget >/dev/null 2>&1 || ! command -v xz >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then",
        "  apt-get update",
        "  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends wget ca-certificates git xz-utils",
        "  rm -rf /var/lib/apt/lists/*",
        "fi",
        f"if [ ! -x {NODE_RUNTIME_BIN}/node ] || ! {NODE_RUNTIME_BIN}/node -e \"process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)\"; then",
        f"  rm -rf {NODE_RUNTIME_ROOT} /tmp/{NODE_TARBALL}",
        f"  wget -q -O /tmp/{NODE_TARBALL} {NODE_DOWNLOAD_URL}",
        f"  echo \"{NODE_SHA256}  /tmp/{NODE_TARBALL}\" | sha256sum -c -",
        "  mkdir -p /tmp",
        f"  tar -xf /tmp/{NODE_TARBALL} -C /tmp",
        f"  mv /tmp/node-v22.18.0-linux-x64 {NODE_RUNTIME_ROOT}",
        f"  rm -f /tmp/{NODE_TARBALL}",
        "fi",
        f"export PATH=\"{NODE_RUNTIME_BIN}:$PATH\"",
        "git config --global --unset-all url.'https://github.com/'.insteadOf || true",
        "git config --global --add url.'https://github.com/'.insteadOf 'ssh://git@github.com/'",
        "git config --global --add url.'https://github.com/'.insteadOf 'git@github.com:'",
        "git config --global --add url.'https://github.com/'.insteadOf 'git+ssh://git@github.com/'",
        "git config --global http.version HTTP/1.1",
        "git config --global http.lowSpeedLimit 0",
        "git config --global http.lowSpeedTime 999999",
        f"if [ -f {LIBSIGNAL_MIRROR_REMOTE_ARCHIVE} ]; then",
        f"  rm -rf {LIBSIGNAL_MIRROR_REMOTE_DIR}",
        "  mkdir -p /tmp",
        f"  tar -xf {LIBSIGNAL_MIRROR_REMOTE_ARCHIVE} -C /tmp",
        f"  git config --global --add safe.directory {LIBSIGNAL_MIRROR_REMOTE_DIR}",
        f"  git config --global --add url.'file://{LIBSIGNAL_MIRROR_REMOTE_DIR}'.insteadOf '{LIBSIGNAL_GIT_URL}'",
        f"  git config --global --add url.'file://{LIBSIGNAL_MIRROR_REMOTE_DIR}'.insteadOf 'git+{LIBSIGNAL_GIT_URL}'",
        "fi",
    ]
)


def _progress(message: str) -> None:
    print(f"[OpenClawContainer] {message}", flush=True)


def _is_ready_probe_status(status_code: int) -> bool:
    return status_code in (200, 404, 405)


def _make_directory_archive(source_dir: Path, arcname: str) -> bytes:
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as tar:
        tar.add(source_dir, arcname=arcname)
    return payload.getvalue()


def _ensure_bare_git_mirror(source_url: str, cache_dir: Path) -> Path | None:
    if (cache_dir / "HEAD").exists():
        return cache_dir

    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = cache_dir.with_name(f"{cache_dir.name}.tmp")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    try:
        subprocess.run(
            ["git", "clone", "--bare", "--depth", "1", source_url, str(tmp_dir)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        tmp_dir.replace(cache_dir)
        return cache_dir
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if (cache_dir / "HEAD").exists():
            return cache_dir
        _logger.warning("Failed to prepare git mirror for %s: %s", source_url, exc)
        return None


def _resolve_offline_source_image() -> str | None:
    explicit = os.environ.get(OPENCLAW_OFFLINE_SOURCE_IMAGE_ENV, "").strip()
    if explicit:
        return explicit
    for engine in ("podman", "docker"):
        try:
            result = subprocess.run(
                [engine, "images", "--format", "{{.Repository}}:{{.Tag}}"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as exc:
            _logger.debug("Failed to list local OpenClaw source images with %s: %s", engine, exc)
            continue
        for line in result.stdout.splitlines():
            image = line.strip()
            if image and any(image.startswith(prefix) for prefix in OPENCLAW_OFFLINE_SOURCE_IMAGE_PREFIXES):
                return image
    return None


def _offline_source_engine(source_image: str) -> str:
    explicit = os.environ.get("OPENCLAW_OFFLINE_SOURCE_ENGINE", "").strip().lower()
    if explicit in {"docker", "podman"}:
        return explicit
    if source_image.startswith("localhost/"):
        return "podman"
    try:
        subprocess.run(
            ["podman", "image", "exists", source_image],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return "podman"
    except Exception as exc:
        _logger.debug("OpenClaw offline source image not found via podman: %s", exc)
    return "docker"


def _ensure_offline_openclaw_payload(cache_dir: Path) -> Path | None:
    if (cache_dir / "dist" / "index.js").exists() and (cache_dir / "node_modules").is_dir():
        return cache_dir

    source_image = _resolve_offline_source_image()
    if not source_image:
        return None

    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = cache_dir.with_name(f"{cache_dir.name}.tmp-{os.getpid()}-{time.time_ns()}")
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    container_id = ""
    engine = _offline_source_engine(source_image)
    try:
        container_id = subprocess.run(
            [engine, "create", source_image],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        copied = False
        for source_path in OPENCLAW_OFFLINE_SOURCE_PATH_CANDIDATES:
            result = subprocess.run(
                [engine, "cp", f"{container_id}:{source_path}/.", str(tmp_dir)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode == 0:
                copied = True
                break
        if not copied:
            _logger.warning(
                "Failed to extract offline OpenClaw payload from %s",
                source_image,
            )
            return None
        if not (tmp_dir / "dist" / "index.js").exists() or not (tmp_dir / "node_modules").is_dir():
            _logger.warning(
                "Extracted offline OpenClaw payload from %s but required runtime files are missing",
                source_image,
            )
            return None
        if cache_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return cache_dir
        tmp_dir.replace(cache_dir)
        return cache_dir
    except Exception as exc:
        _logger.warning("Failed to prepare offline OpenClaw payload from %s: %s", source_image, exc)
        return None
    finally:
        if container_id:
            subprocess.run(
                [engine, "rm", "-f", container_id],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SECRET_FIELD_NAMES or normalized.endswith("_api_key"):
                redacted[key] = "REDACTED"
            else:
                redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _is_unresolved_placeholder(value: str) -> bool:
    return bool(_UNRESOLVED_PLACEHOLDER_RE.match(value.strip()))


def _resolve_gateway_token(value: Any) -> str:
    token = str(value or "").strip()
    if not token or _is_unresolved_placeholder(token):
        return "OPENCLAW"
    return token


def _sanitize_json_text(raw_text: str) -> str:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw_text
    return json.dumps(_redact_secret_fields(payload), indent=2, ensure_ascii=False)


class OpenClawContainerRuntimeManager:
    """Bootstraps the OpenClaw gateway inside a SWE-bench task container."""

    def __init__(self, config: dict) -> None:
        self._config = dict(config)
        self._gateway_token = _resolve_gateway_token(config.get("gateway_token", "OPENCLAW"))
        if self._gateway_token and not re.fullmatch(r"[A-Za-z0-9_\-]+", self._gateway_token):
            raise ValueError("Invalid gateway token charset; allowed [A-Za-z0-9_-]")
        self._gateway_model = config.get("model", "openclaw")
        self._openclaw_config_path = str(self._resolve_config_path(config.get("openclaw_config_path", ""))) if config.get("openclaw_config_path") else ""
        self._gateway_startup_timeout = int(config.get("gateway_startup_timeout", 180))
        self._gateway_warmup_timeout = int(config.get("gateway_warmup_timeout", 180))
        self._gateway_warmup_initial_delay = float(config.get("gateway_warmup_initial_delay", 3.0))
        self._gateway_log_path = config.get("gateway_log_path", "/tmp/openclaw-gateway.log")
        self._gateway_pidfile = config.get("gateway_pidfile", "/tmp/openclaw-gateway.pid")
        self._workspace_path = config.get("workspace_path", "/tmp/oc_home/.openclaw/workspace")
        self._openclaw_home = config.get("openclaw_home", "/tmp/oc_home")
        self._remote_openclaw_config_path = config.get("remote_openclaw_config_path", "/tmp/openclaw.json")
        self._install_openclaw_command = config.get(
            "install_openclaw_command",
            DEFAULT_INSTALL_OPENCLAW_COMMAND,
        )
        self._gateway_port = int(config.get("container_gateway_port", config.get("gateway_port", 8080)))
        self._gateway_host = config.get("container_gateway_host", "127.0.0.1")
        self._gateway_bind_host = str(
            config.get("container_gateway_bind_host", config.get("gateway_bind_host", ""))
        ).strip()
        self._started_sandboxes: set[str] = set()
        self._managed_sandboxes: dict[str, Any] = {}
        self._agent_md_applied_sandboxes: set[str] = set()
        self._git_mirror_uploaded_sandboxes: set[str] = set()
        self._offline_openclaw_uploaded_sandboxes: set[str] = set()

        self._agent_md_mode = config.get("agent_md_mode", "none")
        self._agent_md_content = config.get("agent_md_content", "")
        self._workspace = config.get("workspace", "")

        self._embedding_api_base = config.get("embedding_api_base", "")
        self._embedding_api_key = config.get("embedding_api_key", "")
        self._max_tokens = config.get("max_tokens", None)

    def _resolve_config_path(self, path_str: str) -> Path:
        path = Path(path_str).expanduser()
        if path.is_absolute():
            return path.resolve()
        for candidate in ((PROJECT_ROOT / path).resolve(), path.resolve()):
            if candidate.exists():
                return candidate
        return (PROJECT_ROOT / path).resolve()

    @property
    def is_configured(self) -> bool:
        return bool(self._openclaw_config_path)

    def set_provider_api_base(self, api_base: str) -> None:
        value = str(api_base or "").strip()
        self._config["OPENAI_BASE_URL"] = value
        self._config["openai_base_url"] = value

    def set_provider_api_key(self, api_key: str) -> None:
        value = str(api_key or "").strip()
        self._config["OPENAI_API_KEY"] = value
        self._config["openai_api_key"] = value

    def _resolve_env_value(self, env_key: str, config_key: str) -> str:
        value = str(self._config.get(config_key, "")).strip()
        if not value:
            # Backward compatibility: some configs/tests pass upstream values
            # using uppercase env-style keys directly in agent.config.
            value = str(self._config.get(env_key, "")).strip()
        if value:
            return value
        return os.environ.get(env_key, "").strip()

    def _sandbox_metadata(self, sandbox: Any) -> dict[str, Any]:
        if hasattr(sandbox, "metadata"):
            try:
                metadata = sandbox.metadata()
                if isinstance(metadata, dict):
                    return metadata
            except Exception:
                _logger.debug("Failed to read sandbox metadata", exc_info=True)
        return {}

    def _stage_offline_openclaw_payload(self, sandbox: Any) -> bool:
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if sandbox_id and sandbox_id in self._offline_openclaw_uploaded_sandboxes:
            return True
        metadata = self._sandbox_metadata(sandbox)
        container_name = str(metadata.get("container_name", "")).strip()
        if not container_name:
            return False
        container_engine = str(metadata.get("container_engine", "docker") or "docker").strip().lower()
        if container_engine not in {"docker", "podman"}:
            container_engine = "docker"
        payload_dir = _ensure_offline_openclaw_payload(OPENCLAW_OFFLINE_CACHE_DIR)
        if payload_dir is None:
            return False
        sandbox.execute(f"rm -rf {shlex.quote(OPENCLAW_OFFLINE_REMOTE_DIR)} && mkdir -p {shlex.quote(OPENCLAW_OFFLINE_REMOTE_DIR)}")
        subprocess.run(
            [container_engine, "cp", f"{payload_dir}/.", f"{container_name}:{OPENCLAW_OFFLINE_REMOTE_DIR}"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if sandbox_id:
            self._offline_openclaw_uploaded_sandboxes.add(sandbox_id)
        return True

    def _resolve_workspace_root(self, sandbox: Any | None = None) -> str:
        if self._workspace:
            return self._workspace
        if sandbox is not None:
            repo_workdir = str(self._sandbox_metadata(sandbox).get("repo_workdir", "")).strip()
            if repo_workdir:
                return repo_workdir
        return self._workspace_path

    def _build_openclaw_config(self, base_config: dict | None = None, *, workspace_root: str | None = None) -> dict:
        config: dict[str, Any] = deepcopy(base_config or {})
        env_cfg = config.setdefault("env", {})
        upstream = {
            "OPENAI_BASE_URL": self._resolve_env_value("OPENAI_BASE_URL", "openai_base_url"),
            "OPENAI_API_KEY": self._resolve_env_value("OPENAI_API_KEY", "openai_api_key"),
            "OPENAI_MODEL_NAME": self._resolve_env_value("OPENAI_MODEL_NAME", "openai_model_name"),
            "OPENCLAW_GATEWAY_TOKEN": self._gateway_token,
        }
        missing = [key for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME") if not upstream[key]]
        if missing:
            raise RuntimeError(
                "OpenClaw container runtime requires the upstream model settings to be available "
                f"via environment or agent config: {', '.join(missing)}"
            )
        env_cfg.update(upstream)
        configured_path = str(env_cfg.get("PATH", "")).strip()
        env_cfg["PATH"] = (
            f"{TESTBED_TOOL_PATH}:{configured_path}"
            if configured_path
            else TESTBED_TOOL_PATH
        )

        if self._embedding_api_base:
            models = config.setdefault("models", {})
            providers = models.setdefault("providers", {})
            providers["embedding"] = {
                "baseUrl": self._embedding_api_base,
                "apiKey": self._embedding_api_key or "EMPTY",
                "api": "openai",
            }

        agents = config.setdefault("agents", {})
        defaults = agents.setdefault("defaults", {})
        defaults["workspace"] = workspace_root or self._workspace_path
        if self._max_tokens is not None:
            try:
                max_tokens = int(self._max_tokens)
            except (TypeError, ValueError):
                max_tokens = 0
            if max_tokens > 0:
                model_defaults = defaults.setdefault("models", {})
                primary_model = ""
                model_cfg = defaults.get("model")
                if isinstance(model_cfg, dict):
                    primary_model = str(model_cfg.get("primary", "")).strip()
                if not primary_model:
                    model_name = str(env_cfg.get("OPENAI_MODEL_NAME", "")).strip()
                    if model_name:
                        primary_model = f"local/{model_name}"
                if primary_model:
                    target_cfg = model_defaults.setdefault(primary_model, {})
                    if not isinstance(target_cfg, dict):
                        target_cfg = {}
                        model_defaults[primary_model] = target_cfg
                    params = target_cfg.setdefault("params", {})
                    if not isinstance(params, dict):
                        params = {}
                        target_cfg["params"] = params
                    params["maxTokens"] = max_tokens

        gateway = config.setdefault("gateway", {})
        gateway["port"] = self._gateway_port
        gateway["mode"] = "local"
        # Published host ports cannot reach a gateway bound to container-local
        # loopback. Default to a non-loopback bind for task containers and only
        # fall back to custom binds when explicitly requested.
        if self._gateway_bind_host:
            gateway["bind"] = "custom"
            gateway["customBindHost"] = self._gateway_bind_host
        else:
            gateway["bind"] = "lan"
            gateway.pop("customBindHost", None)
        auth = gateway.setdefault("auth", {})
        auth["mode"] = "token"
        auth["token"] = "${OPENCLAW_GATEWAY_TOKEN}"

        tools = config.setdefault("tools", {})
        exec_cfg = tools.setdefault("exec", {})
        exec_cfg["ask"] = "off"
        exec_cfg["host"] = "gateway"
        exec_cfg["pathPrepend"] = [OPENCLAW_EXEC_PATH_PREPEND]
        exec_cfg["security"] = "full"

        memory = config.get("memory", {})
        if isinstance(memory, dict):
            memory.pop("enabled", None)
            if not memory:
                config.pop("memory", None)

        return config

    def inject_agent_md(self, sandbox: Any) -> None:
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if sandbox_id and sandbox_id in self._agent_md_applied_sandboxes:
            return
        if self._agent_md_mode == "none":
            return

        agent_md_path = f"{self._resolve_workspace_root(sandbox)}/AGENTS.md"
        existing = ""
        try:
            existing = sandbox.read_text(agent_md_path)
        except Exception:
            existing = ""

        if self._agent_md_mode == "append":
            new_content = existing + self._agent_md_content
        else:
            new_content = self._agent_md_content
        sandbox.upload(agent_md_path, new_content.encode("utf-8"))

        if sandbox_id:
            self._agent_md_applied_sandboxes.add(sandbox_id)

    def runtime_info(self, sandbox: Any) -> dict:
        if hasattr(sandbox, "gateway_api_base"):
            api_base = sandbox.gateway_api_base()
        else:
            metadata = self._sandbox_metadata(sandbox)
            host = metadata.get("gateway_host", self._gateway_host)
            port = metadata.get("gateway_host_port")
            if not port:
                raise RuntimeError("Sandbox metadata does not include a published gateway port")
            api_base = f"http://{host}:{port}/v1"
        return {
            "sandbox_id": str(getattr(sandbox, "sandbox_id", "")),
            "gateway_url": f"{api_base}/chat/completions",
            "api_base": api_base,
            "gateway_token": self._gateway_token,
        }

    def _probe_gateway_alive(self, sandbox: Any) -> bool:
        try:
            import httpx

            info = self.runtime_info(sandbox)
            response = httpx.get(
                f"{info['api_base']}/models",
                headers={"Authorization": f"bearer {self._gateway_token}"},
                timeout=5,
                trust_env=False,
            )
            return _is_ready_probe_status(response.status_code)
        except Exception:
            return False

    def _with_node_runtime_path(self, command: str) -> str:
        return "\n".join(
            [
                "set -e",
                f"if [ -d {NODE_RUNTIME_BIN} ]; then export PATH=\"{NODE_RUNTIME_BIN}:$PATH\"; fi",
                command,
            ]
        )

    def _build_git_dependency_mirror_archive(self) -> tuple[str, bytes] | None:
        mirror_dir = _ensure_bare_git_mirror(LIBSIGNAL_GIT_URL, LIBSIGNAL_MIRROR_CACHE_DIR)
        if mirror_dir is None:
            return None
        return (
            LIBSIGNAL_MIRROR_REMOTE_ARCHIVE,
            _make_directory_archive(mirror_dir, arcname=LIBSIGNAL_MIRROR_DIR_NAME),
        )

    def _upload_git_dependency_mirrors(self, sandbox: Any) -> None:
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if sandbox_id and sandbox_id in self._git_mirror_uploaded_sandboxes:
            return
        archive = self._build_git_dependency_mirror_archive()
        if archive is None:
            return
        remote_path, payload = archive
        sandbox.upload(remote_path, payload)
        if sandbox_id:
            self._git_mirror_uploaded_sandboxes.add(sandbox_id)

    def _install_openclaw_if_needed(self, sandbox: Any) -> None:
        check = sandbox.execute(
            self._with_node_runtime_path("command -v openclaw >/dev/null 2>&1 && openclaw --version")
        )
        if check.exit_code == 0:
            return
        sandbox.execute(self._with_node_runtime_path(DEFAULT_PREPARE_OPENCLAW_PREREQS_COMMAND))
        if self._stage_offline_openclaw_payload(sandbox):
            offline_check = sandbox.execute(
                self._with_node_runtime_path(
                    f"test -f {shlex.quote(OPENCLAW_OFFLINE_REMOTE_ENTRYPOINT)} && "
                    f"{shlex.quote(NODE_RUNTIME_BIN + '/node')} {shlex.quote(OPENCLAW_OFFLINE_REMOTE_ENTRYPOINT)} --version"
                )
            )
            if offline_check.exit_code == 0:
                return
            _logger.warning("Offline OpenClaw payload verification failed inside task container")
        if not self._install_openclaw_command:
            raise RuntimeError("OpenClaw is not installed in the container and no install command was configured")
        _progress("installing OpenClaw inside task container")
        result = sandbox.execute(self._with_node_runtime_path(self._install_openclaw_command))
        if result.exit_code != 0:
            raise RuntimeError(
                "Failed to install OpenClaw inside the task container:\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )

    def _prepare_runtime_files(self, sandbox: Any) -> None:
        openclaw_config = Path(self._openclaw_config_path)
        if not openclaw_config.exists():
            raise FileNotFoundError(f"openclaw_config_path not found: {openclaw_config}")
        workspace_root = self._resolve_workspace_root(sandbox)
        rendered = self._build_openclaw_config(
            json.loads(openclaw_config.read_text(encoding="utf-8")),
            workspace_root=workspace_root,
        )
        sandbox.upload(
            self._remote_openclaw_config_path,
            json.dumps(rendered, indent=2).encode("utf-8"),
        )

    def _build_start_gateway_command(self, sandbox: Any | None = None) -> str:
        workspace_root = self._resolve_workspace_root(sandbox)
        return "\n".join(
            [
                "set -e",
                "mkdir -p /tmp/empty-bundled",
                f"mkdir -p {shlex.quote(self._openclaw_home)}",
                f"mkdir -p {shlex.quote(workspace_root)}",
                f"cd {shlex.quote(workspace_root)}",
                "pkill -x openclaw >/dev/null 2>&1 || true",
                f"export PATH=\"{TESTBED_TOOL_PATH}:$PATH\"",
                f"if [ -d {NODE_RUNTIME_BIN} ]; then export PATH=\"{NODE_RUNTIME_BIN}:$PATH\"; fi",
                f"export OPENCLAW_CONFIG_PATH={shlex.quote(self._remote_openclaw_config_path)}",
                f"export OPENCLAW_HOME={shlex.quote(self._openclaw_home)}",
                "export OPENCLAW_BUNDLED_PLUGINS_DIR=/tmp/empty-bundled",
                f"export OPENCLAW_GATEWAY_TOKEN={shlex.quote(self._gateway_token)}",
                f"if [ -f {shlex.quote(OPENCLAW_OFFLINE_REMOTE_ENTRYPOINT)} ]; then",
                f"  nohup {shlex.quote(NODE_RUNTIME_BIN + '/node')} {shlex.quote(OPENCLAW_OFFLINE_REMOTE_ENTRYPOINT)} gateway > {shlex.quote(self._gateway_log_path)} 2>&1 &",
                "else",
                f"  nohup openclaw gateway > {shlex.quote(self._gateway_log_path)} 2>&1 &",
                "fi",
                f"echo $! > {shlex.quote(self._gateway_pidfile)}",
            ]
        )

    def _start_gateway(self, sandbox: Any) -> None:
        command = self._build_start_gateway_command(sandbox)
        result = sandbox.execute(command)
        if result.exit_code != 0:
            raise RuntimeError(
                "Failed to start OpenClaw gateway inside the task container:\n"
                f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
            )

    def _list_workspace_paths(self, sandbox: Any, workspace_root: str) -> list[str]:
        metadata = self._sandbox_metadata(sandbox)
        repo_workdir = str(metadata.get("repo_workdir", "")).strip()
        if repo_workdir and workspace_root == repo_workdir:
            candidates = [
                f"{workspace_root}/AGENTS.md",
                f"{workspace_root}/SOUL.md",
                f"{workspace_root}/BOOTSTRAP.md",
                f"{workspace_root}/HEARTBEAT.md",
                f"{workspace_root}/IDENTITY.md",
                f"{workspace_root}/TOOLS.md",
                f"{workspace_root}/USER.md",
                f"{workspace_root}/.openclaw/workspace-state.json",
            ]
            existing: list[str] = []
            for path in candidates:
                try:
                    sandbox.read_text(path)
                except Exception:
                    continue
                existing.append(path)
            return existing

        workspace_listing = sandbox.execute(
            f"find {shlex.quote(workspace_root)} -maxdepth 4 -type f | sort"
        )
        return [
            line.strip()
            for line in workspace_listing.stdout.splitlines()
            if line.strip()
        ]

    def _wait_for_gateway(self, sandbox: Any) -> None:
        import httpx

        info = self.runtime_info(sandbox)
        url = f"{info['api_base']}/models"
        headers = {"Authorization": f"bearer {self._gateway_token}"}
        deadline = time.monotonic() + self._gateway_startup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(url, headers=headers, timeout=10, trust_env=False)
                if _is_ready_probe_status(response.status_code):
                    return
            except Exception as exc:
                last_error = exc
            time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"OpenClaw gateway did not become ready: {last_error}") from last_error
        raise RuntimeError("OpenClaw gateway did not become ready before timeout")

    def _warmup_gateway(self, sandbox: Any) -> None:
        import httpx

        info = self.runtime_info(sandbox)
        url = f"{info['api_base']}/chat/completions"
        headers = {
            "Authorization": f"bearer {self._gateway_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._gateway_model,
            "messages": [
                {"role": "system", "content": "Reply briefly."},
                {"role": "user", "content": "Say READY."},
            ],
            "temperature": 0.0,
            "max_tokens": 32,
            "stream": False,
        }
        if self._gateway_warmup_initial_delay > 0:
            time.sleep(self._gateway_warmup_initial_delay)
        deadline = time.monotonic() + self._gateway_warmup_timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=30, trust_env=False)
                response.raise_for_status()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        if last_error is not None:
            raise RuntimeError(f"OpenClaw warmup failed: {last_error}") from last_error

    def ensure_ready(self, sandbox: Any) -> dict:
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))
        if sandbox_id in self._started_sandboxes and self._probe_gateway_alive(sandbox):
            return self.runtime_info(sandbox)

        _progress(f"preparing OpenClaw runtime for sandbox_id={sandbox_id}")
        self._prepare_runtime_files(sandbox)
        self.inject_agent_md(sandbox)
        self._upload_git_dependency_mirrors(sandbox)
        self._install_openclaw_if_needed(sandbox)
        self._start_gateway(sandbox)
        probe_error: Exception | None = None
        try:
            self._wait_for_gateway(sandbox)
        except Exception as exc:
            probe_error = exc
            _logger.warning(
                "OpenClaw container readiness probe failed; falling back to warmup: %s",
                exc,
            )
        try:
            self._warmup_gateway(sandbox)
        except Exception as exc:
            if probe_error is not None:
                raise RuntimeError(
                    "OpenClaw gateway did not become ready: "
                    f"probe_error={probe_error}; warmup_error={exc}"
                ) from exc
            _logger.warning("OpenClaw container warmup did not fully succeed: %s", exc)
        else:
            if probe_error is not None:
                _logger.warning(
                    "OpenClaw container readiness probe failed but warmup succeeded: %s",
                    probe_error,
                )
        self._started_sandboxes.add(sandbox_id)
        if sandbox_id:
            self._managed_sandboxes[sandbox_id] = sandbox
        return self.runtime_info(sandbox)

    def collect_artifacts(self, sandbox: Any) -> dict:
        gateway_log = ""
        try:
            gateway_log = sandbox.read_text(self._gateway_log_path)
        except Exception:
            gateway_log = ""

        workspace_root = self._resolve_workspace_root(sandbox)
        workspace_paths = self._list_workspace_paths(sandbox, workspace_root)

        session_listing = sandbox.execute(
            "ls -t /tmp/oc_home/.openclaw/agents/main/sessions/*.jsonl "
            "/root/.openclaw/agents/main/sessions/*.jsonl 2>/dev/null | head -10"
        )
        session_paths = [
            line.strip()
            for line in session_listing.stdout.splitlines()
            if line.strip().endswith(".jsonl")
        ]

        workspace_file_contents: dict[str, str] = {}
        manifest_files: dict[str, str] = {
            "gateway_log_source": self._gateway_log_path,
            "workspace_root": workspace_root,
        }
        state_files: list[str] = []
        session_alias_path = ""
        response_stream_alias = ""
        collected_snapshot_paths = list(workspace_paths)
        if workspace_paths:
            workspace_file_contents["openclaw_workspace_listing.txt"] = (
                "\n".join(dict.fromkeys(workspace_paths)) + "\n"
            )
            manifest_files["workspace_listing"] = "openclaw_workspace_listing.txt"
        if gateway_log.strip():
            workspace_file_contents["openclaw_gateway.log"] = gateway_log[-20000:]
            manifest_files["gateway_log"] = "openclaw_gateway.log"
        candidate_paths = [
            f"{workspace_root}/AGENTS.md",
            f"{workspace_root}/SOUL.md",
            f"{workspace_root}/openclaw_output.jsonl",
            f"{workspace_root}/.openclaw/workspace-state.json",
            "/tmp/oc_home/.openclaw/workspace-state.json",
            "/root/.openclaw/workspace-state.json",
            "/tmp/oc_home/.openclaw/agents/main/sessions.json",
            "/root/.openclaw/agents/main/sessions.json",
            self._remote_openclaw_config_path,
            *session_paths[:3],
        ]
        for path in candidate_paths:
            text = self._read_text_best_effort(sandbox, path)
            if not text.strip():
                continue
            workspace_file_contents[path] = text
            if path.endswith("workspace-state.json"):
                workspace_file_contents.setdefault("openclaw_workspace_state.json", text)
                manifest_files["workspace_state"] = "openclaw_workspace_state.json"
                state_files.append("openclaw_workspace_state.json")
            if path.endswith("sessions.json"):
                workspace_file_contents.setdefault("openclaw_sessions_index.json", text)
                manifest_files["session_index"] = "openclaw_sessions_index.json"
                state_files.append("openclaw_sessions_index.json")
            if path == self._remote_openclaw_config_path:
                sanitized_config = _sanitize_json_text(text)
                workspace_file_contents[path] = sanitized_config
                workspace_file_contents.setdefault("openclaw_runtime_config.json", sanitized_config)
                manifest_files["runtime_config"] = "openclaw_runtime_config.json"
                state_files.append("openclaw_runtime_config.json")
            if path.endswith("openclaw_output.jsonl") and not response_stream_alias:
                response_stream_alias = "openclaw_output.jsonl"
                workspace_file_contents.setdefault(response_stream_alias, text)
            if path.endswith(".jsonl") and "/sessions/" in path and not session_alias_path:
                session_alias_path = "openclaw_session.jsonl"
                workspace_file_contents.setdefault(session_alias_path, text)
                state_files.append(session_alias_path)
            if path not in collected_snapshot_paths:
                collected_snapshot_paths.append(path)
        if not response_stream_alias and session_alias_path:
            response_stream_alias = session_alias_path

        artifact_manifest = {
            "files": {
                **manifest_files,
                "response_stream": response_stream_alias,
                "session_trace": session_alias_path or (session_paths[0] if session_paths else ""),
                "openclaw_output": response_stream_alias,
                "openclaw_session": session_alias_path or (session_paths[0] if session_paths else ""),
                "system_prompt": f"{workspace_root}/AGENTS.md",
            },
            "session_paths": session_paths,
        }
        if state_files:
            artifact_manifest["state_files"] = list(dict.fromkeys(state_files))
        return {
            "artifact_manifest": artifact_manifest,
            "gateway_log_excerpt": gateway_log[-20000:] if gateway_log else "",
            "workspace_snapshot_paths": collected_snapshot_paths,
            "workspace_file_contents": workspace_file_contents,
            "sandbox_metadata": self._sandbox_metadata(sandbox),
        }

    def _read_text_best_effort(self, sandbox: Any, remote_path: str) -> str:
        path = str(remote_path or "").strip()
        if not path:
            return ""
        try:
            text = sandbox.read_text(path)
            if isinstance(text, str):
                return text
        except Exception:
            pass
        try:
            result = sandbox.execute(f"cat {shlex.quote(path)}")
        except Exception:
            return ""
        exit_code = getattr(result, "exit_code", getattr(result, "returncode", 1))
        if int(exit_code or 0) != 0:
            return ""
        stdout = getattr(result, "stdout", "")
        return stdout if isinstance(stdout, str) else ""

    def teardown(self) -> None:
        stop_command = (
            f"if [ -f {shlex.quote(self._gateway_pidfile)} ]; then "
            f"kill $(cat {shlex.quote(self._gateway_pidfile)}) >/dev/null 2>&1 || true; "
            f"rm -f {shlex.quote(self._gateway_pidfile)}; "
            "else pkill -x openclaw >/dev/null 2>&1 || true; fi"
        )
        for sandbox in list(self._managed_sandboxes.values()):
            try:
                sandbox.execute(stop_command)
            except Exception:
                _logger.debug("Failed to stop OpenClaw gateway during teardown", exc_info=True)
        self._managed_sandboxes.clear()
        self._started_sandboxes.clear()
