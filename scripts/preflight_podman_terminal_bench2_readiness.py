#!/usr/bin/env python3
"""Preflight the Phase 7 TerminalBench2 Podman readiness pilot."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import yaml

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


REQUIRED_ENV = (
    "TERMINAL_BENCH2_DIR",
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL_NAME",
    "TB2_OPENCODE_RUNTIME_IMAGE",
    "ALPHADIANA_TB2_LOGS_DIR",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _sanitize_url(raw: str) -> str:
    parsed = urlparse(raw)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "", ""))


def _repo_relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _run(cmd: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _podman_image_exists(image: str) -> bool:
    if not image:
        return False
    result = _run(["podman", "image", "exists", image], timeout=30)
    return result.returncode == 0


def _task_image(task_dir: Path) -> str:
    with (task_dir / "task.toml").open("rb") as f:
        data = tomllib.load(f)
    return str(_as_dict(data.get("environment")).get("docker_image") or "").strip()


def _task_ids_from_config(config: dict[str, Any]) -> list[str]:
    metadata = _as_dict(config.get("metadata"))
    configured = metadata.get("selected_task_names")
    if isinstance(configured, list) and configured:
        return [str(value) for value in configured]
    benchmark_config = _as_dict(_as_dict(config.get("benchmark")).get("config"))
    task_ids = benchmark_config.get("task_ids")
    if isinstance(task_ids, list):
        return [str(value).removeprefix("tb2_") for value in task_ids]
    return []


def _probe_provider_from_podman(
    *,
    runtime_image: str,
    network: str,
    timeout: int,
) -> dict[str, Any]:
    base_url = os.environ.get("OPENAI_BASE_URL", "").rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model_name = os.environ.get("OPENAI_MODEL_NAME", "")
    js = r"""
const base = process.env.OPENAI_BASE_URL.replace(/\/+$/, "");
const key = process.env.OPENAI_API_KEY || "";
const model = process.env.OPENAI_MODEL_NAME || "";
const headers = {};
if (key) headers["Authorization"] = `Bearer ${key}`;
fetch(`${base}/models`, { headers })
  .then(async (response) => {
    const body = await response.text();
    const ok = response.ok && body.length > 0;
    console.log(JSON.stringify({
      ok,
      status: response.status,
      response_non_empty: body.length > 0,
      model_name: model,
    }));
    process.exit(ok ? 0 : 2);
  })
  .catch((error) => {
    console.log(JSON.stringify({
      ok: false,
      error_type: error.name || "Error",
      error: String(error.message || error),
      model_name: model,
    }));
    process.exit(2);
  });
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
    cmd.extend([runtime_image, "node", "-e", js])
    try:
        result = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_type": "timeout",
            "error": f"provider probe timed out after {timeout}s",
        }
    stdout = result.stdout.strip()
    detail: dict[str, Any] = {}
    if stdout:
        try:
            detail = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError:
            detail = {"raw_stdout": stdout[-1000:]}
    if not isinstance(detail, dict):
        detail = {}
    detail.setdefault("ok", result.returncode == 0)
    detail["returncode"] = result.returncode
    if result.stderr.strip():
        detail["stderr_tail"] = result.stderr.strip()[-1000:]
    return detail


def preflight(
    *,
    config_path: Path,
    output_path: Path,
    require_local_images: bool,
    provider_network: str,
    provider_timeout: int,
) -> dict[str, Any]:
    config = _load_yaml(config_path)
    task_names = _task_ids_from_config(config)
    tasks_dir = Path(os.environ.get("TERMINAL_BENCH2_DIR", ""))
    runtime_image = os.environ.get("TB2_OPENCODE_RUNTIME_IMAGE", "").strip()
    missing_env = [name for name in REQUIRED_ENV if not os.environ.get(name)]

    failures: list[str] = []
    if missing_env:
        failures.append("missing_required_env")

    podman_version = ""
    podman_info_ok = False
    if not missing_env or "TB2_OPENCODE_RUNTIME_IMAGE" not in missing_env:
        try:
            version = _run(["podman", "--version"], timeout=15)
            podman_version = (version.stdout or version.stderr).strip()
            info = _run(["podman", "info"], timeout=30)
            podman_info_ok = info.returncode == 0
            if version.returncode != 0 or not podman_info_ok:
                failures.append("podman_runtime")
        except (OSError, subprocess.TimeoutExpired):
            failures.append("podman_runtime")
    else:
        failures.append("podman_runtime")

    task_checks: list[dict[str, Any]] = []
    if not tasks_dir.exists() or not tasks_dir.is_dir():
        failures.append("terminal_bench2_dir")
    else:
        for task_name in task_names:
            task_dir = tasks_dir / task_name
            task_failure: list[str] = []
            if not task_dir.is_dir():
                task_failure.append("missing_task_dir")
            if not (task_dir / "task.toml").exists():
                task_failure.append("missing_task_toml")
            if not (task_dir / "instruction.md").exists():
                task_failure.append("missing_instruction")
            docker_image = ""
            image_present = False
            if not task_failure:
                try:
                    docker_image = _task_image(task_dir)
                    image_present = _podman_image_exists(docker_image)
                except Exception as exc:  # pragma: no cover - defensive detail
                    task_failure.append(f"task_toml_error:{exc.__class__.__name__}")
            if task_failure:
                failures.append("task_setup")
            if require_local_images and docker_image and not image_present:
                failures.append("image_pull")
            task_checks.append({
                "task_name": task_name,
                "task_id": f"tb2_{task_name}",
                "task_dir_present": task_dir.is_dir(),
                "task_toml_present": (task_dir / "task.toml").exists(),
                "instruction_present": (task_dir / "instruction.md").exists(),
                "docker_image": docker_image,
                "image_present_locally": image_present,
                "failures": task_failure,
            })

    runtime_image_present = _podman_image_exists(runtime_image) if runtime_image else False
    if not runtime_image_present:
        failures.append("runtime_image_missing")

    provider_probe: dict[str, Any] = {"ok": False, "skipped": True}
    if runtime_image_present and not any(name in missing_env for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL_NAME")):
        provider_probe = _probe_provider_from_podman(
            runtime_image=runtime_image,
            network=provider_network,
            timeout=provider_timeout,
        )
        if not provider_probe.get("ok"):
            failures.append("provider_unreachable_from_podman")

    unique_failures = sorted(set(failures))
    result = {
        "ok": not unique_failures,
        "failure_taxonomy": "clean" if not unique_failures else unique_failures[0],
        "failures": unique_failures,
        "config": _repo_relative(config_path, Path.cwd()),
        "selected_task_ids": [f"tb2_{name}" for name in task_names],
        "selected_task_names": task_names,
        "terminal_bench2_dir_set": bool(os.environ.get("TERMINAL_BENCH2_DIR")),
        "terminal_bench2_dir_exists": tasks_dir.exists() and tasks_dir.is_dir(),
        "podman_version": podman_version,
        "podman_info_ok": podman_info_ok,
        "runtime_image": runtime_image,
        "runtime_image_present": runtime_image_present,
        "task_checks": task_checks,
        "task_images_missing_locally": [
            item["docker_image"]
            for item in task_checks
            if item.get("docker_image") and not item.get("image_present_locally")
        ],
        "task_images_require_local": require_local_images,
        "provider": {
            "base_url": _sanitize_url(os.environ.get("OPENAI_BASE_URL", "")),
            "model_name": os.environ.get("OPENAI_MODEL_NAME", ""),
            "probe_network": provider_network or "podman-default",
            "probe": provider_probe,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--require-local-images", action="store_true")
    parser.add_argument("--provider-network", default=os.environ.get("PODMAN_TB2_PREFLIGHT_NETWORK", ""))
    parser.add_argument(
        "--provider-timeout",
        type=int,
        default=int(os.environ.get("PODMAN_TB2_PREFLIGHT_TIMEOUT_SECONDS", "60")),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = preflight(
        config_path=args.config,
        output_path=args.output,
        require_local_images=args.require_local_images,
        provider_network=args.provider_network,
        provider_timeout=args.provider_timeout,
    )
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
