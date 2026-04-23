"""Phase 9: monitor + review script tests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.phase9_monitor import monitor_once, _format_alert


def _write_task(run_dir: Path, task_id: str, *, error: bool = False,
                correct: bool = True, wall: float = 10.0) -> None:
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "task_id": task_id,
        "sample_index": 0,
        "correct": None if error else correct,
        "error": {"error": "boom"} if error else None,
        "wall_time_sec": wall,
        "token_entropy_stats": {"mean": 0.5, "max": 1.0, "p50": 0.5, "p90": 0.8, "n_tokens": 100},
    }
    (tasks_dir / f"{task_id}.json").write_text(json.dumps([record]))


class TestMonitorOnce:
    def test_empty_run_dir_no_alerts(self, tmp_path: Path):
        state, alerts = monitor_once(
            tmp_path, {},
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=1000.0,
        )
        assert alerts == []
        assert state["consecutive_failures"] == 0

    def test_successful_tasks_reset_counter(self, tmp_path: Path):
        for i in range(3):
            _write_task(tmp_path, f"t{i}", error=False)
        state, alerts = monitor_once(
            tmp_path, {},
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=1000.0,
        )
        assert alerts == []
        assert state["consecutive_failures"] == 0
        assert state["seen_tasks"] == {"t0", "t1", "t2"}

    def test_consecutive_failures_triggers_alert(self, tmp_path: Path):
        for i in range(5):
            _write_task(tmp_path, f"t{i}", error=True)
        state, alerts = monitor_once(
            tmp_path, {},
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=1000.0,
        )
        assert ("consecutive_failures", 5) in alerts
        assert state["consecutive_failures"] == 5

    def test_failure_after_success_does_not_trigger(self, tmp_path: Path):
        _write_task(tmp_path, "t0", error=False)
        _write_task(tmp_path, "t1", error=True)
        _write_task(tmp_path, "t2", error=False)
        _write_task(tmp_path, "t3", error=True)
        state, alerts = monitor_once(
            tmp_path, {},
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=1000.0,
        )
        # Sorted order t0..t3 -> counter: 0,1,0,1 -> does NOT hit 5
        assert not any(a[0] == "consecutive_failures" for a in alerts)
        assert state["consecutive_failures"] == 1

    def test_silence_alert(self, tmp_path: Path):
        _write_task(tmp_path, "t0", error=False)
        state1, _ = monitor_once(
            tmp_path, {},
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=1000.0,
        )
        # Second pass, 3000 s later, no new tasks
        state2, alerts = monitor_once(
            tmp_path, state1,
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=4000.0,
        )
        assert any(a[0] == "silence" for a in alerts)

    def test_task_timeout_alert(self, tmp_path: Path):
        _write_task(tmp_path, "slow_task", error=False, wall=2000.0)
        _, alerts = monitor_once(
            tmp_path, {},
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=1000.0,
        )
        assert ("task_timeout", "slow_task", 2000.0) in alerts

    def test_malformed_json_ignored(self, tmp_path: Path):
        (tmp_path / "tasks").mkdir()
        (tmp_path / "tasks" / "bad.json").write_text("{not valid json")
        state, alerts = monitor_once(
            tmp_path, {},
            task_timeout=1800, silence_timeout=2700, max_failures=5, now=1000.0,
        )
        assert state["seen_tasks"] == set()
        assert alerts == []


class TestFormatAlert:
    def test_consecutive_failures_message(self):
        assert "ALERT:" in _format_alert(("consecutive_failures", 5))
        assert "5" in _format_alert(("consecutive_failures", 5))

    def test_silence_message(self):
        msg = _format_alert(("silence", 3000.0))
        assert msg.startswith("ALERT:")
        assert "3000" in msg

    def test_task_timeout_message(self):
        msg = _format_alert(("task_timeout", "gpqa_42", 2000.0))
        assert "gpqa_42" in msg
        assert msg.startswith("ALERT:")


from scripts.phase9_review import generate_review, main as review_main


def _write_jsonl(run_dir: Path, records: list[dict]) -> None:
    """Write records to results/{run_id}.jsonl (one line per record)."""
    run_id = run_dir.name
    jsonl = run_dir.parent / f"{run_id}.jsonl"
    jsonl.parent.mkdir(parents=True, exist_ok=True)
    jsonl.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _make_record(task_id: str, correct: bool, entropy_mean: float,
                 predicted: str = "A", ground_truth: str = "A") -> dict:
    return {
        "task_id": task_id,
        "correct": correct,
        "predicted": predicted,
        "ground_truth": ground_truth,
        "token_entropy_stats": {
            "mean": entropy_mean, "max": entropy_mean + 0.5,
            "p50": entropy_mean, "p90": entropy_mean + 0.3,
            "n_tokens": 100,
        },
        "logprobs_path": f"{task_id}.jsonl",
        "wall_time_sec": 10.0,
    }


class TestGenerateReview:
    def test_missing_jsonl(self, tmp_path: Path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        md = generate_review(run_dir)
        assert "# Phase 9 Review" in md
        assert "No results found" in md

    def test_accuracy_and_sections(self, tmp_path: Path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        records = [
            _make_record("t0", True, 0.5, predicted="A", ground_truth="A"),
            _make_record("t1", True, 1.0, predicted="B", ground_truth="B"),
            _make_record("t2", False, 2.5, predicted="C", ground_truth="D"),  # high-entropy error
            _make_record("t3", False, 0.2, predicted="A", ground_truth="B"),  # low-entropy error
        ]
        _write_jsonl(run_dir, records)
        md = generate_review(run_dir)
        assert "## Overall Accuracy" in md
        assert "2" in md  # 2 correct
        assert "50.00%" in md
        assert "## Entropy Summary" in md
        assert "## High-Entropy Errors" in md
        assert "## Low-Entropy Errors" in md

    def test_errors_appear_in_correct_sections(self, tmp_path: Path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        records = [
            _make_record("lo_err", False, 0.1, predicted="A", ground_truth="B"),
            _make_record("med_ok", True, 1.0),
            _make_record("hi_err", False, 2.5, predicted="C", ground_truth="D"),
            _make_record("med_ok2", True, 1.1),
        ]
        _write_jsonl(run_dir, records)
        md = generate_review(run_dir)
        # hi_err (entropy 2.5) in high section; lo_err (0.1) in low section
        hi_idx = md.index("## High-Entropy Errors")
        lo_idx = md.index("## Low-Entropy Errors")
        assert md.index("hi_err") > hi_idx
        assert md.index("lo_err") > lo_idx

    def test_records_without_entropy_excluded_from_error_lists(self, tmp_path: Path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        rec_no_stats = {"task_id": "no_stats", "correct": False,
                        "predicted": "A", "ground_truth": "B",
                        "token_entropy_stats": {}}
        records = [
            _make_record("t0", True, 0.5),
            rec_no_stats,
        ]
        _write_jsonl(run_dir, records)
        md = generate_review(run_dir)
        # Accuracy counts the no-stats record as an error (1/2 = 50%)
        assert "50.00%" in md
        # But it is not in the entropy error lists
        assert "no_stats" not in md.split("## High-Entropy Errors")[1]


class TestReviewCLI:
    def test_reads_jsonl_inside_run_dir(self, tmp_path: Path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        (run_dir / "run_1.jsonl").write_text(
            json.dumps(_make_record("t0", True, 0.5)) + "\n",
            encoding="utf-8",
        )
        md = generate_review(run_dir)
        assert "Total records: **1**" in md
        assert "Correct: **1**" in md

    def test_writes_codex_review_md(self, tmp_path: Path):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        _write_jsonl(run_dir, [_make_record("t0", True, 0.5)])
        rc = review_main(["--run-dir", str(run_dir)])
        assert rc == 0
        review_path = run_dir / "codex_review.md"
        assert review_path.exists()
        assert "# Phase 9 Review" in review_path.read_text()

    def test_stdout_flag(self, tmp_path: Path, capsys):
        run_dir = tmp_path / "run_1"
        run_dir.mkdir()
        _write_jsonl(run_dir, [_make_record("t0", True, 0.5)])
        rc = review_main(["--run-dir", str(run_dir), "--stdout"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "# Phase 9 Review" in captured.out
