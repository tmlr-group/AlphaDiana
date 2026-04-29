from __future__ import annotations

import json

from scripts.analyze_gpqa_trajectories import main


def _write_run(results_dir, run_id, *, task_records):
    run_dir = results_dir / run_id
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_manifest.json").write_text(
        json.dumps({"run_id": run_id, "expected_sample_count": len(task_records)}),
        encoding="utf-8",
    )
    records = []
    for task_id, samples in task_records.items():
        records.extend(samples)
        (tasks_dir / f"{task_id}.json").write_text(json.dumps(samples), encoding="utf-8")
    (results_dir / f"{run_id}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records),
        encoding="utf-8",
    )


def _sample(run_id, task_id, *, correct, score_status="valid_scored", raw_output=None, predicted="B"):
    return {
        "run_id": run_id,
        "task_id": task_id,
        "sample_index": 0,
        "score_status": score_status,
        "correct": correct,
        "raw_output": raw_output or "Plan: compare options. I will verify with a check.",
        "predicted": predicted,
    }


def test_analyze_gpqa_trajectories_cli_writes_phase14_outputs(tmp_path):
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "phase14"
    runs = {
        "full_gpqa_v2_openclaw_qwen35_27b_logprobs": "openclaw",
        "full_gpqa_v2_opencode_qwen35_27b_logprobs": "opencode",
        "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs": "zeroclaw",
        "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs": "directllm",
    }
    for run_id, harness in runs.items():
        _write_run(
            results_dir,
            run_id,
            task_records={
                f"{harness}_correct": [_sample(run_id, f"{harness}_correct", correct=True)],
                f"{harness}_incorrect": [
                    _sample(
                        run_id,
                        f"{harness}_incorrect",
                        correct=False,
                        raw_output="Final answer: A without verification",
                        predicted="A",
                    )
                ],
            },
        )
    _write_run(
        results_dir,
        "bkp_gpqa_20260425",
        task_records={"backup": [_sample("bkp_gpqa_20260425", "backup", correct=True)]},
    )
    measurement_summary = output_dir.parent / "measurement_summary.json"
    measurement_summary.write_text(
        json.dumps(
            {
                "entropy_token_quadrants": [
                    {
                        "bucket": "low_entropy_long",
                        "n": 4,
                        "wrong_rate": 0.75,
                        "median_tokens": 9000,
                        "median_entropy": 0.05,
                        "token_threshold_q75": 5000,
                        "entropy_threshold_q25": 0.1,
                    }
                ],
                "confidence_inversion": [
                    {
                        "entropy_threshold": 0.1,
                        "low_entropy_n": 3,
                        "high_entropy_n": 12,
                        "wrong_rate_low_entropy": 1.0,
                        "wrong_rate_high_entropy": 0.2,
                        "inversion_lift": 0.8,
                    }
                ],
                "posttool_state_shift": [
                    {
                        "turn_label": "after_tool_1+",
                        "wrong_minus_correct_entropy": 0.1,
                        "separation_gain_vs_baseline": 0.11,
                        "correct_n": 7,
                        "wrong_n": 2,
                    }
                ],
                "verification_conversion": [
                    {
                        "harness": "openclaw",
                        "correct": "False",
                        "n": 6,
                        "verify_rate": 0.8,
                        "verify_before_answer_rate": 0.1,
                        "post_verify_action_change_rate": 0.2,
                    },
                    {
                        "harness": "openclaw",
                        "correct": "True",
                        "n": 8,
                        "verify_rate": 0.7,
                        "verify_before_answer_rate": 0.4,
                        "post_verify_action_change_rate": 0.5,
                    },
                ],
                "operational_tax_adjusted_accuracy": [
                    {
                        "harness": "openclaw",
                        "deployable_accuracy": 0.55,
                        "operational_tax": 0.18,
                    }
                ],
                "paired_net_gain": [{"harness": "openclaw", "paired_net_gain": -2}],
                "action_space_distance": [
                    {
                        "harness_a": "openclaw",
                        "harness_b": "zeroclaw",
                        "canonical_action_jsd": 0.51,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "--results-dir",
                str(results_dir),
                "--output-dir",
                str(output_dir),
                "--measurement-summary",
                str(measurement_summary),
            ]
        )
        == 0
    )

    expected_files = {
        "corpus_inventory.json",
        "action_events.csv",
        "trajectory_metrics.json",
        "case_studies.json",
        "gpqa_trajectory_appendix.tex",
        "gpqa_trajectory_discussion_zh.md",
    }
    assert {path.name for path in output_dir.iterdir()} == expected_files

    inventory = json.loads((output_dir / "corpus_inventory.json").read_text(encoding="utf-8"))
    metrics = json.loads((output_dir / "trajectory_metrics.json").read_text(encoding="utf-8"))
    latex = (output_dir / "gpqa_trajectory_appendix.tex").read_text(encoding="utf-8")
    zh = (output_dir / "gpqa_trajectory_discussion_zh.md").read_text(encoding="utf-8")

    assert "metric_names" in metrics
    assert metrics["measurement_insights"]["available"] is True
    assert metrics["measurement_insights"]["low_entropy_long_collapse"]["wrong_rate"] == 0.75
    assert "bkp_gpqa_20260425" not in json.dumps(inventory)
    for heading in (
        "Harness-Induced Action Ecology",
        "Action-Intent Grammar",
        "Extraction Protocol",
        "Outcome-Conditioned Macro Analysis",
        "Micro Trajectory Case Studies",
        "Metrics and Control Motifs",
        "Analyze Tools Measurement Insights",
        "Caveats",
    ):
        assert heading in latex
    for heading in (
        "# GPQA-Diamond Qwen3.5 跨框架轨迹分析讨论",
        "## 动作意图语法",
        "## 宏观结果",
        "## 指标定义",
        "## 指标分析",
        "## analyze_tools measurement insights",
        "## 微观案例",
        "## 注意事项",
    ):
        assert heading in zh
    for required_text in (
        "`DeltaVerifyShare`",
        "`DeltaToolUseShare`",
        "`AnswerAfterVerificationRate`",
        "`ErrorRecoveryRate`",
        "`PrematureAnswerRate`",
        "`MotifOutcomeLift`",
        "`FailureCostRatio`",
        "`LowEntropyLongCollapseRate`",
        "`OperationalTaxAdjustedAccuracy`",
        "`ScaffoldDominance`",
        "### Failure-cost / readiness",
        "### 解读要点",
    ):
        assert required_text in zh
