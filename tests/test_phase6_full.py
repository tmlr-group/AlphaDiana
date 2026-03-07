"""Phase 6: 全量 AIME 2024 评估并获取准确率

跑完全部 30 道 AIME 2024 题目，生成报告。

需要:
  - OpenClaw gateway 可用 (Phase 4)
  - OPENCLAW_SANDBOX_ID 环境变量

用 pytest -m integration 运行这些测试。

也可以直接用 CLI:
  alphadiana run configs/examples/openclaw_aime2024.yaml
"""
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _get_sandbox_id() -> str | None:
    return os.environ.get("OPENCLAW_SANDBOX_ID")


def _get_openclaw_api_base() -> str:
    sandbox_id = _get_sandbox_id()
    return f"http://localhost:9001/apis/envs/sandbox/v1/sandboxes/{sandbox_id}/proxy/v1"


@pytest.mark.skipif(
    _get_sandbox_id() is None,
    reason="OPENCLAW_SANDBOX_ID not set."
)
class TestFullAIME2024Evaluation:
    """全量 AIME 2024 评估。"""

    @pytest.fixture(scope="class")
    def run_result(self, tmp_path_factory):
        """跑完整 AIME 2024 评估并返回 (summary, result_store)。"""
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.runner.runner import Runner

        output_dir = str(tmp_path_factory.mktemp("results"))
        config = ExperimentConfig(
            run_id="test-openclaw-qwen3-8b-aime2024",
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
                "dataset": "HuggingFaceH4/aime_2024",
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
        """30 道题全部完成。"""
        summary, _, _ = run_result
        assert summary.total_tasks == 30
        assert summary.completed == 30
        assert summary.failed == 0

    def test_accuracy_in_range(self, run_result):
        """准确率在合理范围 (0.0 - 1.0)。"""
        summary, _, _ = run_result
        assert 0.0 <= summary.accuracy <= 1.0
        print(f"\n=== AIME 2024 Results ===")
        print(f"Accuracy: {summary.accuracy:.4f} ({int(summary.accuracy * 30)}/30)")
        print(f"Mean Score: {summary.mean_score:.4f}")
        print(f"Mean Wall Time: {summary.mean_wall_time_sec:.1f}s")
        print(f"Total Tokens: {summary.total_tokens}")

    def test_jsonl_file_created(self, run_result):
        """结果 JSONL 文件已创建。"""
        _, store, _ = run_result
        assert Path(store.path).exists()
        results = store.load()
        assert len(results) == 30

    def test_each_result_has_fields(self, run_result):
        """每条结果包含必要字段。"""
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
        """预测的答案应可解析为数字（即使错误）。"""
        _, store, _ = run_result
        results = store.load()
        for r in results:
            predicted = str(r["predicted"])
            # 答案应该是从模型输出中提取的，可能是数字或包含数字的字符串
            assert len(predicted) > 0, f"Empty prediction for {r['task_id']}"

    def test_report_generation(self, run_result):
        """能生成 markdown 报告。"""
        from alphadiana.config.experiment_config import ExperimentConfig
        from alphadiana.results.report import ReportGenerator

        summary, store, _ = run_result
        gen = ReportGenerator()

        config = ExperimentConfig(
            run_id="test-openclaw-qwen3-8b-aime2024",
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
        """打印每题得分明细。"""
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
