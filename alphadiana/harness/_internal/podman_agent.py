"""Disposable native CLI execution. No solver code is imported on the host."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from alphadiana.engine.container_runtime.podman_cli import PodmanCLI, PodmanError
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
        "training",
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
    if "training" in config:
        # Keep the optional HTTP client and training contract out of ordinary
        # native imports.  Native SFT/GRPO runs opt in explicitly and require
        # the pinned AlphaDiana agent extra at runtime.
        from alphadiana.harness.pi.training import validate_training_config

        validate_training_config(config["training"])
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


def remove_container(cli: PodmanCLI, container: str) -> None:
    """Bounded force-stop/removal; absent containers are already cleaned."""
    for attempt in range(3):
        try:
            cli.stop(container, stop_timeout=0, timeout=5, check=False)
            cli.rm(container, force=True, ignore_missing=True, check=True, timeout=15)
            return
        except PodmanError as exc:
            transient = exc.result.returncode == 124 or any(
                message in exc.result.stderr.lower()
                for message in ("device or resource busy", "being removed")
            )
            if attempt == 2 or not transient:
                raise
            time.sleep(0.1 * 2**attempt)


def cleanup_execution(execution_id: str, runtime: PodmanCLI | None = None) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", execution_id):
        raise ValueError("Invalid execution identifier")
    cli = runtime or PodmanCLI()
    for container in cli.list_ids(labels={"alphadiana.execution": execution_id}):
        remove_container(cli, container)


class _TaskBudgetExpired(TimeoutError):
    """Only the evaluator-owned execution deadline may produce scored zero."""


class NativeHarnessError(RuntimeError):
    """Native failures enter Runner's error path rather than a scored zero."""

    error_type = "native_harness_failure"


class NativePodmanAgent(Agent):
    """One container per solve, including when Runner reuses an agent instance."""

    def setup(self, config: dict) -> None:
        validate_config(config)
        if config.get("training") is not None and self.name != "pi":
            raise ValueError(
                "native-token training capture is currently supported by the Pi harness only"
            )
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
        capture = None
        capture_endpoint = None
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
            # Set public input permissions explicitly, independently of host umask.
            # Only this disposable task projection is made readable, never source
            # workspaces or evaluator evidence. The container mount remains read-only.
            public.chmod(0o755)
            for path in public.rglob("*"):
                path.chmod(0o755 if path.is_dir() else 0o644)
            request_config = {
                k: v
                for k, v in self.config.items()
                if k not in {"candidate_root", "_execution_id"}
            }
            if self.config.get("training") is not None:
                from alphadiana.harness.pi.training import (
                    Backend,
                    NativeCapture,
                    TokenCaptureEndpoint,
                    capture_config_from_dict,
                )

                sample_index = task.metadata.get("sample_index", 0)
                attempt_id = task.metadata.get("execution_id", self.execution_id)
                capture_config = capture_config_from_dict(
                    self.config["training"],
                    task_id=task.task_id,
                    sample_index=sample_index,
                    model_name=self.config["model"],
                    attempt_id=attempt_id,
                )
                if capture_config is not None:
                    capture = NativeCapture(
                        capture_config.plan,
                        capture_config.task_id,
                        capture_config.sample_index,
                        capture_config.model_name,
                        capture_config.max_calls,
                        require_logprobs=capture_config.require_logprobs,
                        attempt_id=capture_config.attempt_id,
                        model_identity=capture_config.plan.model_identity,
                        require_policy_attestation=capture_config.require_policy_attestation,
                        require_model_attestation=capture_config.require_model_attestation,
                    )
                    # The backend credential stays on the host.  Pi receives a
                    # short-lived proxy credential and can therefore never
                    # persist or print the model-service key.
                    capture_endpoint = TokenCaptureEndpoint(
                        Backend(
                            self.config["api_base"],
                            self.config.get("api_key") or "keyless",
                        ),
                        capture,
                        max(
                            0.001,
                            min(timeout - (time.monotonic() - started), 120.0),
                        ),
                        bind_host="0.0.0.0",
                        advertise_host=str(
                            self.config["training"].get(
                                "proxy_host", "host.containers.internal"
                            )
                        ),
                    )
                    try:
                        capture_endpoint.start()
                    except Exception:
                        capture_endpoint.close()
                        capture_endpoint = None
                        raise
                    request_config["api_base"] = capture_endpoint.base_url
                    request_config["api_key"] = capture_endpoint.key
                    request_config["training"] = capture_config.to_dict()
            request = {
                "harness": self.name,
                "problem": task.problem,
                "config": request_config,
            }
            if capture is not None:
                request.update(
                    task_id=task.task_id,
                    sample_index=task.metadata.get("sample_index", 0),
                    execution_id=task.metadata.get("execution_id", self.execution_id),
                )
            volumes = [f"{public}:/input:ro"]
            if "candidate_root" in self.config:
                volumes.append(f"{Path(self.config['candidate_root']).resolve()}:/candidate:ro")
            self._containers.add(name)
            try:
                self.runtime.create(
                    self.config["image"],
                    name=name,
                    remove=False,
                    command=["/opt/runtime/bin/python", "/opt/bridge.py"],
                    user="1000:1000",
                    workdir="/work",
                    network="slirp4netns:allow_host_loopback=true",
                    volumes=volumes,
                    env={"HOME": "/tmp/home", "PYTHONDONTWRITEBYTECODE": "1"},
                    extra_args=[
                        "--pull=never",
                        "--http-proxy=false",
                        "--interactive",
                        "--read-only",
                        "--stop-timeout=0",
                        "--cap-drop=ALL",
                        "--security-opt=no-new-privileges",
                        "--tmpfs=/work:rw,mode=1777",
                        "--tmpfs=/tmp:rw,mode=1777",
                        "--label",
                        f"alphadiana.execution={self.execution_id}",
                    ],
                    timeout=30,
                )
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise _TaskBudgetExpired("Task deadline exhausted after container creation")
                try:
                    result = self.runtime.start_attached(
                        name, input_text=json.dumps(request), timeout=remaining
                    )
                except PodmanError as exc:
                    # Only start/attach uses the remaining task budget. A timeout
                    # from create/cleanup is infrastructure failure, not a score.
                    if exc.result.returncode == 124:
                        raise _TaskBudgetExpired() from None
                    raise
                response = json.loads(result.stdout)
                if (
                    not isinstance(response, dict)
                    or not isinstance(response.get("answer"), str)
                    or (
                        not response["answer"].strip() and response.get("finish_reason") != "length"
                    )
                ):
                    raise ValueError("Missing final assistant response")
                if response.get("finish_reason") not in {"stop", "completed", "length"}:
                    raise ValueError("Harness did not complete normally")
                training_capture = None
                if capture is not None:
                    training_capture = capture.capture().to_dict()
                    response["training_capture"] = training_capture
                    # Cross-repository consumers use this stable projection;
                    # ``training_capture`` remains the Diana-native name.
                    hebe_training = copy.deepcopy(training_capture)
                    response["hebe_training"] = hebe_training
                    response_json_value = response.get("response_json")
                    response_json = (
                        dict(response_json_value)
                        if isinstance(response_json_value, dict)
                        else {}
                    )
                    # Keep the exact native requests/responses alongside the
                    # normal Pi event stream for replay and contract audits.
                    response_json["native_training_capture"] = training_capture
                    response["response_json"] = response_json
                return AgentResponse(
                    answer=response["answer"],
                    trajectory=response.get("trajectory", []),
                    reasoning_trajectory=response.get("reasoning_trajectory", []),
                    token_usage=response.get("token_usage", {}),
                    response_json=response.get("response_json", {}),
                    finish_reason=response["finish_reason"],
                    wall_time_sec=time.monotonic() - started,
                    metadata={
                        "runtime": "podman",
                        "image": self.config["image"],
                        "candidate_digest": self.config.get("candidate_digest"),
                        "usage_status": "reported"
                        if response.get("token_usage")
                        else "unavailable",
                        **(
                            {"training_capture": training_capture}
                            if training_capture is not None
                            else {}
                        ),
                        **(
                            {"hebe_training": hebe_training}
                            if training_capture is not None
                            else {}
                        ),
                    },
                    sandbox_metadata={"isolation_mode": "podman_disposable_task_container"},
                )
            except _TaskBudgetExpired:
                if capture is not None:
                    error = NativeHarnessError(
                        "Native training capture did not complete before the task deadline"
                    )
                    error.error_type = "native_harness_training_timeout"
                    raise error from None
                return AgentResponse(
                    answer=None,
                    finish_reason="timeout",
                    wall_time_sec=time.monotonic() - started,
                    metadata={
                        "runtime": "podman",
                        "image": self.config["image"],
                        "native_harness_timeout_scored_zero": True,
                        "native_harness_timeout_seconds": timeout,
                        "usage_status": "unavailable",
                    },
                    sandbox_metadata={"isolation_mode": "podman_disposable_task_container"},
                )
            except Exception as exc:
                # Runner treats answer=None as a scored zero. Raise a sanitized
                # error so failed solvers never contribute valid evidence.
                stage = "protocol"
                if isinstance(exc, PodmanError):
                    stage = "timeout" if exc.result.returncode == 124 else "container"
                    try:
                        diagnostic = json.loads(exc.result.stderr.strip().splitlines()[-1])
                        if diagnostic.get("stage") in {
                            "initialization",
                            "plugin",
                            "execution",
                            "protocol",
                        }:
                            stage = diagnostic["stage"]
                    except (ValueError, IndexError, AttributeError):
                        pass
                elif isinstance(exc, TimeoutError):
                    stage = "timeout"
                error = NativeHarnessError(f"Native harness failed during {stage}")
                error.error_type = f"native_harness_{stage}"
                raise error from None
            finally:
                if capture_endpoint is not None:
                    capture_endpoint.close()
                remove_container(self.runtime, name)
                self._containers.discard(name)

    def teardown(self) -> None:
        # Runner may tear down one recreated agent while siblings still solve.
        # Only the outer evaluator may clean the whole execution label.
        for name in tuple(getattr(self, "_containers", ())):
            remove_container(self.runtime, name)
            self._containers.discard(name)
