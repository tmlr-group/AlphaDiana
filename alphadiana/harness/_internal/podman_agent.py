"""Disposable native CLI execution. No solver code is imported on the host."""

from __future__ import annotations

import hashlib
import json
import math
import re
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from alphadiana.engine.container_runtime.podman_cli import PodmanCLI
from alphadiana.harness.base import Agent, AgentResponse

IMAGE = re.compile(r"(?:sha256:|[^\s]+@sha256:)[0-9a-f]{64}$")
AGENTS = frozenset({"pi", "deepseek_harness"})
FIELDS = frozenset(
    {
        "image",
        "model",
        "api_base",
        "api_key",
        "system_prompt",
        "timeout",
        "max_tokens",
        "reasoning",
        "candidate_root",
        "candidate_entry",
        "candidate_digest",
        "runtime",
        "_execution_id",
    }
)


def relative_file(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(p in {".", ".."} for p in value.split("/"))
        or "\\" in value
    ):
        raise ValueError("Expected a safe relative file path")
    return str(path)


def tree_digest(root: Path) -> str:
    """Hash all retained files/modes; reject symlinks and special files."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Candidate symlinks are forbidden")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError("Candidate contains a special file")
        mode = "100755" if path.stat().st_mode & 0o111 else "100644"
        digest.update(path.relative_to(root).as_posix().encode() + b"\0" + mode.encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def validate_config(config: dict) -> None:
    if set(config) - FIELDS:
        raise ValueError("Unsupported native Podman harness configuration fields")
    if config.get("runtime", "podman") != "podman":
        raise ValueError("Native harnesses require Podman; local fallback is forbidden")
    if not isinstance(config.get("image"), str) or not IMAGE.fullmatch(config["image"]):
        raise ValueError("image must be an immutable sha256 ID or repository@sha256 digest")
    for field in ("model", "api_base"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError(f"{field} is required")
    timeout = config.get("timeout", 120)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be positive and finite")
    if "max_tokens" in config and (
        type(config["max_tokens"]) is not int or config["max_tokens"] < 1
    ):
        raise ValueError("max_tokens must be a positive integer")
    if "system_prompt" in config and not isinstance(config["system_prompt"], str):
        raise ValueError("system_prompt must be a string")
    if "reasoning" in config and (
        not isinstance(config["reasoning"], str) or not config["reasoning"]
    ):
        raise ValueError("reasoning must be a nonempty string")
    if "_execution_id" in config and not re.fullmatch(r"[0-9a-f]{32}", config["_execution_id"]):
        raise ValueError("Invalid private execution identifier")
    source = {"candidate_root", "candidate_entry", "candidate_digest"}
    if source.intersection(config):
        if not source.issubset(config):
            raise ValueError("Candidate requires root, entry and digest together")
        entry = relative_file(config["candidate_entry"])
        root = Path(config["candidate_root"])
        if not root.is_dir() or root.is_symlink() or not (root / entry).is_file():
            raise ValueError("Candidate root/entry is unavailable")
        if (
            not re.fullmatch(r"[0-9a-f]{64}", config["candidate_digest"])
            or tree_digest(root) != config["candidate_digest"]
        ):
            raise ValueError("Candidate content identity differs")


def cleanup_execution(execution_id: str, runtime: PodmanCLI | None = None) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", execution_id):
        raise ValueError("Invalid execution identifier")
    cli = runtime or PodmanCLI()
    for container in cli.list_ids(labels={"alphadiana.execution": execution_id}):
        cli.rm(container, force=True, check=True)


class NativePodmanAgent(Agent):
    """One container per solve, including when Runner reuses an agent instance."""

    def setup(self, config: dict) -> None:
        validate_config(config)
        self.config = dict(config)
        self._containers = set()
        self.runtime = PodmanCLI()
        self.execution_id = config.get("_execution_id") or uuid.uuid4().hex
        if not self.runtime.rootless():
            raise ValueError("Native harnesses require rootless Podman")
        if not self.runtime.image_exists(config["image"]):
            raise ValueError("Pinned harness image is not installed; build/pull it explicitly")

    def solve(self, task, sandbox=None) -> AgentResponse:
        if sandbox is not None:
            raise ValueError(
                "This agent owns Podman isolation; an additional sandbox is unsupported"
            )
        validate_config(self.config)
        name = f"diana-{self.name.replace('_', '-')}-{uuid.uuid4().hex}"
        started = time.monotonic()
        timeout = float(self.config.get("timeout", 120))
        with tempfile.TemporaryDirectory(prefix="diana-harness-") as directory:
            root = Path(directory)
            # Container uid can traverse this public task input, but cannot change it.
            root.chmod(0o755)
            public = root / "public"
            public.mkdir()
            for key, data in task.attachments.items():
                relative = relative_file(key)
                target = public / "attachments" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            request = {
                "harness": self.name,
                "problem": task.problem,
                "config": {
                    k: v
                    for k, v in self.config.items()
                    if k not in {"candidate_root", "_execution_id"}
                },
            }
            volumes = [f"{public}:/input:ro"]
            if "candidate_root" in self.config:
                volumes.append(f"{Path(self.config['candidate_root']).resolve()}:/candidate:ro")
            self._containers.add(name)
            try:
                result = self.runtime.run(
                    self.config["image"],
                    name=name,
                    remove=False,
                    command=["/opt/runtime/bin/python", "/opt/bridge.py"],
                    input_text=json.dumps(request),
                    user="1000:1000",
                    workdir="/work",
                    network="slirp4netns:allow_host_loopback=true",
                    volumes=volumes,
                    env={"HOME": "/tmp/home", "PYTHONDONTWRITEBYTECODE": "1"},
                    extra_args=[
                        "--pull=never",
                        "--read-only",
                        "--cap-drop=ALL",
                        "--security-opt=no-new-privileges",
                        "--tmpfs=/work:rw,mode=1777",
                        "--tmpfs=/tmp:rw,mode=1777",
                        "--label",
                        f"alphadiana.execution={self.execution_id}",
                    ],
                    timeout=timeout,
                )
                response = json.loads(result.stdout)
                if (
                    not isinstance(response, dict)
                    or not isinstance(response.get("answer"), str)
                    or not response["answer"].strip()
                ):
                    raise ValueError("Missing final assistant response")
                if response.get("finish_reason") not in {"stop", "completed"}:
                    raise ValueError("Harness did not complete normally")
                return AgentResponse(
                    answer=response["answer"],
                    trajectory=response.get("trajectory", []),
                    reasoning_trajectory=response.get("reasoning_trajectory", []),
                    token_usage=response.get("token_usage", {}),
                    finish_reason=response["finish_reason"],
                    wall_time_sec=time.monotonic() - started,
                    metadata={
                        "runtime": "podman",
                        "image": self.config["image"],
                        "candidate_digest": self.config.get("candidate_digest"),
                        "usage_status": "reported"
                        if response.get("token_usage")
                        else "unavailable",
                    },
                    sandbox_metadata={"isolation_mode": "podman_disposable_task_container"},
                )
            except Exception as exc:
                # CLI/SDK diagnostics can include credentials or private model output.
                return AgentResponse(
                    answer=None,
                    wall_time_sec=time.monotonic() - started,
                    metadata={"failure_reason": type(exc).__name__, "runtime": "podman"},
                )
            finally:
                self.runtime.rm(name, force=True, check=True)
                self._containers.discard(name)

    def teardown(self) -> None:
        # Runner may tear down one recreated agent while siblings still solve.
        # Only the outer evaluator may clean the whole execution label.
        for name in tuple(getattr(self, "_containers", ())):
            self.runtime.rm(name, force=True, check=True)
            self._containers.discard(name)
