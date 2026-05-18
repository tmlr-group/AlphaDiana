"""Phase 10 ResultStore logprob artifact sidecar tests."""
from __future__ import annotations

import json
from pathlib import Path

from alphadiana.agent.base import AgentResponse
from alphadiana.benchmark.base import BenchmarkTask
from alphadiana.results.logprob_artifacts import INT16_PROB_SCALE
from alphadiana.results.result_store import ResultStore
from alphadiana.scorer.base import ScoreResult


def _raw_logprob_records() -> list[dict]:
    return [
        {
            "token_index": 0,
            "token": "A",
            "logprob": -0.1,
            "top_logprobs": [
                {"token": "A", "logprob": -0.1},
                {"token": "B", "logprob": -2.0},
            ],
        },
        {
            "token_index": 1,
            "token": "!",
            "logprob": -0.3,
            "top_logprobs": [
                {"token": "!", "logprob": -0.3},
                {"token": ".", "logprob": -1.5},
            ],
        },
    ]


def _task() -> BenchmarkTask:
    return BenchmarkTask(task_id="t1", problem="p", ground_truth="A")


def _score() -> ScoreResult:
    return ScoreResult(correct=True, score=1.0, expected="A", predicted="A", rationale="ok", metadata={})


class TestResultStoreLogprobSidecars:
    def test_write_logprob_sidecars_writes_raw_and_converted_int16(self, tmp_path: Path) -> None:
        store = ResultStore(str(tmp_path), "run_z")
        response_metadata = {
            "logprob_records": _raw_logprob_records(),
            "other_key": "kept",
        }

        refs = store.write_logprob_sidecars("t1", response_metadata)

        assert response_metadata == {"other_key": "kept", "logprob_records": 2}
        assert refs == {
            "logprobs_path": "run_z/logprobs/t1.jsonl",
            "logprobs_int16_path": "run_z/logprobs_int16/t1.jsonl",
            "top_logprobs": 20,
            "int16_probability_scale": INT16_PROB_SCALE,
            "artifact_file_refs": {
                "logprobs_float": "logprobs/t1.jsonl",
                "logprobs_int16": "logprobs_int16/t1.jsonl",
            },
        }

        raw_path = tmp_path / "run_z" / "logprobs" / "t1.jsonl"
        int16_path = tmp_path / "run_z" / "logprobs_int16" / "t1.jsonl"
        assert raw_path.exists()
        assert int16_path.exists()
        assert [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()] == (
            _raw_logprob_records()
        )

        int16_records = [
            json.loads(line)
            for line in int16_path.read_text(encoding="utf-8").splitlines()
        ]
        assert len(int16_records) == 2
        assert int16_records[0]["token_index"] == 0
        assert int16_records[0]["token"] == "A"
        assert set(int16_records[0]) == {"token_index", "token", "top20", "entropy_nats"}
        assert len(int16_records[0]["top20"]) == 2
        assert all(0 <= entry["prob_i16"] <= INT16_PROB_SCALE for entry in int16_records[0]["top20"])

    def test_write_logprob_sidecars_uses_provided_int16_records(self, tmp_path: Path) -> None:
        store = ResultStore(str(tmp_path), "run_z")
        int16_records = [
            {
                "token_index": 0,
                "token": "A",
                "top20": [{"token": "A", "prob_i16": INT16_PROB_SCALE}],
                "entropy_nats": 0.0,
            },
        ]
        response_metadata = {"logprob_int16_records": int16_records}

        refs = store.write_logprob_sidecars("t1", response_metadata)

        assert response_metadata == {}
        assert refs["logprobs_path"] == ""
        assert refs["logprobs_int16_path"] == "run_z/logprobs_int16/t1.jsonl"
        assert refs["top_logprobs"] == 20
        assert refs["int16_probability_scale"] == INT16_PROB_SCALE
        assert refs["artifact_file_refs"] == {
            "logprobs_int16": "logprobs_int16/t1.jsonl",
        }
        assert not (tmp_path / "run_z" / "logprobs" / "t1.jsonl").exists()
        assert json.loads(
            (tmp_path / "run_z" / "logprobs_int16" / "t1.jsonl").read_text(encoding="utf-8").strip()
        ) == int16_records[0]

    def test_write_logprob_sidecars_returns_empty_summary_without_records(self, tmp_path: Path) -> None:
        store = ResultStore(str(tmp_path), "run_z")
        response_metadata = {"logprob_records": [], "logprob_int16_records": [], "other_key": "kept"}

        refs = store.write_logprob_sidecars("t1", response_metadata)

        assert response_metadata == {"other_key": "kept"}
        assert refs == {
            "logprobs_path": "",
            "logprobs_int16_path": "",
            "top_logprobs": 0,
            "int16_probability_scale": 0,
            "artifact_file_refs": {},
        }
        assert not (tmp_path / "run_z" / "logprobs" / "t1.jsonl").exists()
        assert not (tmp_path / "run_z" / "logprobs_int16" / "t1.jsonl").exists()

    def test_append_wires_sidecar_fields_manifest_and_normalized_trace(self, tmp_path: Path) -> None:
        store = ResultStore(str(tmp_path), "run_z")
        response = AgentResponse(
            answer="A",
            token_entropy_stats={"mean": 0.5, "max": 0.5, "p50": 0.5, "p90": 0.5, "n_tokens": 2},
            metadata={"logprob_records": _raw_logprob_records(), "other_key": "kept"},
            artifact_manifest={"files": {"response_stream": "runtime_trace.jsonl"}},
            workspace_file_contents={"runtime_trace.jsonl": "{}\n"},
        )

        store.append(_task(), response, _score())

        record = store.load()[0]
        assert record["logprobs_path"] == "run_z/logprobs/t1.jsonl"
        assert record["logprobs_int16_path"] == "run_z/logprobs_int16/t1.jsonl"
        assert record["top_logprobs"] == 20
        assert record["int16_probability_scale"] == INT16_PROB_SCALE
        assert record["metadata"]["logprob_records"] == 2
        assert "logprob_int16_records" not in record["metadata"]
        assert record["metadata"] == {"other_key": "kept", "logprob_records": 2}

        files = record["artifact_manifest"]["files"]
        assert files["response_stream"].endswith("t1/workspace/runtime_trace.jsonl")
        assert files["normalized_trace"].endswith("t1/agent/normalized_trace.json")
        assert files["logprobs_float"] == "logprobs/t1.jsonl"
        assert files["logprobs_int16"] == "logprobs_int16/t1.jsonl"

        task_record = json.loads((tmp_path / "run_z" / "tasks" / "t1.json").read_text())[0]
        assert task_record["logprobs_int16_path"] == "run_z/logprobs_int16/t1.jsonl"
        assert task_record["artifact_manifest"]["files"]["logprobs_int16"] == "logprobs_int16/t1.jsonl"

        trace = json.loads(
            (tmp_path / "run_z" / "artifacts" / "t1" / "agent" / "normalized_trace.json").read_text(
                encoding="utf-8"
            )
        )
        assert trace["refs"]["logprobs_float"] == "logprobs/t1.jsonl"
        assert trace["refs"]["logprobs_int16"] == "logprobs_int16/t1.jsonl"

    def test_append_error_wires_sidecar_fields_and_strips_metadata(self, tmp_path: Path) -> None:
        store = ResultStore(str(tmp_path), "run_z")
        response = AgentResponse(
            answer=None,
            token_entropy_stats={"mean": 0.0, "max": 0.0, "p50": 0.0, "p90": 0.0, "n_tokens": 1},
            metadata={"logprob_records": _raw_logprob_records(), "other_key": "kept"},
        )

        store.append_error(_task(), error={"error": "boom"}, response=response)

        record = store.load()[0]
        assert record["error"] == {"error": "boom"}
        assert record["logprobs_path"] == "run_z/logprobs/t1.jsonl"
        assert record["logprobs_int16_path"] == "run_z/logprobs_int16/t1.jsonl"
        assert record["top_logprobs"] == 20
        assert record["int16_probability_scale"] == INT16_PROB_SCALE
        assert record["metadata"]["logprob_records"] == 2
        assert "logprob_int16_records" not in record["metadata"]
        assert record["artifact_manifest"]["files"]["logprobs_float"] == "logprobs/t1.jsonl"
        assert record["artifact_manifest"]["files"]["logprobs_int16"] == "logprobs_int16/t1.jsonl"
