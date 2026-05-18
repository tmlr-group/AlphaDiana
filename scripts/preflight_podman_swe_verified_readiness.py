#!/usr/bin/env python3
"""Preflight Phase 9 SWE-bench Verified Podman readiness."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

import yaml

from alphadiana.benchmark.base import load_dataset_with_retry
from alphadiana.container_runtime.podman_socket import (
    default_podman_socket_path,
    podman_socket_env,
)
from alphadiana.utils.swebench import (
    build_swebench_instance,
    qualify_swebench_test_spec_for_podman,
)
from alphadiana.benchmark.base import BenchmarkTask


REQUIRED_ENV = (
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL_NAME",
)
EXPECTED_TIERS = {
    "smoke": 2,
    "pilot32": 10,
    "long64": 2,
    "sample128": 2,
}
EXPECTED_AGENTS = {"openclaw", "opencode", "zeroclaw"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def derive_container_provider_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        return base_url
    netloc = "host.containers.internal"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))


def _sanitize_url(raw: str) -> str:
    parsed = urlparse(raw)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "", ""))


def _run(cmd: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


@contextmanager
def _patched_env(env: dict[str, str]):
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _config_paths(config_dir: Path) -> list[Path]:
    return sorted(config_dir.glob("*.yaml"))


def _taskset_path(config: dict[str, Any], root: Path) -> Path:
    metadata = _as_dict(config.get("metadata"))
    benchmark_config = _as_dict(_as_dict(config.get("benchmark")).get("config"))
    raw = str(metadata.get("taskset_path") or benchmark_config.get("taskset_path") or "").strip()
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _probe_host_provider(base_url: str, api_key: str, timeout: int) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with urlopen(Request(url, headers=headers), timeout=timeout) as response:
            body = response.read(2048)
            return {
                "ok": 200 <= int(response.status) < 300 and bool(body),
                "status": int(response.status),
                "response_non_empty": bool(body),
            }
    except Exception as exc:
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}


def _probe_provider_from_podman(
    *,
    image: str,
    base_url: str,
    api_key: str,
    model_name: str,
    network: str,
    timeout: int,
) -> dict[str, Any]:
    code = r"""
import json
import os
from urllib.request import Request, urlopen

base = os.environ["OPENAI_BASE_URL"].rstrip("/")
headers = {}
if os.environ.get("OPENAI_API_KEY"):
    headers["Authorization"] = "Bearer " + os.environ["OPENAI_API_KEY"]
try:
    with urlopen(Request(base + "/models", headers=headers), timeout=20) as response:
        body = response.read(2048)
        ok = 200 <= response.status < 300 and bool(body)
        print(json.dumps({
            "ok": ok,
            "status": response.status,
            "response_non_empty": bool(body),
            "model_name": os.environ.get("OPENAI_MODEL_NAME", ""),
        }))
        raise SystemExit(0 if ok else 2)
except Exception as exc:
    print(json.dumps({
        "ok": False,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "model_name": os.environ.get("OPENAI_MODEL_NAME", ""),
    }))
    raise SystemExit(2)
"""
    cmd = [
        "podman",
        "run",
        "--rm",
        "-e",
        f"OPENAI_BASE_URL={base_url}",
        "-e",
        f"OPENAI_API_KEY={api_key}",
        "-e",
        f"OPENAI_MODEL_NAME={model_name}",
    ]
    if network:
        cmd.extend(["--network", network])
    cmd.extend([image, "python", "-c", code])
    try:
        result = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error_type": "timeout", "error": f"probe timed out after {timeout}s"}
    detail: dict[str, Any] = {}
    stdout = result.stdout.strip()
    if stdout:
        try:
            detail = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            detail = {"raw_stdout_tail": stdout[-1000:]}
    detail.setdefault("ok", result.returncode == 0)
    detail["returncode"] = result.returncode
    if result.stderr.strip():
        detail["stderr_tail"] = result.stderr.strip()[-1000:]
    return detail


def _task_from_row(index: int, row: dict[str, Any]) -> BenchmarkTask:
    return BenchmarkTask(
        task_id=f"swe_{row['instance_id']}",
        problem=str(row.get("problem_statement") or ""),
        ground_truth=str(row.get("patch") or ""),
        metadata={
            "instance_id": row["instance_id"],
            "repo": row.get("repo", ""),
            "base_commit": row.get("base_commit", ""),
            "version": row.get("version", ""),
            "FAIL_TO_PASS": row.get("FAIL_TO_PASS", ""),
            "PASS_TO_PASS": row.get("PASS_TO_PASS", ""),
            "test_patch": row.get("test_patch", ""),
            "environment_setup_commit": row.get("environment_setup_commit", ""),
        },
    )


def _image_qualification_probe(dataset_name: str, split: str, instance_id: str) -> dict[str, Any]:
    try:
        from swebench.harness.test_spec.test_spec import make_test_spec
    except Exception as exc:
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}

    dataset = load_dataset_with_retry(dataset_name, None, split=split)
    selected: tuple[int, dict[str, Any]] | None = None
    for index, row in enumerate(dataset):
        if str(row.get("instance_id") or "") == instance_id:
            selected = (index, row)
            break
    if selected is None:
        return {"ok": False, "error_type": "missing_task", "error": instance_id}
    index, row = selected
    instance = build_swebench_instance(_task_from_row(index, row))
    spec = make_test_spec(
        instance,
        namespace="swebench",
        env_image_tag="latest",
        instance_image_tag="latest",
        arch="x86_64",
    )
    before = {
        "base_image_key": getattr(spec, "base_image_key", ""),
        "env_image_key": getattr(spec, "env_image_key", ""),
        "instance_image_key": getattr(spec, "instance_image_key", ""),
    }
    qualify_swebench_test_spec_for_podman(spec)
    after = {
        "base_image_key": getattr(spec, "base_image_key", ""),
        "env_image_key": getattr(spec, "env_image_key", ""),
        "instance_image_key": getattr(spec, "instance_image_key", ""),
    }
    unqualified = [
        value for value in after.values()
        if isinstance(value, str) and value.startswith("sweb.")
    ]
    dockerfile_refs = "\n".join(
        str(getattr(spec, attr, "") or "")
        for attr in ("base_dockerfile", "env_dockerfile", "instance_dockerfile")
    )
    if "FROM sweb." in dockerfile_refs:
        unqualified.append("dockerfile:FROM sweb.")
    return {"ok": not unqualified, "before": before, "after": after, "unqualified": unqualified}


def preflight(
    *,
    config_dir: Path,
    output_path: Path,
    root: Path,
    provider_timeout: int,
    podman_provider_image: str,
    podman_provider_network: str,
) -> dict[str, Any]:
    configs = [{"path": path, "config": _load_yaml(path)} for path in _config_paths(config_dir)]
    failures: list[str] = []
    missing_env = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing_env:
        failures.append("missing_required_env")
    if not configs:
        failures.append("missing_config")

    by_tier: dict[str, list[dict[str, Any]]] = {}
    for item in configs:
        metadata = _as_dict(item["config"].get("metadata"))
        tier = str(metadata.get("tier") or "").strip()
        by_tier.setdefault(tier, []).append(item)

    config_checks: list[dict[str, Any]] = []
    for tier, expected_count in EXPECTED_TIERS.items():
        tier_configs = by_tier.get(tier, [])
        agents = {str(_as_dict(item["config"].get("agent")).get("name") or "") for item in tier_configs}
        if agents != EXPECTED_AGENTS or len(tier_configs) != 3:
            failures.append("config_matrix")
        for item in tier_configs:
            path = item["path"]
            config = item["config"]
            metadata = _as_dict(config.get("metadata"))
            taskset_path = _taskset_path(config, root)
            task_count = 0
            taskset_ok = False
            task_ids: list[str] = []
            if taskset_path.exists():
                taskset = _load_json(taskset_path)
                task_ids = [str(value) for value in taskset.get("task_ids", [])]
                task_count = len(task_ids)
                taskset_ok = task_count == expected_count
            if not taskset_ok:
                failures.append("taskset")
            max_output_tokens = int(metadata.get("max_output_tokens") or 0)
            config_checks.append({
                "config": _repo_relative(path, root),
                "tier": tier,
                "agent": str(_as_dict(config.get("agent")).get("name") or ""),
                "taskset": _repo_relative(taskset_path, root),
                "task_count": task_count,
                "taskset_ok": taskset_ok,
                "max_output_tokens": max_output_tokens,
                "task_ids": task_ids,
            })

    token_window = int(os.environ.get("PODMAN_SWE_MODEL_MAX_LEN", "200000") or "200000")
    token_failures = [
        check for check in config_checks
        if int(check.get("max_output_tokens") or 0) <= 0
        or int(check.get("max_output_tokens") or 0) >= token_window
    ]
    if token_failures:
        failures.append("token_window")

    podman_version = ""
    podman_info_ok = False
    try:
        version = _run(["podman", "--version"], timeout=15)
        podman_version = (version.stdout or version.stderr).strip()
        info = _run(["podman", "info"], timeout=30)
        podman_info_ok = info.returncode == 0
        if version.returncode != 0 or not podman_info_ok:
            failures.append("podman_runtime")
    except (OSError, subprocess.TimeoutExpired):
        failures.append("podman_runtime")

    socket_path = os.environ.get("ALPHADIANA_PODMAN_SOCKET", "").strip() or default_podman_socket_path()
    socket_exists = Path(socket_path).exists()
    if not socket_exists:
        failures.append("podman_socket")

    docker_probe: dict[str, Any]
    try:
        import docker
        api_version = os.environ.get("ALPHADIANA_PODMAN_DOCKER_API_VERSION", "").strip()
        with _patched_env(podman_socket_env(socket_path)):
            client = docker.from_env(version=api_version) if api_version else docker.from_env()
            docker_probe = {
                "ok": bool(client.ping()),
                "version": client.version(),
                "api_version": api_version,
            }
    except Exception as exc:
        docker_probe = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        failures.append("docker_api_version")

    swebench_probe: dict[str, Any]
    try:
        import swebench
        swebench_probe = {"ok": True, "version": str(getattr(swebench, "__version__", ""))}
    except Exception as exc:
        swebench_probe = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        failures.append("swebench_import")

    selected_ids = [
        task_id
        for check in config_checks
        if check["tier"] == "smoke"
        for task_id in check["task_ids"]
    ]
    first_instance_id = selected_ids[0] if selected_ids else "astropy__astropy-12907"
    dataset_probe: dict[str, Any]
    image_probe: dict[str, Any]
    try:
        dataset = load_dataset_with_retry("SWE-bench/SWE-bench_Verified", None, split="test")
        ids = {str(row.get("instance_id") or "") for row in dataset}
        dataset_probe = {
            "ok": all(task_id in ids for task_id in set(selected_ids)),
            "dataset": "SWE-bench/SWE-bench_Verified",
            "split": "test",
            "selected_task_count": len(set(selected_ids)),
        }
        if not dataset_probe["ok"]:
            failures.append("hf_dataset_access")
        image_probe = _image_qualification_probe("SWE-bench/SWE-bench_Verified", "test", first_instance_id)
        if not image_probe.get("ok"):
            failures.append("podman_short_name_image")
    except Exception as exc:
        dataset_probe = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        image_probe = {"ok": False, "error_type": "dataset_unavailable"}
        failures.append("hf_dataset_access")

    host_base_url = os.environ.get("OPENAI_BASE_URL", "").strip()
    container_base_url = os.environ.get("PODMAN_SWE_CONTAINER_OPENAI_BASE_URL", "").strip()
    if not container_base_url:
        container_base_url = derive_container_provider_url(host_base_url)
    host_provider_probe = _probe_host_provider(
        host_base_url,
        os.environ.get("OPENAI_API_KEY", ""),
        provider_timeout,
    ) if host_base_url else {"ok": False, "error_type": "missing_base_url"}
    if not host_provider_probe.get("ok"):
        failures.append("provider_failure")

    podman_provider_probe = _probe_provider_from_podman(
        image=podman_provider_image,
        base_url=container_base_url,
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        model_name=os.environ.get("OPENAI_MODEL_NAME", ""),
        network=podman_provider_network,
        timeout=provider_timeout,
    ) if podman_version else {"ok": False, "error_type": "podman_unavailable"}
    if not podman_provider_probe.get("ok"):
        failures.append("provider_unreachable_from_podman")

    vllm_log_raw = os.environ.get("ALPHADIANA_VLLM_LOG", "").strip()
    vllm_log_exists = bool(vllm_log_raw and Path(vllm_log_raw).exists())
    if vllm_log_raw and not vllm_log_exists:
        failures.append("vllm_log_missing")

    unique_failures = sorted(set(failures))
    result = {
        "ok": not unique_failures,
        "failures": unique_failures,
        "config_checks": config_checks,
        "podman": {
            "version": podman_version,
            "info_ok": podman_info_ok,
            "socket_exists": socket_exists,
            "socket_env_present": bool(os.environ.get("ALPHADIANA_PODMAN_SOCKET")),
        },
        "docker_py": docker_probe,
        "swebench": swebench_probe,
        "dataset": dataset_probe,
        "image_qualification": image_probe,
        "provider": {
            "host_base_url": _sanitize_url(host_base_url),
            "container_base_url": _sanitize_url(container_base_url),
            "host_probe": host_provider_probe,
            "podman_probe": podman_provider_probe,
        },
        "model": {
            "name": os.environ.get("OPENAI_MODEL_NAME", ""),
            "max_len": token_window,
        },
        "vllm_log": {
            "configured": bool(vllm_log_raw),
            "exists": vllm_log_exists,
            "basename": Path(vllm_log_raw).name if vllm_log_raw else "",
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--provider-timeout", type=int, default=60)
    parser.add_argument(
        "--podman-provider-image",
        default=os.environ.get("PODMAN_SWE_PREFLIGHT_PROVIDER_IMAGE", "docker.io/library/python:3.12-slim"),
    )
    parser.add_argument(
        "--podman-provider-network",
        default=os.environ.get("PODMAN_SWE_PREFLIGHT_PROVIDER_NETWORK", ""),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = preflight(
        config_dir=args.config_dir,
        output_path=args.output,
        root=args.root,
        provider_timeout=args.provider_timeout,
        podman_provider_image=args.podman_provider_image,
        podman_provider_network=args.podman_provider_network,
    )
    print(json.dumps({"ok": result["ok"], "failures": result["failures"]}, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
