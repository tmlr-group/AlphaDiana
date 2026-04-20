"""SWE-bench Pro scorer backed by the official evaluator script."""

from __future__ import annotations

import ast
import json
import logging
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer

logger = logging.getLogger(__name__)

_PATCH_PREFIX = "alphadiana"


def _normalize_string_list(value: Any) -> list[str]:
    """Normalize task metadata into a stable list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, (list, tuple, set)):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in stripped.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _python_list_literal(value: Any) -> str:
    """Serialize a metadata list the way the upstream evaluator expects."""
    return repr(_normalize_string_list(value))


def _load_json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@register_scorer("swebench_pro")
class SWEBenchProScorer(Scorer):
    """Scorer that wraps the official SWE-bench Pro evaluator."""

    def __init__(self) -> None:
        self._eval_script_path: Path | None = None
        self._scripts_dir: Path | None = None
        self._dockerhub_username: str = "jefzda"
        self._python_bin: str = sys.executable
        self._use_local_docker: bool = True
        self._docker_platform: str = ""
        self._block_network: bool = False
        self._num_workers: int = 1
        self._redo: bool = True

    @property
    def name(self) -> str:
        return "swebench_pro"

    def setup(self, config: dict) -> None:
        self._eval_script_path = self._require_existing_path(
            "eval_script_path", config.get("eval_script_path", ""), expect_dir=False
        )
        self._scripts_dir = self._require_existing_path(
            "scripts_dir", config.get("scripts_dir", ""), expect_dir=True
        )
        self._dockerhub_username = (
            str(config.get("dockerhub_username", "jefzda")).strip() or "jefzda"
        )
        self._python_bin = str(config.get("python_bin", sys.executable)).strip() or sys.executable
        self._use_local_docker = bool(config.get("use_local_docker", True))
        self._docker_platform = str(config.get("docker_platform", "")).strip()
        self._block_network = bool(config.get("block_network", False))
        self._num_workers = max(1, int(config.get("num_workers", 1)))
        self._redo = bool(config.get("redo", True))

    def score(self, task, response) -> ScoreResult:
        patch = ""
        if response.answer is not None:
            patch = str(response.answer).strip()
        if not patch:
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(task.ground_truth),
                predicted="unresolved",
                rationale="Empty patch produced; skipping SWE-bench evaluation.",
                metadata={"resolved": False},
            )

        eval_root = self._eval_root()
        self._validate_eval_assets(eval_root)

        with tempfile.TemporaryDirectory(prefix="swebench_eval_") as temp_dir:
            workspace_dir = Path(temp_dir)
            raw_sample_path = workspace_dir / "raw_sample.jsonl"
            patch_path = workspace_dir / "patches.json"
            output_dir = workspace_dir / "output"
            output_dir.mkdir(parents=True, exist_ok=True)

            self._write_raw_sample(task, raw_sample_path)
            self._write_patches(task, patch, patch_path)
            argv = self._build_eval_argv(raw_sample_path, patch_path, output_dir)
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=False,
                cwd=eval_root,
            )

            response.metadata["swebench_eval_argv"] = argv
            response.metadata["swebench_eval_stdout"] = proc.stdout
            response.metadata["swebench_eval_stderr"] = proc.stderr
            response.metadata["swebench_eval_returncode"] = proc.returncode
            response.metadata["swebench_eval_cwd"] = str(eval_root)
            response.metadata["swebench_eval_output_dir"] = str(output_dir)
            response.metadata["swebench_eval_raw_sample_path"] = str(raw_sample_path)
            response.metadata["swebench_eval_patch_path"] = str(patch_path)

            if self._looks_like_missing_asset_failure(proc):
                raise RuntimeError(
                    "Official SWE-bench evaluator assets are missing or were resolved "
                    "from the wrong working directory. "
                    "Check swebench_eval_stdout / swebench_eval_stderr."
                )

            eval_results = self._load_eval_results(output_dir)
            output_payload = self._load_instance_output(output_dir, task.task_id)
            self._persist_workspace_artifacts(
                response=response,
                task_id=task.task_id,
                output_dir=output_dir,
                output_payload=output_payload,
                process=proc,
                eval_results=eval_results,
            )
            score_metadata = self._build_score_metadata(task, output_payload, eval_results)
            resolved = bool(score_metadata["resolved"])
            if proc.returncode != 0:
                score_metadata["resolved"] = False
                return ScoreResult(
                    correct=False,
                    score=0.0,
                    expected=str(task.ground_truth),
                    predicted=f"evaluator_returncode={proc.returncode}",
                    rationale=(
                        "Official SWE-bench evaluator failed. "
                        "Check swebench_eval_stdout and swebench_eval_stderr in response metadata."
                    ),
                    metadata=score_metadata,
                )

            return ScoreResult(
                correct=resolved,
                score=1.0 if resolved else 0.0,
                expected=str(task.ground_truth),
                predicted="resolved" if resolved else "unresolved",
                rationale=self._build_rationale(score_metadata),
                metadata=score_metadata,
            )

    def _eval_root(self) -> Path:
        assert self._eval_script_path is not None
        return self._eval_script_path.parent

    def _validate_eval_assets(self, eval_root: Path) -> None:
        expected_dir = eval_root / "dockerfiles" / "base_dockerfile"
        if expected_dir.exists():
            return
        raise RuntimeError(
            "SWE-bench evaluator assets missing: expected "
            f"{expected_dir}. Use the official SWE-bench Pro repo root for "
            "SWE_BENCH_PRO_EVAL_SCRIPT and run the scorer from that checkout."
        )

    @staticmethod
    def _looks_like_missing_asset_failure(process: subprocess.CompletedProcess[str]) -> bool:
        combined = "\n".join(
            part for part in (process.stdout, process.stderr) if part
        ).lower()
        return (
            "dockerfiles/base_dockerfile" in combined
            and "no such file or directory" in combined
        )

    def _require_existing_path(self, key: str, value: Any, *, expect_dir: bool) -> Path:
        raw = str(value).strip()
        if not raw:
            raise RuntimeError(f"swebench_pro scorer requires non-empty {key}")
        path = Path(raw).expanduser()
        if not path.exists():
            raise RuntimeError(f"swebench_pro scorer {key} does not exist: {raw}")
        if expect_dir and not path.is_dir():
            raise RuntimeError(f"swebench_pro scorer {key} must be a directory: {raw}")
        if not expect_dir and not path.is_file():
            raise RuntimeError(f"swebench_pro scorer {key} must be a file: {raw}")
        return path.resolve()

    def _build_raw_sample(self, task) -> dict[str, Any]:
        metadata = dict(getattr(task, "metadata", {}) or {})
        ground_truth = dict(getattr(task, "ground_truth", {}) or {})
        return {
            "instance_id": task.task_id,
            "repo": str(metadata.get("repo", "")).strip(),
            "before_repo_set_cmd": str(metadata.get("before_repo_set_cmd", "")).strip(),
            "selected_test_files_to_run": _python_list_literal(
                metadata.get("selected_test_files_to_run", [])
            ),
            "base_commit": str(
                ground_truth.get("base_commit", metadata.get("base_commit", ""))
            ).strip(),
            "fail_to_pass": _python_list_literal(metadata.get("fail_to_pass", [])),
            "pass_to_pass": _python_list_literal(metadata.get("pass_to_pass", [])),
        }

    def _write_raw_sample(self, task, raw_sample_path: Path) -> None:
        raw_sample = self._build_raw_sample(task)
        raw_sample_path.write_text(json.dumps(raw_sample) + "\n", encoding="utf-8")

    def _write_patches(self, task, patch: str, patch_path: Path) -> None:
        payload = [
            {
                "instance_id": task.task_id,
                "patch": patch,
                "prefix": _PATCH_PREFIX,
            }
        ]
        patch_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _build_eval_argv(
        self,
        raw_sample_path: Path,
        patch_path: Path,
        output_dir: Path,
    ) -> list[str]:
        assert self._eval_script_path is not None
        assert self._scripts_dir is not None

        argv = [
            self._python_bin,
            str(self._eval_script_path),
            "--raw_sample_path",
            str(raw_sample_path),
            "--patch_path",
            str(patch_path),
            "--output_dir",
            str(output_dir),
            "--scripts_dir",
            str(self._scripts_dir),
            "--dockerhub_username",
            self._dockerhub_username,
            "--num_workers",
            str(self._num_workers),
        ]
        if self._use_local_docker:
            argv.append("--use_local_docker")
        if self._docker_platform:
            argv.extend(["--docker_platform", self._docker_platform])
        if self._block_network:
            argv.append("--block_network")
        if self._redo:
            argv.append("--redo")
        return argv

    def _load_eval_results(self, output_dir: Path) -> dict[str, bool]:
        eval_results_path = output_dir / "eval_results.json"
        if not eval_results_path.exists():
            logger.warning("SWE-bench evaluator did not create %s", eval_results_path)
            return {}
        try:
            data = _load_json_file(eval_results_path)
        except json.JSONDecodeError:
            logger.warning("Failed to decode %s", eval_results_path)
            return {}
        return data if isinstance(data, dict) else {}

    def _load_instance_output(self, output_dir: Path, task_id: str) -> dict[str, Any]:
        output_path = output_dir / task_id / f"{_PATCH_PREFIX}_output.json"
        if not output_path.exists():
            logger.warning("SWE-bench evaluator did not create %s", output_path)
            return {}
        try:
            data = _load_json_file(output_path)
        except json.JSONDecodeError:
            logger.warning("Failed to decode %s", output_path)
            return {}
        return data if isinstance(data, dict) else {}

    def _persist_workspace_artifacts(
        self,
        *,
        response,
        task_id: str,
        output_dir: Path,
        output_payload: dict[str, Any],
        process: subprocess.CompletedProcess[str],
        eval_results: dict[str, bool],
    ) -> None:
        task_dir = output_dir / task_id
        artifact_paths = {
            f"/swebench_eval/{task_id}/alphadiana_output.json": task_dir / "alphadiana_output.json",
            f"/swebench_eval/{task_id}/alphadiana_stdout.log": task_dir / "alphadiana_stdout.log",
            f"/swebench_eval/{task_id}/alphadiana_stderr.log": task_dir / "alphadiana_stderr.log",
            f"/swebench_eval/{task_id}/alphadiana_entryscript.sh": task_dir / "alphadiana_entryscript.sh",
            "/swebench_eval/eval_results.json": output_dir / "eval_results.json",
        }
        for remote_path, path in artifact_paths.items():
            if path.exists():
                response.workspace_file_contents[remote_path] = path.read_text(encoding="utf-8")

        if (
            f"/swebench_eval/{task_id}/alphadiana_output.json"
            not in response.workspace_file_contents
            and output_payload
        ):
            response.workspace_file_contents[
                f"/swebench_eval/{task_id}/alphadiana_output.json"
            ] = json.dumps(output_payload, indent=2)
        if f"/swebench_eval/{task_id}/alphadiana_stdout.log" not in response.workspace_file_contents:
            response.workspace_file_contents[
                f"/swebench_eval/{task_id}/alphadiana_stdout.log"
            ] = process.stdout
        if f"/swebench_eval/{task_id}/alphadiana_stderr.log" not in response.workspace_file_contents:
            response.workspace_file_contents[
                f"/swebench_eval/{task_id}/alphadiana_stderr.log"
            ] = process.stderr
        if "/swebench_eval/eval_results.json" not in response.workspace_file_contents and eval_results:
            response.workspace_file_contents["/swebench_eval/eval_results.json"] = json.dumps(
                eval_results, indent=2
            )

    def _build_score_metadata(
        self,
        task,
        output_payload: dict[str, Any],
        eval_results: dict[str, bool],
    ) -> dict[str, Any]:
        metadata = dict(getattr(task, "metadata", {}) or {})
        fail_to_pass_expected = sorted(_normalize_string_list(metadata.get("fail_to_pass", [])))
        pass_to_pass_expected = sorted(_normalize_string_list(metadata.get("pass_to_pass", [])))
        selected_test_files_to_run = sorted(
            _normalize_string_list(metadata.get("selected_test_files_to_run", []))
        )
        passed_tests = sorted(self._extract_passed_tests(output_payload))
        passed_test_set = set(passed_tests)
        fail_to_pass_passed = sorted(test for test in fail_to_pass_expected if test in passed_test_set)
        pass_to_pass_passed = sorted(test for test in pass_to_pass_expected if test in passed_test_set)
        fail_to_pass_missing = sorted(
            test for test in fail_to_pass_expected if test not in passed_test_set
        )
        pass_to_pass_missing = sorted(
            test for test in pass_to_pass_expected if test not in passed_test_set
        )
        resolved = not fail_to_pass_missing and not pass_to_pass_missing

        return {
            "fail_to_pass_expected": fail_to_pass_expected,
            "fail_to_pass_passed": fail_to_pass_passed,
            "fail_to_pass_missing": fail_to_pass_missing,
            "pass_to_pass_expected": pass_to_pass_expected,
            "pass_to_pass_passed": pass_to_pass_passed,
            "pass_to_pass_missing": pass_to_pass_missing,
            "selected_test_files_to_run": selected_test_files_to_run,
            "passed_tests": passed_tests,
            "resolved": resolved,
            "upstream_eval_result": bool(eval_results.get(task.task_id, resolved)),
        }

    def _extract_passed_tests(self, output_payload: dict[str, Any]) -> set[str]:
        passed_tests: set[str] = set()
        tests = output_payload.get("tests", [])
        if not isinstance(tests, Iterable) or isinstance(tests, (str, bytes, dict)):
            return passed_tests
        for entry in tests:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("status", "")).upper() != "PASSED":
                continue
            name = str(entry.get("name") or entry.get("test_name") or "").strip()
            if name:
                passed_tests.add(name)
        return passed_tests

    def _build_rationale(self, score_metadata: dict[str, Any]) -> str:
        return (
            "FAIL_TO_PASS "
            f"{len(score_metadata['fail_to_pass_passed'])}/{len(score_metadata['fail_to_pass_expected'])} "
            "passed; PASS_TO_PASS "
            f"{len(score_metadata['pass_to_pass_passed'])}/{len(score_metadata['pass_to_pass_expected'])} "
            f"passed. Resolved={score_metadata['resolved']}."
        )
