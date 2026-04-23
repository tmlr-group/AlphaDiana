"""Phase 10 ResultStore logprob artifact sidecar tests."""
from __future__ import annotations

import json
from pathlib import Path

from alphadiana.results.logprob_artifacts import INT16_PROB_SCALE
from alphadiana.results.result_store import ResultStore


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


class TestResultStoreLogprobSidecars:
    def test_write_logprob_sidecars_writes_raw_and_converted_int16(self, tmp_path: Path) -> None:
        store = ResultStore(str(tmp_path), "run_z")
        response_metadata = {
            "logprob_records": _raw_logprob_records(),
            "other_key": "kept",
        }

        refs = store.write_logprob_sidecars("t1", response_metadata)

        assert response_metadata == {"other_key": "kept"}
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
