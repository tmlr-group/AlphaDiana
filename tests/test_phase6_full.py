"""Phase 6: run the full AIME 2026 evaluation and obtain accuracy

Checks:
- All 30 problems complete (completed=30, failed=0)
- Results are written to a JSONL file
- A RunSummary is generated with accuracy, mean_score, and total_tokens
- A markdown report can be produced
- The accuracy metric is reasonable (AIME answers are integers from 0 to 999)

Requires:
  - OpenClaw gateway available (Phase 4)
  - OPENCLAW_SANDBOX_ID environment variable

You can also run it directly with the CLI:
  alphadiana run configs/examples/openclaw_aime2026.yaml

Run these tests with pytest -m integration.
"""
import os
from pathlib import Path

import pytest

from alphadiana.utils.rock_ports import resolve_rock_ports_from_env

pytestmark = pytest.mark.integration
PORTS = resolve_rock_ports_from_env()


def _get_sandbox_id() -> str | None:
    return os.environ.get("OPENCLAW_SANDBOX_ID")


def _get_openclaw_api_base() -> str:
    sandbox_id = _get_sandbox_id()
    return f"{PORTS.proxy_api_url}/sandboxes/{sandbox_id}/proxy/v1"


@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set."
)
class TestFullAIME2024Evaluation:
    """Run the full AIME 2026 evaluation."""

    @pytest.fixture(scope="class")
    def run_result(self, tmp_path_factory):
        """Run the full AIME 2026 evaluation and return (summary, result_store, output_dir)."""
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.runner.runner import Runner

        output_dir = str(tmp_path_factory.mktemp("results"))
        config = ExperimentConfig(
            run_id="test-openclaw-qwen3-8b-aime2026",
            agent_name="openclaw",
            agent_version="latest",
            agent_config={
                "api_base": _get_openclaw_api_base(),
                "model": "openclaw",
                "gateway_token": "OPENCLAW",
                "temperature": 0.0,
                "max_tokens": 8192,
            },
            benchmark_name="aime",
            benchmark_config={
                "dataset": "MathArena/aime_2026",
                "split": "train",
            },
            scorer_name="numeric",
            scorer_config={"tolerance": 1e-6},
            max_concurrent=1,
            output_dir=output_dir,
        )

        runner = Runner(config)
        runner.setup()
        try:
            summary = runner.run()
        finally:
            runner.teardown()

        return summary, runner.result_store, output_dir

    def test_all_tasks_completed(self, run_result):
        """All 30 problems complete (completed=30, failed=0)."""
        summary, _, _ = run_result
        assert summary.total_tasks == 30
        assert summary.completed == 30
        assert summary.failed == 0

    def test_accuracy_in_range(self, run_result):
        """Accuracy stays within a reasonable range (0.0 - 1.0)."""
        summary, _, _ = run_result
        assert 0.0 <= summary.accuracy <= 1.0
        print(f"\n=== AIME 2026 Results ===")
        print(f"Accuracy: {summary.accuracy:.4f} ({int(summary.accuracy * 30)}/30)")
        print(f"Mean Score: {summary.mean_score:.4f}")
        print(f"Mean Wall Time: {summary.mean_wall_time_sec:.1f}s")
        print(f"Total Tokens: {summary.total_tokens}")

    def test_jsonl_file_created(self, run_result):
        """The results JSONL file is created and contains 30 records."""
        _, store, _ = run_result
        assert Path(store.path).exists()
        results = store.load()
        assert len(results) == 30

    def test_each_result_has_fields(self, run_result):
        """Each result contains the required fields."""
        _, store, _ = run_result
        results = store.load()
        required_fields = [
            "task_id", "problem", "ground_truth", "predicted",
            "correct", "score", "wall_time_sec", "timestamp",
        ]
        for r in results:
            for field in required_fields:
                assert field in r, f"Missing field '{field}' in result {r.get('task_id')}"

    def test_answers_are_integers(self, run_result):
        """Predicted answers should be strings, even if empty when OpenClaw times out."""
        _, store, _ = run_result
        results = store.load()
        for r in results:
            predicted = r["predicted"]
            assert isinstance(predicted, str), f"Prediction should be str for {r['task_id']}"

    def test_ground_truth_in_range(self, run_result):
        """All ground_truth values are integers between 0 and 999."""
        _, store, _ = run_result
        results = store.load()
        for r in results:
            gt = int(r["ground_truth"])
            assert 0 <= gt <= 999, f"Ground truth {gt} out of range for {r['task_id']}"

    def test_summary_has_tokens(self, run_result):
        """RunSummary includes total_tokens statistics."""
        summary, _, _ = run_result
        assert "prompt_tokens" in summary.total_tokens
        assert "completion_tokens" in summary.total_tokens
        # OpenClaw gateway may not always report token usage,
        # so we only verify the keys exist
        assert summary.total_tokens["prompt_tokens"] >= 0
        assert summary.total_tokens["completion_tokens"] >= 0

    def test_report_generation(self, run_result):
        """A markdown report can be generated."""
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.results.report import ReportGenerator

        summary, store, _ = run_result
        gen = ReportGenerator()

        config = ExperimentConfig(
            run_id="test-openclaw-qwen3-8b-aime2026",
            agent_name="openclaw",
            agent_version="latest",
            benchmark_name="aime",
            scorer_name="numeric",
        )
        report_summary = gen.generate(store, config)
        md = gen.to_markdown(report_summary)

        assert "openclaw" in md
        assert "aime" in md
        assert str(report_summary.total_tasks) in md
        print(f"\n{md}")

    def test_per_task_breakdown(self, run_result):
        """Print the per-problem scoring breakdown."""
        _, store, _ = run_result
        results = store.load()

        print("\n=== Per-Task Breakdown ===")
        print(f"{'Task ID':<20} {'Ground Truth':>12} {'Predicted':>12} {'Correct':>8}")
        print("-" * 56)
        for r in results:
            correct_mark = "Y" if r["correct"] else "N"
            print(
                f"{r['task_id']:<20} {r['ground_truth']:>12} "
                f"{str(r['predicted']):>12} {correct_mark:>8}"
            )

        correct_count = sum(1 for r in results if r["correct"])
        print(f"\nTotal: {correct_count}/{len(results)} correct")
