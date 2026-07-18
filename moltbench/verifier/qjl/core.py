"""Private QJL verifier core used only by the verifier image."""

from __future__ import annotations

import json
import math
import os
import pwd
import signal
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

EDITABLE_PREFIX = "qjl_kernel/csrc/"
SANDBOX_USER = os.environ.get("QJL_SANDBOX_USER", "nobody")


def _sandbox_identity() -> tuple[int, int]:
    if os.geteuid() != 0:
        return os.geteuid(), os.getegid()
    account = pwd.getpwnam(SANDBOX_USER)
    return account.pw_uid, account.pw_gid


def kill_sandbox_processes() -> None:
    if os.geteuid() != 0:
        return
    sandbox_uid, _ = _sandbox_identity()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8")
            uid_line = next(
                line for line in status.splitlines() if line.startswith("Uid:")
            )
            real_uid = int(uid_line.split()[1])
            if real_uid == sandbox_uid:
                os.kill(int(entry.name), signal.SIGKILL)
        except (FileNotFoundError, PermissionError, ProcessLookupError, StopIteration):
            continue


def evaluate_candidate_isolated(
    payload: dict[str, Any],
    *,
    artifact_dir: Path,
    label: str,
) -> dict[str, Any]:
    try:
        return evaluate_candidate(payload, artifact_dir=artifact_dir, label=label)
    finally:
        kill_sandbox_processes()


def _chown_tree(root: Path, uid: int, gid: int) -> None:
    os.chown(root, uid, gid)
    for path in root.rglob("*"):
        os.chown(path, uid, gid, follow_symlinks=False)


def _sandbox_env(workspace: Path) -> dict[str, str]:
    home = workspace / ".sandbox-home"
    home.mkdir(mode=0o700, exist_ok=True)
    allowed = {
        "CC",
        "CFLAGS",
        "CPATH",
        "CPLUS_INCLUDE_PATH",
        "CXX",
        "CXXFLAGS",
        "CUDA_HOME",
        "CUDA_VISIBLE_DEVICES",
        "C_INCLUDE_PATH",
        "LD_LIBRARY_PATH",
        "LIBRARY_PATH",
        "NVIDIA_DRIVER_CAPABILITIES",
        "NVIDIA_VISIBLE_DEVICES",
        "NVCC_THREADS",
        "PATH",
        "TORCH_CUDA_ARCH_LIST",
    }
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(
        {
            "HOME": str(home),
            "PYTHONPATH": str(workspace),
            "QJL_WORKSPACE_ROOT": str(workspace),
            "TMPDIR": str(workspace / ".tmp"),
        }
    )
    (workspace / ".tmp").mkdir(mode=0o700, exist_ok=True)
    return env


def _failure(reason: str, wall_time_sec: float, status: str = "FAIL") -> dict[str, Any]:
    return {
        "correct": False,
        "score": 0.0,
        "metrics": {
            "runtime_ms": 0.0,
            "distortion": 1.0,
            "gain": 0.0,
        },
        "wall_time_sec": wall_time_sec,
        "verifier_status": status,
        "failure_reason": reason,
    }


def validate_candidate(payload: dict[str, Any]) -> dict[str, str]:
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ValueError("candidate.files must be an object")
    clean: dict[str, str] = {}
    for raw_path, content in files.items():
        path = str(raw_path)
        if (
            not path.startswith(EDITABLE_PREFIX)
            or not path.endswith(".cu")
            or Path(path).parent.as_posix() != EDITABLE_PREFIX.rstrip("/")
            or ".." in Path(path).parts
            or Path(path).name == ""
        ):
            raise ValueError(f"non-editable path rejected: {path}")
        if not isinstance(content, str):
            raise ValueError(f"candidate content must be text: {path}")
        if len(content.encode("utf-8")) > 4 * 1024 * 1024:
            raise ValueError(f"candidate file too large: {path}")
        clean[path] = content
    return clean


def _last_json_object(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "runtime_ms" in payload:
            return payload
    raise ValueError("benchmark produced no structured result")


def evaluate_candidate(
    payload: dict[str, Any],
    *,
    artifact_dir: Path,
    label: str,
) -> dict[str, Any]:
    started = time.monotonic()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        files = validate_candidate(payload)
    except ValueError as exc:
        return _failure(str(exc), time.monotonic() - started, "REJECTED")

    seed_root = Path(os.environ.get("QJL_SEED_ROOT", "/opt/qjl-seed"))
    threshold = float(os.environ.get("QJL_DISTORTION_THRESHOLD", "0.52"))
    baseline_runtime = float(os.environ["QJL_BASELINE_RUNTIME_MS"])
    build_timeout = int(os.environ.get("QJL_BUILD_TIMEOUT_SEC", "900"))
    eval_timeout = int(os.environ.get("QJL_EVAL_TIMEOUT_SEC", "300"))
    attempt_timeout = int(
        os.environ.get(
            "QJL_ATTEMPT_TIMEOUT_SEC",
            str(build_timeout + eval_timeout * 3),
        )
    )
    hidden_seeds = [
        int(value)
        for value in os.environ.get("QJL_HIDDEN_SEEDS", "42,1234,2025").split(",")
        if value.strip()
    ]

    with tempfile.TemporaryDirectory(prefix=f"qjl-{label}-") as temp_dir:
        deadline = started + attempt_timeout

        def remaining_timeout(limit: int) -> float:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("attempt_timeout")
            return min(float(limit), remaining)

        workspace = Path(temp_dir) / "repo"
        shutil.copytree(seed_root, workspace, symlinks=True)
        shutil.rmtree(workspace / "qjl_kernel" / "build", ignore_errors=True)
        for generated in (workspace / "qjl_kernel").glob("*.so"):
            generated.unlink()
        if not bool(payload.get("use_seed")):
            for existing in (workspace / "qjl_kernel" / "csrc").glob("*.cu"):
                existing.unlink()
        for relative, content in files.items():
            target = workspace / relative
            target.write_text(content, encoding="utf-8")
        sandbox_uid, sandbox_gid = _sandbox_identity()
        sandbox_env = _sandbox_env(workspace)
        _chown_tree(workspace, sandbox_uid, sandbox_gid)

        build_log = artifact_dir / f"{label}_build.log"
        try:
            build = subprocess.run(
                ["python3", "setup.py", "build_ext", "--inplace"],
                cwd=workspace / "qjl_kernel",
                capture_output=True,
                text=True,
                timeout=remaining_timeout(build_timeout),
                env=sandbox_env,
                user=sandbox_uid,
                group=sandbox_gid,
                extra_groups=[],
                start_new_session=True,
            )
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            build_log.write_text(
                f"{getattr(exc, 'stdout', '') or ''}\n"
                f"{getattr(exc, 'stderr', '') or ''}",
                encoding="utf-8",
            )
            reason = "attempt_timeout" if isinstance(exc, TimeoutError) else "build_timeout"
            return _failure(reason, time.monotonic() - started, "TIMEOUT")
        build_log.write_text(
            f"{build.stdout}\n{build.stderr}",
            encoding="utf-8",
        )
        if build.returncode != 0:
            return _failure("build_failed", time.monotonic() - started)

        runtimes: list[float] = []
        distortions: list[float] = []
        test_log = artifact_dir / f"{label}_test.log"
        log_parts: list[str] = []
        for seed in hidden_seeds:
            try:
                bench = subprocess.run(
                    [
                        "python3",
                        "/verifier/bench_cuda.py",
                        "--seed",
                        str(seed),
                        "--distortion-threshold",
                        str(threshold),
                    ],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=remaining_timeout(eval_timeout),
                    env=sandbox_env,
                    user=sandbox_uid,
                    group=sandbox_gid,
                    extra_groups=[],
                    start_new_session=True,
                )
            except (subprocess.TimeoutExpired, TimeoutError) as exc:
                log_parts.append(
                    f"seed={seed}\n{getattr(exc, 'stdout', '') or ''}\n"
                    f"{getattr(exc, 'stderr', '') or ''}"
                )
                test_log.write_text("\n".join(log_parts), encoding="utf-8")
                reason = (
                    "attempt_timeout"
                    if isinstance(exc, TimeoutError)
                    else "benchmark_timeout"
                )
                return _failure(reason, time.monotonic() - started, "TIMEOUT")
            log_parts.append(
                f"seed={seed} exit={bench.returncode}\n{bench.stdout}\n{bench.stderr}"
            )
            try:
                result = _last_json_object(bench.stdout)
            except ValueError:
                test_log.write_text("\n".join(log_parts), encoding="utf-8")
                return _failure("benchmark_parse_failed", time.monotonic() - started)
            runtime_value = float(result["runtime_ms"])
            distortion_value = float(result["distortion"])
            if (
                not math.isfinite(runtime_value)
                or runtime_value <= 0
                or not math.isfinite(distortion_value)
                or distortion_value < 0
            ):
                test_log.write_text("\n".join(log_parts), encoding="utf-8")
                return _failure(
                    "benchmark_invalid_metrics",
                    time.monotonic() - started,
                    "REJECTED",
                )
            runtimes.append(runtime_value)
            distortions.append(distortion_value)
            if bench.returncode != 0 or not bool(result.get("correctness_pass")):
                test_log.write_text("\n".join(log_parts), encoding="utf-8")
                runtime_ms = statistics.median(runtimes)
                distortion = max(distortions)
                failed = _failure("distortion_threshold", time.monotonic() - started)
                failed["metrics"] = {
                    "runtime_ms": runtime_ms,
                    "distortion": distortion,
                    "gain": 0.0,
                }
                return failed

        test_log.write_text("\n".join(log_parts), encoding="utf-8")
        runtime_ms = statistics.median(runtimes)
        distortion = max(distortions)
        gain = baseline_runtime / runtime_ms if runtime_ms > 0 else 0.0
        return {
            "correct": bool(runtime_ms > 0 and distortion < threshold),
            "score": gain,
            "metrics": {
                "baseline_runtime_ms": baseline_runtime,
                "runtime_ms": runtime_ms,
                "distortion": distortion,
                "gain": gain,
            },
            "wall_time_sec": time.monotonic() - started,
            "verifier_status": "PASS",
            "failure_reason": None,
        }
