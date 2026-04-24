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
from pathlib import Path
from typing import Any

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
_PRESERVED_TEXT_ARTIFACT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
}

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


def _redact_secret_fields(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in {"api_key", "apikey", "authorization", "token"} or normalized.endswith("_api_key"):
                redacted[key] = "REDACTED"
            else:
                redacted[key] = _redact_secret_fields(item)
        return redacted
    if isinstance(value, list):
        return [_redact_secret_fields(item) for item in value]
    return value


def _sanitize_json_text(raw_text: str) -> str:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return raw_text
    return json.dumps(_redact_secret_fields(payload), indent=2, ensure_ascii=False)


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
        if getattr(result, "exit_code", 1) != 0:
            return ""
        stdout = getattr(result, "stdout", "")
        return stdout if isinstance(stdout, str) else ""

    def _summarize_sqlite_db(self, sandbox: Any, db_path: str) -> str:
        script = """
import json
import os
import pathlib
import sqlite3

db_path = pathlib.Path(os.environ["DB_PATH"])
summary = {"path": str(db_path)}
if db_path.exists():
    summary["size_bytes"] = db_path.stat().st_size
else:
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    raise SystemExit(0)

conn = None
try:
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    tables = []
    schema = []
    for name, sql in cur.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall():
        row_count = None
        try:
            row_count = cur.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except Exception:
            row_count = None
        tables.append({"name": name, "row_count": row_count})
        schema.append({"name": name, "sql": sql})
    summary["tables"] = tables
    summary["schema"] = schema
    preview_tables = [name for name in ("session", "message", "part") if any(item["name"] == name for item in tables)]
    previews = {}
    for name in preview_tables:
        columns = [row[1] for row in cur.execute(f"PRAGMA table_info('{name}')").fetchall()]
        rows = cur.execute(f'SELECT * FROM "{name}" ORDER BY rowid LIMIT 10').fetchall()
        preview_rows = []
        for row in rows:
            item = {}
            for index, column in enumerate(columns):
                value = row[index]
                if isinstance(value, bytes):
                    value = value.decode("utf-8", "replace")
                if isinstance(value, str) and len(value) > 2000:
                    value = value[:2000] + "...<truncated>"
                item[column] = value
            preview_rows.append(item)
        previews[name] = preview_rows
    if previews:
        summary["previews"] = previews
except Exception as exc:
    summary["error"] = f"{type(exc).__name__}: {exc}"
finally:
    if conn is not None:
        conn.close()

print(json.dumps(summary, ensure_ascii=False, indent=2))
"""
        command = (
            f"DB_PATH={shlex.quote(db_path)} python3 - <<'PY'\n"
            f"{script}"
            "PY"
        )
        try:
            result = sandbox.execute(command)
        except Exception:
            return ""
        if getattr(result, "exit_code", 1) != 0:
            return ""
        stdout = getattr(result, "stdout", "")
        return stdout if isinstance(stdout, str) else ""

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
        artifact_root = f"{OPENCODE_XDG_DIR}/opencode"
        listing = sandbox.execute(
            f"find {shlex.quote(artifact_root)} -maxdepth 4 -type f | sort 2>/dev/null || true"
        )
        artifact_paths = [
            line.strip()
            for line in listing.stdout.splitlines()
            if line.strip().startswith("/")
        ]
        session_listing = sandbox.execute(
            f"ls -t {shlex.quote(artifact_root)}/sessions/*.jsonl 2>/dev/null | head -5 || true"
        )
        session_files = [
            ln.strip()
            for ln in session_listing.stdout.splitlines()
            if ln.strip().endswith(".jsonl")
        ]

        workspace_file_contents: dict[str, str] = {}
        manifest_files: dict[str, str] = {}
        state_files: list[str] = []
        memory_files: list[str] = []

        if artifact_paths:
            workspace_file_contents["opencode_workspace_listing.txt"] = "".join(
                f"{Path(path).relative_to(Path(artifact_root)).as_posix()}\n"
                for path in artifact_paths
            )
            manifest_files["workspace_listing"] = "opencode_workspace_listing.txt"

        config_text = self._read_text_best_effort(sandbox, OPENCODE_CONFIG_PATH)
        if config_text.strip():
            workspace_file_contents["opencode_config.json"] = _sanitize_json_text(config_text)
            manifest_files["runtime_config"] = "opencode_config.json"
            state_files.append("opencode_config.json")

        selected_session_path = ""
        for remote_path in session_files[:3]:
            text = self._read_text_best_effort(sandbox, remote_path)
            if not text.strip():
                continue
            selected_session_path = remote_path
            workspace_file_contents["opencode_session.jsonl"] = text
            manifest_files["session_trace"] = "opencode_session.jsonl"
            state_files.append("opencode_session.jsonl")
            session_alias = f"state/sessions/{Path(remote_path).name}"
            workspace_file_contents[session_alias] = text
            state_files.append(session_alias)
            break

        db_paths = [path for path in artifact_paths if Path(path).name.startswith("opencode.db")]
        if db_paths:
            inventory_alias = "memory/opencode_db_files.json"
            workspace_file_contents[inventory_alias] = json.dumps(
                {
                    "files": [
                        {
                            "path": Path(path).relative_to(Path(artifact_root)).as_posix(),
                            "size_bytes": int(
                                getattr(
                                    sandbox.execute(f"wc -c < {shlex.quote(path)} || true"),
                                    "stdout",
                                    "0",
                                ).strip() or "0"
                            ),
                        }
                        for path in db_paths
                    ]
                },
                indent=2,
                ensure_ascii=False,
            )
            manifest_files["memory_inventory"] = inventory_alias
            memory_files.append(inventory_alias)

            primary_db = next((path for path in db_paths if Path(path).name == "opencode.db"), "")
            if primary_db:
                summary_text = self._summarize_sqlite_db(sandbox, primary_db)
                if summary_text.strip():
                    summary_alias = "memory/opencode.db.summary.json"
                    workspace_file_contents[summary_alias] = summary_text
                    manifest_files["memory_summary"] = summary_alias
                    memory_files.append(summary_alias)

        for remote_path in artifact_paths:
            relative = Path(remote_path).relative_to(Path(artifact_root)).as_posix()
            if remote_path == OPENCODE_CONFIG_PATH:
                continue
            if relative.startswith("sessions/"):
                continue
            if Path(remote_path).name.startswith("opencode.db"):
                continue
            if Path(relative).suffix.lower() not in _PRESERVED_TEXT_ARTIFACT_SUFFIXES:
                continue
            text = self._read_text_best_effort(sandbox, remote_path)
            if not text.strip():
                continue
            alias = f"state/{relative}"
            workspace_file_contents[alias] = text
            state_files.append(alias)

        artifact_manifest: dict[str, Any] = {"files": manifest_files} if manifest_files else {}
        if state_files:
            artifact_manifest["state_files"] = list(dict.fromkeys(state_files))
        if memory_files:
            artifact_manifest["memory_files"] = list(dict.fromkeys(memory_files))
        return {
            "xdg_dir": OPENCODE_XDG_DIR,
            "session_files": session_files,
            "artifact_manifest": artifact_manifest,
            "workspace_snapshot_paths": artifact_paths,
            "workspace_file_contents": workspace_file_contents,
            "sandbox_metadata": self._sandbox_metadata(sandbox),
            "selected_session_path": selected_session_path,
        }

    def teardown(self) -> None:
        pass
