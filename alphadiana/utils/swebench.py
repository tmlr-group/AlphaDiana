"""Helpers for normalizing SWE-bench task metadata."""

from __future__ import annotations

import json
import shlex
from typing import Any

from alphadiana.benchmark.base import BenchmarkTask


def json_field_to_string(value: Any, *, default: str = "[]") -> str:
    """Return a stable JSON-string representation expected by swebench."""
    if value is None:
        return default
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or default
    return json.dumps(value)


def infer_instance_id(task: BenchmarkTask) -> str:
    """Infer the SWE-bench instance id from task metadata."""
    metadata = getattr(task, "metadata", None) or {}
    raw = metadata.get("instance_id")
    if raw:
        return str(raw)
    if task.task_id.startswith("swe_"):
        return task.task_id[len("swe_") :]
    return task.task_id


def build_swebench_instance(task: BenchmarkTask) -> dict[str, Any]:
    """Reconstruct a SWE-bench instance dict from a benchmark task."""
    metadata = getattr(task, "metadata", None) or {}
    return {
        "instance_id": infer_instance_id(task),
        "repo": str(metadata.get("repo", "")),
        "base_commit": str(metadata.get("base_commit", "")),
        "patch": str(task.ground_truth or ""),
        "test_patch": str(metadata.get("test_patch", "")),
        "problem_statement": task.problem,
        "hints_text": str(metadata.get("hints_text", "")),
        "created_at": str(metadata.get("created_at", "")),
        "version": str(metadata.get("version", "")),
        "FAIL_TO_PASS": json_field_to_string(metadata.get("FAIL_TO_PASS")),
        "PASS_TO_PASS": json_field_to_string(metadata.get("PASS_TO_PASS")),
        "environment_setup_commit": str(metadata.get("environment_setup_commit", "")),
    }


def harden_test_spec_repo_clone(
    test_spec: Any,
    *,
    clone_retries: int = 3,
    retry_sleep_sec: int = 5,
) -> Any:
    """Wrap the generated git-clone step with retry logic for flaky networks."""
    repo_script_list = list(getattr(test_spec, "repo_script_list", []) or [])
    updated: list[str] = []
    replaced = False

    for command in repo_script_list:
        stripped = str(command).strip()
        if not replaced and stripped.startswith("git clone "):
            clone_parts = shlex.split(stripped)
            clone_target = clone_parts[-1] if len(clone_parts) >= 2 else ""
            cleanup_target = shlex.quote(clone_target) if clone_target and not clone_target.startswith("-") else ""
            retry_count = max(1, int(clone_retries))
            sleep_count = max(0, int(retry_sleep_sec))

            updated.extend([
                "git config --global http.version HTTP/1.1",
                "git config --global http.lowSpeedLimit 0",
                "git config --global http.lowSpeedTime 999999",
                "cd /",
                f"for attempt in $(seq 1 {retry_count}); do",
                f"  {'rm -rf ' + cleanup_target if cleanup_target else ':'}",
                f"  git -c http.version=HTTP/1.1 {stripped[4:]} && break",
                "  status=$?",
                f"  echo \"git clone attempt ${{attempt}}/{retry_count} failed with exit ${{status}}; retrying...\" >&2",
                f"  {'rm -rf ' + cleanup_target if cleanup_target else ':'}",
                f"  if [ \"$attempt\" -eq {retry_count} ]; then exit \"$status\"; fi",
                f"  sleep {sleep_count}",
                "done",
            ])
            replaced = True
            continue
        updated.append(str(command))

    if replaced:
        test_spec.repo_script_list = updated
    return test_spec


def ensure_swebench_build_network_mode(network_mode: str | None) -> None:
    """Patch swebench image builds to use an explicit Docker build network."""
    selected = str(network_mode or "").strip()
    if not selected:
        return

    import swebench.harness.docker_build as docker_build

    if getattr(docker_build, "_alphadiana_build_network_mode", None) == selected:
        return

    def build_image(
        image_name: str,
        setup_scripts: dict,
        dockerfile: str,
        platform: str,
        client: Any,
        build_dir: Any,
        nocache: bool = False,
    ) -> None:
        logger = docker_build.setup_logger(image_name, build_dir / "build_image.log")
        logger.info(
            f"Building image {image_name}\n"
            f"Using dockerfile:\n{dockerfile}\n"
            f"Adding ({len(setup_scripts)}) setup scripts to image build repo"
        )

        for setup_script_name, setup_script in setup_scripts.items():
            logger.info(f"[SETUP SCRIPT] {setup_script_name}:\n{setup_script}")
        try:
            for setup_script_name, setup_script in setup_scripts.items():
                setup_script_path = build_dir / setup_script_name
                with open(setup_script_path, "w", encoding="utf-8") as f:
                    f.write(setup_script)
                if setup_script_name not in dockerfile:
                    logger.warning(
                        f"Setup script {setup_script_name} may not be used in Dockerfile"
                    )

            dockerfile_path = build_dir / "Dockerfile"
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(dockerfile)

            logger.info(
                f"Building docker image {image_name} in {build_dir} with platform {platform} "
                f"and network_mode={selected}"
            )
            response = client.api.build(
                path=str(build_dir),
                tag=image_name,
                rm=True,
                forcerm=True,
                decode=True,
                platform=platform,
                nocache=nocache,
                network_mode=selected,
            )

            buildlog = ""
            for chunk in response:
                if "stream" in chunk:
                    chunk_stream = docker_build.ansi_escape(chunk["stream"])
                    logger.info(chunk_stream.strip())
                    buildlog += chunk_stream
                elif "errorDetail" in chunk:
                    logger.error(f"Error: {docker_build.ansi_escape(chunk['errorDetail']['message'])}")
                    raise docker_build.docker.errors.BuildError(
                        chunk["errorDetail"]["message"], buildlog
                    )
            logger.info("Image built successfully!")
        except docker_build.docker.errors.BuildError as exc:
            logger.error(f"docker.errors.BuildError during {image_name}: {exc}")
            raise docker_build.BuildImageError(image_name, str(exc), logger) from exc
        except Exception as exc:
            logger.error(f"Error building image {image_name}: {exc}")
            raise docker_build.BuildImageError(image_name, str(exc), logger) from exc
        finally:
            docker_build.close_logger(logger)

    docker_build.build_image = build_image
    docker_build._alphadiana_build_network_mode = selected
