"""Phase 9: ResultStore.write_logprobs_jsonl + token_entropy_stats tests."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from alphadiana.results.result_store import ResultStore


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
