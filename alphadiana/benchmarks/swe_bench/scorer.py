"""SWE-bench scorer backed by the official swebench harness."""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from alphadiana.engine.container_runtime.podman_socket import (
    podman_socket_env,
    resolve_podman_docker_api_version,
)
from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer
from alphadiana.utils.swebench import (
    build_swebench_instance,
    ensure_swebench_build_network_mode,
    harden_test_spec_repo_clone,
    infer_instance_id,
    qualify_swebench_test_spec_for_podman,
)

logger = logging.getLogger(__name__)


def _load_swebench_runtime() -> dict[str, Any]:
    try:
        import docker
    except ImportError as exc:
        raise RuntimeError(
            "The 'docker' package is required for the swe_bench scorer. "
            "Install the SWE-bench optional dependencies with: pip install -e '.[swebench]'"
        ) from exc

    try:
        import swebench.harness.run_evaluation as run_evaluation_module
        from swebench.harness.constants import (
            APPLY_PATCH_FAIL,
            KEY_INSTANCE_ID,
            KEY_MODEL,
            KEY_PREDICTION,
            LOG_INSTANCE,
            LOG_REPORT,
            LOG_TEST_OUTPUT,
            RESET_FAILED,
            TESTS_ERROR,
            TESTS_TIMEOUT,
        )
        from swebench.harness.docker_build import build_env_images, build_instance_image
        from swebench.harness.run_evaluation import run_instance
        from swebench.harness.test_spec.test_spec import make_test_spec
    except ImportError as exc:
        raise RuntimeError(
            "The 'swebench' package is required for the swe_bench scorer. "
            "Install the SWE-bench optional dependencies with: pip install -e '.[swebench]'"
        ) from exc

    return {
        "docker": docker,
        "run_evaluation_module": run_evaluation_module,
        "build_env_images": build_env_images,
        "build_instance_image": build_instance_image,
        "make_test_spec": make_test_spec,
        "run_instance": run_instance,
        "KEY_INSTANCE_ID": KEY_INSTANCE_ID,
        "KEY_MODEL": KEY_MODEL,
        "KEY_PREDICTION": KEY_PREDICTION,
        "LOG_REPORT": LOG_REPORT,
        "LOG_INSTANCE": LOG_INSTANCE,
        "LOG_TEST_OUTPUT": LOG_TEST_OUTPUT,
        "APPLY_PATCH_FAIL": APPLY_PATCH_FAIL,
        "RESET_FAILED": RESET_FAILED,
        "TESTS_ERROR": TESTS_ERROR,
        "TESTS_TIMEOUT": TESTS_TIMEOUT,
    }


def _read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


@register_scorer("swe_bench")
class SWEBenchScorer(Scorer):
    """Score SWE-bench patches with the official swebench harness."""

    def __init__(self) -> None:
        self._timeout = 1800
        self._namespace = "swebench"
        self._force_rebuild = False
        self._rm_image = False
        self._arch = "x86_64"
        self._env_image_tag = "latest"
        self._instance_image_tag = "latest"
        self._run_id_prefix = "alphadiana-swebench"
        self._log_dir = Path("./swe_bench_logs")
        self._git_clone_retries = 3
        self._git_clone_retry_sleep_sec = 5
        self._git_clone_filter = ""
        self._docker_build_network = "host"
        self._container_engine = "docker"
        self._podman_socket_path = ""
        self._docker_api_version = ""
        self._runtime: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return "swe_bench"

    def setup(self, config: dict) -> None:
        self._timeout = int(config.get("timeout", self._timeout))
        self._namespace = str(config.get("namespace", self._namespace)).strip() or self._namespace
        self._force_rebuild = bool(config.get("force_rebuild", self._force_rebuild))
        self._rm_image = bool(config.get("rm_image", self._rm_image))
        self._arch = str(config.get("arch", self._arch)).strip() or self._arch
        self._env_image_tag = (
            str(config.get("env_image_tag", self._env_image_tag)).strip() or self._env_image_tag
        )
        self._instance_image_tag = (
            str(config.get("instance_image_tag", self._instance_image_tag)).strip()
            or self._instance_image_tag
        )
        self._run_id_prefix = (
            str(config.get("run_id_prefix", self._run_id_prefix)).strip() or self._run_id_prefix
        )
        self._log_dir = Path(config.get("log_dir", str(self._log_dir)))
        self._git_clone_retries = int(config.get("git_clone_retries", self._git_clone_retries))
        self._git_clone_retry_sleep_sec = int(
            config.get("git_clone_retry_sleep_sec", self._git_clone_retry_sleep_sec)
        )
        self._docker_build_network = (
            str(config.get("docker_build_network", self._docker_build_network)).strip()
            or self._docker_build_network
        )
        self._container_engine = str(
            config.get("container_engine", self._container_engine) or self._container_engine
        ).strip().lower()
        if self._container_engine not in {"docker", "podman"}:
            raise ValueError("swe_bench scorer.config.container_engine must be one of docker, podman")
        if self._container_engine == "podman":
            self._git_clone_retries = int(config.get("git_clone_retries", 6))
            self._git_clone_retry_sleep_sec = int(config.get("git_clone_retry_sleep_sec", 10))
            self._git_clone_filter = str(config.get("git_clone_filter", "blob:none") or "").strip()
        else:
            self._git_clone_filter = str(config.get("git_clone_filter", self._git_clone_filter) or "").strip()
        self._podman_socket_path = str(config.get("podman_socket", "") or "").strip()
        self._docker_api_version = str(
            resolve_podman_docker_api_version(config.get("docker_api_version"))
            if self._container_engine == "podman"
            else (config.get("docker_api_version") or "")
        ).strip()

    def _runtime_api(self) -> dict[str, Any]:
        if self._runtime is None:
            self._runtime = _load_swebench_runtime()
        ensure_swebench_build_network_mode(
            self._docker_build_network,
            podman_mode=(self._container_engine == "podman"),
        )
        return self._runtime

    def _make_run_id(self, instance_id: str) -> str:
        suffix = uuid.uuid4().hex[:8]
        return f"{self._run_id_prefix}-{instance_id}-{suffix}"

    def _model_name_or_path(self, response) -> str:
        model_name = str(response.metadata.get("agent_model", "")).strip()
        if model_name:
            return model_name
        runtime_name = str(response.metadata.get("runtime", "")).strip()
        if runtime_name:
            return f"alphadiana/{runtime_name}"
        return "alphadiana/swe_bench"

    def _set_eval_log_dir(self, runtime: dict[str, Any]) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        runtime["run_evaluation_module"].RUN_EVALUATION_LOG_DIR = self._log_dir

    @contextmanager
    def _container_runtime_env(self):
        if self._container_engine != "podman":
            yield
            return

        env = podman_socket_env(self._podman_socket_path or None)
        env["ALPHADIANA_SWEBENCH_PODMAN_BUILD"] = "1"
        if self._docker_api_version:
            env["DOCKER_API_VERSION"] = self._docker_api_version
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

    def _docker_client(self, docker_module: Any) -> Any:
        if self._container_engine == "podman" and self._docker_api_version:
            return docker_module.from_env(version=self._docker_api_version)
        return docker_module.from_env()

    def _log_dir_for(self, runtime: dict[str, Any], run_id: str, model_name: str, instance_id: str) -> Path:
        return (
            Path(runtime["run_evaluation_module"].RUN_EVALUATION_LOG_DIR)
            / run_id
            / model_name.replace("/", "__")
            / instance_id
        )

    def _attach_eval_artifacts(self, response, instance_id: str, log_dir: Path) -> None:
        files_to_collect = [
            runtime_name
            for runtime_name in ("report.json", "run_instance.log", "test_output.txt", "patch.diff", "eval.sh")
        ]
        for filename in files_to_collect:
            file_path = log_dir / filename
            content = _read_text_if_exists(file_path)
            if not content:
                continue
            artifact_path = f"/swebench_eval/{instance_id}/{filename}"
            response.workspace_file_contents[artifact_path] = content
            if artifact_path not in response.workspace_snapshot_paths:
                response.workspace_snapshot_paths.append(artifact_path)

    def _score_from_log(self, runtime: dict[str, Any], log_text: str, *, expected: str) -> ScoreResult | None:
        markers = {
            runtime["APPLY_PATCH_FAIL"]: "Patch did not apply in the official SWE-bench harness.",
            runtime["TESTS_TIMEOUT"]: "Official SWE-bench evaluation timed out.",
            runtime["TESTS_ERROR"]: "Official SWE-bench evaluation ended with test errors.",
            runtime["RESET_FAILED"]: "Official SWE-bench evaluation failed while resetting tests.",
        }
        for marker, rationale in markers.items():
            if marker in log_text:
                return ScoreResult(
                    correct=False,
                    score=0.0,
                    expected=expected,
                    predicted="unresolved",
                    rationale=rationale,
                    metadata={"resolved": False, "status_marker": marker},
                )
        return None

    def score(self, task, response) -> ScoreResult:
        patch = ""
        if response.answer is not None:
            patch = str(response.answer).strip()
        expected = str(task.ground_truth)
        instance_id = infer_instance_id(task)
        if not patch:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=expected,
                predicted="unresolved",
                rationale="Empty patch produced; skipping SWE-bench evaluation.",
                metadata={"resolved": False, "instance_id": instance_id},
            )

        runtime = self._runtime_api()
        self._set_eval_log_dir(runtime)

        instance = build_swebench_instance(task)
        if not instance.get("repo") or not instance.get("base_commit"):
            raise RuntimeError(
                "SWE-bench task metadata is incomplete; both 'repo' and 'base_commit' are required"
            )

        test_spec = runtime["make_test_spec"](
            instance,
            namespace=self._namespace,
            env_image_tag=self._env_image_tag,
            instance_image_tag=self._instance_image_tag,
            arch=self._arch,
        )
        harden_test_spec_repo_clone(
            test_spec,
            clone_retries=self._git_clone_retries,
            retry_sleep_sec=self._git_clone_retry_sleep_sec,
            clone_filter=self._git_clone_filter,
            fallback_base_commit=(
                instance.get("base_commit") if self._container_engine == "podman" else None
            ),
        )
        if self._container_engine == "podman":
            qualify_swebench_test_spec_for_podman(test_spec)
        with self._container_runtime_env():
            client = self._docker_client(runtime["docker"])
            _, failed = runtime["build_env_images"](
                client,
                [test_spec] if self._container_engine == "podman" else [instance],
                force_rebuild=self._force_rebuild,
                max_workers=1,
                namespace=self._namespace,
                instance_image_tag=self._instance_image_tag,
                env_image_tag=self._env_image_tag,
            )
            if failed:
                raise RuntimeError(f"Failed to build SWE-bench environment images: {failed}")
            runtime["build_instance_image"](
                test_spec,
                client,
                None,
                nocache=self._force_rebuild,
            )

        model_name = self._model_name_or_path(response)
        prediction = {
            runtime["KEY_INSTANCE_ID"]: instance_id,
            runtime["KEY_MODEL"]: model_name,
            runtime["KEY_PREDICTION"]: patch,
        }
        run_id = self._make_run_id(instance_id)
        with self._container_runtime_env():
            run_result = runtime["run_instance"](
                test_spec,
                prediction,
                self._rm_image,
                self._force_rebuild,
                client,
                run_id=run_id,
                timeout=self._timeout,
                rewrite_reports=False,
            )

        log_dir = self._log_dir_for(runtime, run_id, model_name, instance_id)
        report_path = log_dir / runtime["LOG_REPORT"]
        log_path = log_dir / runtime["LOG_INSTANCE"]
        report = _read_json_if_exists(report_path)
        report_entry = report.get(instance_id, {}) if report else {}
        log_text = _read_text_if_exists(log_path)

        self._attach_eval_artifacts(response, instance_id, log_dir)
        response.metadata["swe_bench_eval"] = {
            "run_id": run_id,
            "log_dir": str(log_dir),
            "completed": bool(run_result.get("completed", False)),
            "resolved": bool(report_entry.get("resolved", False)),
            "model_name_or_path": model_name,
            "container_engine": self._container_engine,
            "docker_api_version": self._docker_api_version,
            "podman_socket": podman_socket_env(self._podman_socket_path or None)["DOCKER_HOST"]
            if self._container_engine == "podman"
            else "",
        }

        if report_entry:
            resolved = bool(report_entry.get("resolved", False))
            return ScoreResult(
                correct=resolved,
                score=1.0 if resolved else 0.0,
                expected=expected,
                predicted="resolved" if resolved else "unresolved",
                rationale=(
                    "Official SWE-bench evaluation resolved the instance."
                    if resolved
                    else "Official SWE-bench evaluation completed but did not resolve the instance."
                ),
                metadata={
                    "resolved": resolved,
                    "instance_id": instance_id,
                    "run_id": run_id,
                    "log_dir": str(log_dir),
                    "completed": bool(run_result.get("completed", False)),
                },
            )

        log_result = self._score_from_log(runtime, log_text, expected=expected)
        if log_result is not None:
            log_result.metadata.update({
                "instance_id": instance_id,
                "run_id": run_id,
                "log_dir": str(log_dir),
                "completed": bool(run_result.get("completed", False)),
            })
            return log_result

        raise RuntimeError(
            "SWE-bench evaluation did not produce a report. "
            f"Check {log_dir} for details."
        )
