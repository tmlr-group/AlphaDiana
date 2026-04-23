"""OpenCode runtime manager for task-local SWE-bench containers.

OpenCode is a CLI-based coding agent that edits files directly using tool
calls. This manager bootstraps it inside a SWE-bench instance container so
it can access the repository at the correct commit.

Workflow per task:
  1. Install Node.js + opencode inside the container (once per sandbox).
  2. Write an opencode config (provider / model credentials) into the container.
  3. Run ``opencode run --format json --dir <workdir> <problem>`` via exec.
  4. After the run, capture ``git diff HEAD`` from the repo to extract the patch.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import time
from typing import Any

from alphadiana.agent.logprob_capture import (
    apply_openai_logprob_request,
    resolve_logprob_capture_config,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Node.js + npm constants (shared path with openclaw to avoid re-downloading)
# ---------------------------------------------------------------------------
NODE_RUNTIME_ROOT = "/tmp/openclaw-node"
NODE_RUNTIME_BIN = f"{NODE_RUNTIME_ROOT}/bin"
NODE_TARBALL = "node-v22.18.0-linux-x64.tar.xz"
NODE_DOWNLOAD_URL = f"https://npmmirror.com/mirrors/node/v22.18.0/{NODE_TARBALL}"
NODE_SHA256 = "c1bfeecf1d7404fa74728f9db72e697decbd8119ccc6f5a294d795756dfcfca7"
NPM_REGISTRY = "https://registry.npmmirror.com"
OPENCODE_VERSION = "latest"

# ---------------------------------------------------------------------------
# PATH inside the container
# ---------------------------------------------------------------------------
TESTBED_PYTHON_BIN_CANDIDATES = (
    "/opt/miniconda3/envs/testbed/bin",
    "/opt/conda/envs/testbed/bin",
    "/root/miniconda3/envs/testbed/bin",
)
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
TESTBED_TOOL_PATH = ":".join(dict.fromkeys(TESTBED_PATH_SEGMENTS))

# ---------------------------------------------------------------------------
# opencode config location inside the container
# ---------------------------------------------------------------------------
OPENCODE_XDG_DIR = "/tmp/opencode-xdg"
OPENCODE_CONFIG_PATH = f"{OPENCODE_XDG_DIR}/opencode/opencode.json"

# ---------------------------------------------------------------------------
# Default install script
# ---------------------------------------------------------------------------
DEFAULT_INSTALL_OPENCODE_COMMAND = "\n".join([
    "set -e",
    # Install wget/xz if missing
    "if ! command -v wget >/dev/null 2>&1 || ! command -v xz >/dev/null 2>&1; then",
    "  apt-get update -qq",
    "  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends wget ca-certificates xz-utils",
    "  rm -rf /var/lib/apt/lists/*",
    "fi",
    # Install Node.js v22 if not present
    f"if [ ! -x {NODE_RUNTIME_BIN}/node ] || "
    f"! {NODE_RUNTIME_BIN}/node -e \"process.exit(Number(process.versions.node.split('.')[0]) >= 20 ? 0 : 1)\"; then",
    f"  rm -rf {NODE_RUNTIME_ROOT} /tmp/{NODE_TARBALL}",
    f"  wget -q -O /tmp/{NODE_TARBALL} {NODE_DOWNLOAD_URL}",
    f"  echo \"{NODE_SHA256}  /tmp/{NODE_TARBALL}\" | sha256sum -c -",
    f"  tar -xf /tmp/{NODE_TARBALL} -C /tmp",
    f"  mv /tmp/node-v22.18.0-linux-x64 {NODE_RUNTIME_ROOT}",
    f"  rm -f /tmp/{NODE_TARBALL}",
    "fi",
    f"export PATH=\"{NODE_RUNTIME_BIN}:$PATH\"",
    f"npm config set registry {NPM_REGISTRY}",
    # opencode-ai ships the platform binary via optionalDependencies.
    # `--omit=optional` breaks installation because the wrapper package
    # cannot find `opencode-linux-x64` (or the matching platform package).
    f"npm install -g opencode-ai@{OPENCODE_VERSION}",
    "npm cache clean --force || true",
])


def _progress(message: str) -> None:
    print(f"[OpenCodeContainer] {message}", flush=True)


class OpenCodeContainerRuntimeManager:
    """Bootstraps OpenCode inside a SWE-bench task container and runs it."""

    def __init__(self, config: dict) -> None:
        self._config = dict(config)
        self._timeout = int(config.get("timeout", 1800))
        self._tool_call = bool(config.get("tool_call", True))
        streaming = config.get("streaming")
        self._streaming: bool | None = bool(streaming) if streaming is not None else None
        self._variant = str(config.get("variant", "")).strip()
        self._agent_name = str(config.get("agent", "")).strip()
        self._print_logs = bool(config.get("print_logs", False))
        self._log_level = str(config.get("log_level", "")).strip()
        self._logprob_capture = resolve_logprob_capture_config(config)
        self._install_opencode_command = config.get(
            "install_opencode_command", DEFAULT_INSTALL_OPENCODE_COMMAND
        )
        # Track which sandboxes have opencode installed
        self._installed_sandboxes: set[str] = set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_env_value(self, config_key: str, env_key: str, default: str = "") -> str:
        value = str(self._config.get(config_key, "")).strip()
        if value and value.upper() != "EMPTY":
            return value
        env_val = os.environ.get(env_key, "").strip()
        if env_val:
            return env_val
        return default

    def _with_node_path(self, command: str) -> str:
        return "\n".join([
            f"if [ -d {NODE_RUNTIME_BIN} ]; then export PATH=\"{NODE_RUNTIME_BIN}:$PATH\"; fi",
            command,
        ])

    def _sandbox_metadata(self, sandbox: Any) -> dict[str, Any]:
        if hasattr(sandbox, "metadata"):
            try:
                md = sandbox.metadata()
                if isinstance(md, dict):
                    return md
            except Exception:
                _logger.debug("Failed to read sandbox metadata", exc_info=True)
        return {}

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    def _install_opencode_if_needed(self, sandbox: Any) -> None:
        check = sandbox.execute(
            self._with_node_path(
                "command -v opencode >/dev/null 2>&1 && opencode --version"
            )
        )
        if check.exit_code == 0:
            _logger.debug("opencode already installed: %s", check.stdout.strip())
            return

        if not self._install_opencode_command:
            raise RuntimeError(
                "opencode not found in container and no install_opencode_command configured"
            )
        _progress("Installing opencode inside task container")
        result = sandbox.execute(
            self._with_node_path(self._install_opencode_command)
        )
        if result.exit_code != 0:
            raise RuntimeError(
                "Failed to install opencode inside container.\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        _progress("opencode installed successfully")

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _write_opencode_config(self, sandbox: Any, *, model_name: str) -> None:
        api_base = self._resolve_env_value("api_base", "OPENAI_BASE_URL")
        api_key = self._resolve_env_value("api_key", "OPENAI_API_KEY") or "EMPTY"

        missing = []
        if not api_base:
            missing.append("OPENAI_BASE_URL (or agent.config.api_base)")
        if not model_name:
            missing.append("OPENAI_MODEL_NAME (or agent.config.model_name)")
        if missing:
            raise RuntimeError(
                "OpenCode container runtime is missing required settings: "
                + ", ".join(missing)
            )

        provider_options: dict[str, Any] = {
            "apiKey": api_key,
            "baseURL": api_base,
            "timeout": self._timeout * 1000,
        }
        if self._streaming is not None:
            provider_options["streaming"] = self._streaming
        apply_openai_logprob_request(provider_options, self._logprob_capture)

        cfg = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "custom": {
                    "api": "openai",
                    "name": "Custom Provider",
                    "options": provider_options,
                    "models": {
                        model_name: {
                            "name": model_name,
                            "tool_call": self._tool_call,
                        }
                    },
                }
            },
            "model": f"custom/{model_name}",
            "small_model": f"custom/{model_name}",
        }
        permission_cfg = self._config.get("permission")
        if isinstance(permission_cfg, dict) and permission_cfg:
            cfg["permission"] = permission_cfg
        sandbox.upload(OPENCODE_CONFIG_PATH, json.dumps(cfg, indent=2).encode("utf-8"))

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run_task(
        self,
        sandbox: Any,
        problem: str,
        *,
        model_name: str,
        task_id: str = "",
    ) -> dict[str, Any]:
        """Install opencode (once), configure it, and run on *problem*.

        Returns a dict with keys:
          - stdout: raw JSON-lines output from ``opencode run``
          - stderr: stderr text
          - exit_code: process exit code (0 = ok, 124 = timeout, other = error)
          - wall_time: elapsed seconds
          - patch: ``git diff HEAD`` output from the repo after the run
        """
        sandbox_id = str(getattr(sandbox, "sandbox_id", ""))

        # Install once per sandbox lifetime
        if sandbox_id not in self._installed_sandboxes:
            self._install_opencode_if_needed(sandbox)
            self._installed_sandboxes.add(sandbox_id)

        # Write fresh config for each task (credentials may change)
        self._write_opencode_config(sandbox, model_name=model_name)

        # Resolve repo working directory from sandbox metadata
        metadata = self._sandbox_metadata(sandbox)
        workdir = str(metadata.get("repo_workdir", "")).strip()

        # Resolve credentials (env is set inside the container via export)
        api_key = self._resolve_env_value("api_key", "OPENAI_API_KEY") or "EMPTY"
        api_base = self._resolve_env_value("api_base", "OPENAI_BASE_URL")

        # Build the opencode CLI invocation
        opencode_args = [
            "opencode", "run",
            "--format", "json",
        ]
        if workdir:
            opencode_args.extend(["--dir", workdir])
        if task_id:
            opencode_args.extend(["--title", task_id])
        opencode_args.extend(["--model", f"custom/{model_name}"])
        if self._variant:
            opencode_args.extend(["--variant", self._variant])
        if self._agent_name:
            opencode_args.extend(["--agent", self._agent_name])
        if self._print_logs:
            opencode_args.append("--print-logs")
        if self._log_level:
            opencode_args.extend(["--log-level", self._log_level])
        opencode_args.append(problem)

        shell_cmd = "\n".join([
            f"export XDG_CONFIG_HOME={OPENCODE_XDG_DIR}",
            f"export PATH=\"{NODE_RUNTIME_BIN}:{TESTBED_TOOL_PATH}:$PATH\"",
            f"export OPENAI_API_KEY={shlex.quote(api_key)}",
            f"export OPENAI_BASE_URL={shlex.quote(api_base)}",
            # Clear proxy variables that may interfere with model calls
            "unset ALL_PROXY HTTP_PROXY HTTPS_PROXY all_proxy http_proxy https_proxy 2>/dev/null || true",
            # Wrap with timeout to avoid indefinite hangs
            f"timeout {self._timeout} {shlex.join(opencode_args)}",
        ])

        _logger.info(
            "Running opencode inside container for task_id=%s timeout=%ds",
            task_id, self._timeout,
        )
        start = time.time()
        run_result = sandbox.execute(shell_cmd)
        wall_time = time.time() - start

        # Collect git diff to capture file-level changes opencode made
        patch = ""
        if workdir:
            diff_cmd = f"cd {shlex.quote(workdir)} && git diff HEAD 2>/dev/null"
            diff_result = sandbox.execute(diff_cmd)
            if diff_result.exit_code == 0:
                patch = diff_result.stdout.strip()
            else:
                _logger.debug(
                    "git diff failed (exit %d): %s",
                    diff_result.exit_code, diff_result.stderr[:500],
                )

        if run_result.exit_code == 124:
            _logger.warning(
                "opencode timed out after %ds for task_id=%s", self._timeout, task_id
            )
        elif run_result.exit_code != 0:
            _logger.warning(
                "opencode exited with code %d for task_id=%s. stderr: %s",
                run_result.exit_code, task_id, run_result.stderr[:500],
            )

        return {
            "stdout": run_result.stdout,
            "stderr": run_result.stderr,
            "exit_code": run_result.exit_code,
            "wall_time": wall_time,
            "patch": patch,
        }

    def collect_artifacts(self, sandbox: Any) -> dict[str, Any]:
        """Collect opencode session artifacts from the container."""
        session_dir = f"{OPENCODE_XDG_DIR}/opencode/sessions"
        listing = sandbox.execute(
            f"ls -t {shlex.quote(session_dir)}/ 2>/dev/null | head -5 || true"
        )
        session_files = [ln.strip() for ln in listing.stdout.splitlines() if ln.strip()]
        return {
            "xdg_dir": OPENCODE_XDG_DIR,
            "session_files": session_files,
        }

    def teardown(self) -> None:
        pass
