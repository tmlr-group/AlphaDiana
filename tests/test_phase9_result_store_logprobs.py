"""Phase 9: ResultStore.write_logprobs_jsonl + token_entropy_stats tests."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from alphadiana.results.result_store import ResultStore
from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.scorer.base import ScoreResult


def _task():
    return BenchmarkTask(task_id="t1", problem="p", ground_truth="A")


def _score():
    return ScoreResult(correct=True, score=1.0, expected="A", predicted="A", rationale="ok", metadata={})


def _response(records, stats):
    return AgentResponse(
        answer="A",
        token_entropy_stats=stats,
        metadata={"logprob_records": records, "other_key": "keep"},
    )


class TestWriteLogprobsJsonl:
    def test_writes_records_line_by_line(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_x")
        records = [
            {"token_index": 0, "token": "Hi", "logprob": -0.1,
             "top_logprobs": [{"token": "Hi", "logprob": -0.1}]},
            {"token_index": 1, "token": "!",  "logprob": -0.3,
             "top_logprobs": [{"token": "!",  "logprob": -0.3}]},
        ]
        path = store.write_logprobs_jsonl("task_0", records)
        assert path == tmp_path / "run_x" / "logprobs" / "task_0.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == records[0]
        assert json.loads(lines[1]) == records[1]

    def test_empty_records_creates_empty_file(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_x")
        path = store.write_logprobs_jsonl("empty_task", [])
        assert path.exists()
        assert path.read_text(encoding="utf-8") == ""

    def test_overwrites_on_second_call(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_x")
        r1 = [{"token_index": 0, "token": "A", "logprob": 0.0, "top_logprobs": []}]
        r2 = [{"token_index": 0, "token": "B", "logprob": 0.0, "top_logprobs": []}]
        store.write_logprobs_jsonl("t", r1)
        path = store.write_logprobs_jsonl("t", r2)
        assert json.loads(path.read_text().strip()) == r2[0]

    def test_multi_sample_paths_do_not_overwrite_each_other(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_x")
        sample0 = [{"token_index": 0, "token": "A", "logprob": 0.0, "top_logprobs": []}]
        sample1 = [{"token_index": 0, "token": "B", "logprob": 0.0, "top_logprobs": []}]
        path0 = store.write_logprobs_jsonl("t", sample0, sample_index=0)
        path1 = store.write_logprobs_jsonl("t", sample1, sample_index=1)
        assert path0 == tmp_path / "run_x" / "logprobs" / "t.jsonl"
        assert path1 == tmp_path / "run_x" / "logprobs" / "t" / "sample_1.jsonl"
        assert json.loads(path0.read_text().strip()) == sample0[0]
        assert json.loads(path1.read_text().strip()) == sample1[0]

    def test_concurrent_writes_different_tasks(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_x")

        def worker(tid: str, n: int):
            recs = [
                {"token_index": i, "token": f"t{i}", "logprob": -0.01 * i,
                 "top_logprobs": [{"token": f"c{j}", "logprob": -0.1 * j} for j in range(5)]}
                for i in range(n)
            ]
            store.write_logprobs_jsonl(tid, recs)

        threads = [threading.Thread(target=worker, args=(f"task_{i}", 100)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for i in range(4):
            p = tmp_path / "run_x" / "logprobs" / f"task_{i}.jsonl"
            lines = p.read_text(encoding="utf-8").splitlines()
            assert len(lines) == 100
            for ln in lines:
                json.loads(ln)  # no decode errors = no interleaving


class TestAppendSerializesLogprobs:
    def test_paths_remain_under_initial_output_dir_after_cwd_change(self, tmp_path: Path, monkeypatch):
        root = tmp_path / "root"
        other = tmp_path / "other"
        root.mkdir()
        other.mkdir()
        monkeypatch.chdir(root)
        store = ResultStore("results", "run_cwd")

        monkeypatch.chdir(other)
        stats = {"mean": 0.5, "max": 0.5, "p50": 0.5, "p90": 0.5, "n_tokens": 1}
        records = [{"token_index": 0, "token": "A", "logprob": -0.1, "top_logprobs": []}]
        store.append(_task(), _response(records, stats), _score())

        expected_root = root / "results"
        assert Path(store.path) == expected_root / "run_cwd.jsonl"
        assert (expected_root / "run_cwd.jsonl").exists()
        assert (expected_root / "run_cwd" / "tasks" / "t1.json").exists()
        assert (expected_root / "run_cwd" / "artifacts" / "t1" / "agent" / "normalized_trace.json").exists()
        assert (expected_root / "run_cwd" / "logprobs" / "t1.jsonl").exists()
        assert not (other / "results").exists()

    def test_with_records(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_y")
        records = [
            {"token_index": 0, "token": "A", "logprob": -0.1,
             "top_logprobs": [{"token": "A", "logprob": -0.1}]},
        ]
        stats = {"mean": 0.5, "max": 0.5, "p50": 0.5, "p90": 0.5, "n_tokens": 1}
        store.append(_task(), _response(records, stats), _score())

        # Logprobs file written
        lp_path = tmp_path / "run_y" / "logprobs" / "t1.jsonl"
        assert lp_path.exists()
        assert json.loads(lp_path.read_text().strip()) == records[0]

        # Main JSONL record
        loaded = store.load()
        assert len(loaded) == 1
        rec = loaded[0]
        assert rec["token_entropy_stats"] == stats
        assert rec["logprobs_path"] == "run_y/logprobs/t1.jsonl"
        assert "logprob_records" not in rec.get("metadata", {})
        assert rec["metadata"].get("other_key") == "keep"

        # Per-task JSON
        per_task = json.loads((tmp_path / "run_y" / "tasks" / "t1.json").read_text())
        assert per_task[0]["token_entropy_stats"] == stats
        assert per_task[0]["logprobs_path"] == "run_y/logprobs/t1.jsonl"

    def test_without_records(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_y")
        stats = {"mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 0}
        store.append(_task(), _response([], stats), _score())

        lp_path = tmp_path / "run_y" / "logprobs" / "t1.jsonl"
        assert not lp_path.exists()

        rec = store.load()[0]
        assert rec["token_entropy_stats"] == stats
        assert rec["logprobs_path"] == ""

    def test_multi_sample_append_uses_distinct_logprob_paths(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_y")
        stats = {"mean": 0.5, "max": 0.5, "p50": 0.5, "p90": 0.5, "n_tokens": 1}
        sample0 = [{"token_index": 0, "token": "A", "logprob": -0.1, "top_logprobs": []}]
        sample1 = [{"token_index": 0, "token": "B", "logprob": -0.2, "top_logprobs": []}]
        store.append(_task(), _response(sample0, stats), _score(), sample_index=0)
        store.append(_task(), _response(sample1, stats), _score(), sample_index=1)

        recs = sorted(store.load(), key=lambda rec: rec["sample_index"])
        assert recs[0]["logprobs_path"] == "run_y/logprobs/t1.jsonl"
        assert recs[1]["logprobs_path"] == "run_y/logprobs/t1/sample_1.jsonl"
        assert json.loads((tmp_path / "run_y" / "logprobs" / "t1.jsonl").read_text().strip()) == sample0[0]
        assert json.loads((tmp_path / "run_y" / "logprobs" / "t1" / "sample_1.jsonl").read_text().strip()) == sample1[0]

    def test_legacy_response_no_entropy_field(self, tmp_path: Path):
        """Older AgentResponse shape (entropy_stats missing) must not crash."""
        store = ResultStore(str(tmp_path), "run_y")
        resp = AgentResponse(answer="A")
        # Simulate legacy by deleting the attribute.
        del resp.token_entropy_stats
        store.append(_task(), resp, _score())
        rec = store.load()[0]
        assert rec["token_entropy_stats"] == {}
        assert rec["logprobs_path"] == ""


class TestAppendErrorSerializesLogprobs:
    def test_error_path_preserves_entropy(self, tmp_path: Path):
        store = ResultStore(str(tmp_path), "run_y")
        records = [
            {"token_index": 0, "token": "X", "logprob": -2.0, "top_logprobs": []},
        ]
        stats = {"mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 1}
        resp = _response(records, stats)
        store.append_error(_task(), error={"error": "boom"}, response=resp)

        lp_path = tmp_path / "run_y" / "logprobs" / "t1.jsonl"
        assert lp_path.exists()
        rec = store.load()[0]
        assert rec["token_entropy_stats"] == stats
        assert rec["logprobs_path"] == "run_y/logprobs/t1.jsonl"
        assert rec["error"] == {"error": "boom"}
