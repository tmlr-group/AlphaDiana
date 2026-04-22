"""Phase 9 direct GPQA vLLM top-20 Int16 logprob tests."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
from types import SimpleNamespace

from alphadiana.benchmark.base import BenchmarkTask


SCRIPT_PATH = Path("scripts/phase9_gpqa_top20_int16_entropy.py")
spec = importlib.util.spec_from_file_location("phase9_gpqa_top20_int16_entropy", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def _tlp(token: str, logprob: float, top: list[tuple[str, float]]) -> SimpleNamespace:
    return SimpleNamespace(
        token=token,
        logprob=logprob,
        top_logprobs=[
            SimpleNamespace(token=t, logprob=lp)
            for t, lp in top
        ],
    )


def test_top_logprob_to_int16_record_quantizes_and_computes_entropy():
    converted = script.top_logprob_to_int16_record(
        _tlp("A", -0.1, [("A", -0.1), ("B", -1.1)]),
        0,
    )

    assert converted["token_index"] == 0
    assert converted["token"] == "A"
    assert converted["top20"] == [
        {"token": "A", "prob_i16": 23955},
        {"token": "B", "prob_i16": 8812},
    ]
    assert abs(converted["entropy_nats"] - 0.5822031088882179) < 1e-12


def test_uniform_top20_entropy_is_log20():
    converted = script.top_logprob_to_int16_record(
        _tlp("t0", math.log(1 / 20), [(f"t{i}", math.log(1 / 20)) for i in range(20)]),
        0,
    )

    assert len(converted["top20"]) == 20
    assert all(c["prob_i16"] in {1638, 1639} for c in converted["top20"])
    assert abs(converted["entropy_nats"] - math.log(20)) < 1e-12


def test_run_task_writes_trajectory_artifacts_and_int16_logprobs(tmp_path: Path, monkeypatch):
    records = [
        script.top_logprob_to_int16_record(
            _tlp("A", -0.1, [("A", -0.1), ("B", -1.1)]),
            0,
        )
    ]

    def fake_call_vllm(*args, **kwargs):
        return "$$\\boxed{A}$$", "", {"completion_tokens": 1}, {"id": "cmpl-test"}, records

    monkeypatch.setattr(script, "call_vllm", fake_call_vllm)
    task = BenchmarkTask(task_id="gpqa_0", problem="Q?", ground_truth="A", metadata={"index": 0})

    rec = script.run_task(
        SimpleNamespace(),
        task,
        run_dir=tmp_path,
        run_id="run_x",
        model="Qwen/Qwen3.5-27B",
        temperature=0.0,
        top_p=0.95,
        max_tokens=128,
    )

    assert rec["score"] == 1.0
    assert rec["top_logprobs"] == 20
    assert rec["int16_probability_scale"] == 32767
    assert rec["token_entropy_stats"]["n_tokens"] == 1
    assert (tmp_path / "artifacts/gpqa_0/request_messages.json").exists()
    assert (tmp_path / "artifacts/gpqa_0/response.json").exists()
    assert (tmp_path / "trajectories/gpqa_0.json").exists()
    logprob_line = (tmp_path / "logprobs_int16/gpqa_0.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(logprob_line)["top20"][0]["prob_i16"] == 23955
    task_record = json.loads((tmp_path / "tasks/gpqa_0.json").read_text(encoding="utf-8"))[0]
    assert task_record["logprobs_int16_path"] == "logprobs_int16/gpqa_0.jsonl"
