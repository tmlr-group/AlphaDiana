"""Helpers for normalizing SWE-bench task metadata."""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Any

from alphadiana.benchmark.base import BenchmarkTask


_PODMAN_TEST_SPEC_CLASS_CACHE: dict[type, type] = {}


def _image_has_registry(image_ref: str) -> bool:
    if "/" not in image_ref:
        return False
    first = image_ref.split("/", 1)[0]
    return first == "localhost" or "." in first or ":" in first


def qualify_swebench_podman_image_ref(image_ref: str) -> str:
    """Qualify SWE-bench local image names for Podman's short-name policy.

    The official SWE-bench harness generates local build targets such as
    ``sweb.base...`` and ``sweb.env...``. Docker accepts those short names, but
    Podman can reject them on hosts without search registries configured. Treat
    unqualified SWE-bench build outputs as local images and leave already
    qualified registry references untouched.
    """
    ref = str(image_ref or "").strip()
    if not ref or "://" in ref or _image_has_registry(ref):
        return ref
    return f"localhost/{ref}"


def strip_from_platform_directives_for_podman(dockerfile: str) -> str:
    """Remove FROM-level platform pins that make Podman miss local images."""
    return re.sub(r"(?m)^(FROM)\s+--platform=\S+\s+", r"\1 ", dockerfile)


def qualify_swebench_test_spec_for_podman(test_spec: Any) -> Any:
    """Mutate a SWE-bench TestSpec so local image refs are Podman-qualified."""
    spec_cls = type(test_spec)
    if not getattr(spec_cls, "_alphadiana_podman_qualified", False):
        try:
            qualified_cls = _PODMAN_TEST_SPEC_CLASS_CACHE.get(spec_cls)
            if qualified_cls is None:

                class PodmanQualifiedTestSpec(spec_cls):  # type: ignore[misc, valid-type]
                    _alphadiana_podman_qualified = True

                    @property
                    def base_image_key(self):  # type: ignore[no-untyped-def]
                        return qualify_swebench_podman_image_ref(super().base_image_key)

                    @property
                    def env_image_key(self):  # type: ignore[no-untyped-def]
                        return qualify_swebench_podman_image_ref(super().env_image_key)

                    @property
                    def instance_image_key(self):  # type: ignore[no-untyped-def]
                        return qualify_swebench_podman_image_ref(super().instance_image_key)

                PodmanQualifiedTestSpec.__name__ = f"PodmanQualified{spec_cls.__name__}"
                qualified_cls = PodmanQualifiedTestSpec
                _PODMAN_TEST_SPEC_CLASS_CACHE[spec_cls] = qualified_cls
            test_spec.__class__ = qualified_cls
            return test_spec
        except (TypeError, AttributeError):
            pass

    replacements: dict[str, str] = {}
    for attr in ("base_image_key", "env_image_key", "instance_image_key"):
        current = str(getattr(test_spec, attr, "") or "").strip()
        qualified = qualify_swebench_podman_image_ref(current)
        if current and qualified != current:
            try:
                setattr(test_spec, attr, qualified)
                replacements[current] = qualified
            except AttributeError:
                replacements[current] = qualified

    for attr in ("base_dockerfile", "env_dockerfile", "instance_dockerfile"):
        value = getattr(test_spec, attr, None)
        if not isinstance(value, str) or not value:
            continue
        updated = value
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != value:
            try:
                setattr(test_spec, attr, updated)
            except AttributeError:
                pass
    return test_spec


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
    clone_filter: str | None = None,
    fallback_base_commit: str | None = None,
) -> Any:
    """Wrap the generated git setup step with retry logic for flaky networks."""
    repo_script_list = list(getattr(test_spec, "repo_script_list", []) or [])
    updated: list[str] = []
    replaced = False
    filter_text = str(clone_filter or "").strip()
    fallback_commit = str(fallback_base_commit or "").strip()

    for command in repo_script_list:
        stripped = str(command).strip()
        if fallback_commit:
            if stripped.startswith("TARGET_TIMESTAMP=$(git show -s --format=%ci "):
                quoted_commit = shlex.quote(fallback_commit)
                updated.append(
                    "TARGET_TIMESTAMP=$(git show -s --format=%ci "
                    f"{quoted_commit} 2>/dev/null || date -u '+%Y-%m-%d %H:%M:%S %z')"
                )
                continue
            if stripped.startswith("git tag -l | while read tag; do "):
                updated.append(": # skipped tag timestamp cleanup for Podman partial clone stability")
                continue
            if stripped.startswith("git gc "):
                updated.append(": # skipped git gc for Podman partial clone stability")
                continue
            if (
                stripped.startswith("AFTER_TIMESTAMP=")
                or stripped.startswith("COMMIT_COUNT=")
                or stripped == '[ "$COMMIT_COUNT" -eq 0 ] || exit 1'
            ):
                updated.append(": # skipped commit-count check for Podman partial clone stability")
                continue
        if not replaced and stripped.startswith("git clone "):
            clone_parts = shlex.split(stripped)
            if fallback_commit and "--no-tags" not in clone_parts:
                clone_parts.insert(2, "--no-tags")
            if filter_text and not any(part.startswith("--filter") for part in clone_parts):
                clone_parts.insert(2, f"--filter={filter_text}")
            clone_command = shlex.join(clone_parts)
            clone_target = clone_parts[-1] if len(clone_parts) >= 2 else ""
            clone_remote = clone_parts[-2] if len(clone_parts) >= 3 else ""
            cleanup_target = shlex.quote(clone_target) if clone_target and not clone_target.startswith("-") else ""
            retry_count = max(1, int(clone_retries))
            sleep_count = max(0, int(retry_sleep_sec))
            fetch_filter = f"--filter={filter_text} " if filter_text else ""
            if fallback_commit and clone_remote and cleanup_target:
                quoted_remote = shlex.quote(clone_remote)
                quoted_commit = shlex.quote(fallback_commit)
                updated.extend([
                    "git config --global http.version HTTP/1.1",
                    "git config --global http.lowSpeedLimit 0",
                    "git config --global http.lowSpeedTime 999999",
                    "cd /",
                    f"rm -rf {cleanup_target}",
                    f"mkdir -p {cleanup_target}",
                    f"cd {cleanup_target}",
                    "git init",
                    f"git remote add origin {quoted_remote}",
                    f"for fetch_attempt in $(seq 1 {retry_count}); do",
                    "  git -c http.version=HTTP/1.1 fetch "
                    f"{fetch_filter}--depth=50 --no-tags origin {quoted_commit} && break",
                    "  fetch_status=$?",
                    "  echo \"git fetch attempt "
                    f"${{fetch_attempt}}/{retry_count} failed with exit "
                    "${fetch_status}; retrying...\" >&2",
                    f"  if [ \"$fetch_attempt\" -eq {retry_count} ]; then exit \"$fetch_status\"; fi",
                    f"  sleep {sleep_count}",
                    "done",
                    f"git checkout -f {quoted_commit}",
                    "cd /",
                ])
                replaced = True
                continue

            updated.extend([
                "git config --global http.version HTTP/1.1",
                "git config --global http.lowSpeedLimit 0",
                "git config --global http.lowSpeedTime 999999",
                "cd /",
                f"for attempt in $(seq 1 {retry_count}); do",
                f"  {'rm -rf ' + cleanup_target if cleanup_target else ':'}",
                f"  git -c http.version=HTTP/1.1 {clone_command[4:]} && break",
                "  status=$?",
                f"  echo \"git clone attempt ${{attempt}}/{retry_count} failed with exit ${{status}}; retrying...\" >&2",
                f"  {'rm -rf ' + cleanup_target if cleanup_target else ':'}",
                f"  if [ \"$attempt\" -eq {retry_count} ]; then",
                '    exit "$status"',
                "  fi",
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

            podman_compat = os.environ.get("ALPHADIANA_SWEBENCH_PODMAN_BUILD", "").strip() == "1"
            if podman_compat:
                dockerfile = strip_from_platform_directives_for_podman(dockerfile)
            dockerfile_path = build_dir / "Dockerfile"
            with open(dockerfile_path, "w", encoding="utf-8") as f:
                f.write(dockerfile)

            build_kwargs = {
                "path": str(build_dir),
                "tag": image_name,
                "rm": True,
                "forcerm": True,
                "decode": True,
                "nocache": nocache,
                "network_mode": selected,
            }
            if not podman_compat:
                build_kwargs["platform"] = platform
            logger.info(
                f"Building docker image {image_name} in {build_dir} with platform "
                f"{'omitted for Podman local image compatibility' if podman_compat else platform} "
                f"and network_mode={selected}"
            )
            response = client.api.build(**build_kwargs)

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
