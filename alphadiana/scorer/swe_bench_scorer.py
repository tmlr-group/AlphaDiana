"""SWE-bench scorer using the official swebench evaluation harness."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.scorer.registry import register_scorer
from alphadiana.utils.swebench import build_swebench_instance, infer_instance_id

logger = logging.getLogger(__name__)


@register_scorer("swe_bench")
class SWEBenchScorer(Scorer):
    """Scorer that evaluates SWE-bench patches using Docker-based test execution.

    Wraps the official ``swebench`` harness to:
    1. Build a Docker image with the repo at the correct commit.
    2. Apply the model's patch.
    3. Run the test suite.
    4. Check that FAIL_TO_PASS tests now pass and PASS_TO_PASS tests still pass.

    Config keys:
        timeout:       Per-instance test timeout in seconds (default: 1800).
        cache_level:   Docker cache level – "none", "base", "env", or
                       "instance" (default: "env").
        namespace:     Docker image namespace (default: "swebench").
        force_rebuild: Force rebuild Docker images (default: false).
        log_dir:       Directory for evaluation logs (default: "./swe_bench_logs").
        run_id:        Run identifier for log sub-directory (default: "default").
    """

    @property
    def name(self) -> str:
        return "swe_bench"

    def setup(self, config: dict) -> None:
        self.timeout: int = int(config.get("timeout", 1800))
        self.cache_level: str = config.get("cache_level", "env")
        self.namespace: str = config.get("namespace", "swebench")
        self.force_rebuild: bool = bool(config.get("force_rebuild", False))
        self.log_dir: Path = Path(config.get("log_dir", "./swe_bench_logs"))
        self.run_id: str = config.get("run_id", "default")

        # Validate dependencies at setup time.
        try:
            import docker  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "The 'docker' package is required for the swe_bench scorer. "
                "Install with: pip install docker"
            )
        try:
            import swebench  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "The 'swebench' package is required for the swe_bench scorer. "
                "Install with: pip install swebench"
            )

        self.log_dir.mkdir(parents=True, exist_ok=True)

    def score(self, task, response) -> ScoreResult:
        import docker
        from swebench.harness.test_spec.test_spec import make_test_spec
        from swebench.harness.run_evaluation import run_instance

        instance_id = infer_instance_id(task)
        model_patch = str(response.answer) if response.answer is not None else ""

        if not model_patch.strip():
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(task.ground_truth),
                predicted=model_patch,
                rationale="No patch produced (empty answer).",
                metadata={"instance_id": instance_id, "resolved": False},
            )

        # Reconstruct the SWE-bench instance dict from task metadata.
        instance = build_swebench_instance(task)

        # Build the prediction dict expected by run_instance.
        pred = {
            "instance_id": instance_id,
            "model_name_or_path": self.run_id,
            "model_patch": model_patch,
        }

        try:
            test_spec = make_test_spec(instance, namespace=self.namespace)
        except Exception as exc:
            logger.error(
                "Failed to create TestSpec for %s: %s", instance_id, exc
            )
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(task.ground_truth),
                predicted=model_patch,
                rationale=f"TestSpec creation failed: {exc}",
                metadata={"instance_id": instance_id, "error": str(exc)},
            )

        try:
            client = docker.from_env()
            result = run_instance(
                test_spec=test_spec,
                pred=pred,
                rm_image=False,
                force_rebuild=self.force_rebuild,
                client=client,
                run_id=self.run_id,
                timeout=self.timeout,
            )
        except Exception as exc:
            logger.error(
                "run_instance failed for %s: %s", instance_id, exc
            )
            return ScoreResult(
                correct=False,
                score=0.0,
                expected=str(task.ground_truth),
                predicted=model_patch,
                rationale=f"Docker evaluation failed: {exc}",
                metadata={"instance_id": instance_id, "error": str(exc)},
            )

        completed = result.get("completed", False)
        resolved = result.get("resolved", False)

        # Try to read the detailed report from the log directory.
        report_data: dict = {}
        report_path = (
            Path("logs")
            / self.run_id
            / instance_id
            / "report.json"
        )
        if report_path.exists():
            try:
                report_data = json.loads(report_path.read_text())
            except Exception:
                logger.debug("Could not parse report at %s", report_path)

        # Also copy/symlink report to our log_dir for convenience.
        local_report_dir = self.log_dir / instance_id
        local_report_dir.mkdir(parents=True, exist_ok=True)
        local_report_path = local_report_dir / "report.json"
        if report_data:
            try:
                local_report_path.write_text(
                    json.dumps(report_data, indent=2)
                )
            except Exception:
                pass

        rationale_parts = [
            f"completed={completed}",
            f"resolved={resolved}",
        ]
        if not completed:
            rationale_parts.append(
                "Evaluation did not complete (Docker or test error)."
            )

        return ScoreResult(
            correct=resolved,
            score=1.0 if resolved else 0.0,
            expected=str(task.ground_truth),
            predicted=model_patch,
            rationale="; ".join(rationale_parts),
            metadata={
                "instance_id": instance_id,
                "completed": completed,
                "resolved": resolved,
                "report": report_data,
            },
        )
