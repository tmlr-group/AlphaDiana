from __future__ import annotations

import base64

from alphadiana.benchmark.hle import HLEBenchmark


def test_hle_benchmark_allows_cached_load_without_hf_token(monkeypatch) -> None:
    benchmark = HLEBenchmark()
    rows = [
        {
            "question": "Q1",
            "answer": "A",
            "answer_type": "multipleChoice",
            "subject": "math",
        }
    ]

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr("alphadiana.benchmark.hle.load_dataset_with_retry", lambda *args, **kwargs: rows)

    tasks = benchmark.load_tasks({
        "dataset": "cais/hle",
        "split": "test",
        "answer_types": ["multipleChoice"],
        "max_tasks": 1,
    })

    assert len(tasks) == 1
    assert tasks[0].task_id == "hle_0"
    assert tasks[0].ground_truth == "A"


def test_hle_benchmark_dataset_index_selects_single_row(monkeypatch) -> None:
    rows = [
        {
            "question": "Q0",
            "answer": "A",
            "answer_type": "multipleChoice",
            "subject": "math",
        },
        {
            "question": "Q1",
            "answer": "B",
            "answer_type": "multipleChoice",
            "subject": "physics",
        },
    ]

    monkeypatch.setattr("alphadiana.benchmark.hle.load_dataset_with_retry", lambda *args, **kwargs: rows)

    tasks = HLEBenchmark().load_tasks({
        "dataset": "cais/hle",
        "dataset_index": 1,
        "answer_types": ["multipleChoice"],
    })

    assert len(tasks) == 1
    assert tasks[0].task_id == "hle_1"
    assert tasks[0].ground_truth == "B"


def test_hle_benchmark_extracts_image_attachments_from_data_urls(monkeypatch) -> None:
    payload = b"fake-image-payload"
    rows = [
        {
            "question": "Q0",
            "answer": "A",
            "answer_type": "multipleChoice",
            "subject": "math",
            "image": "data:image/png;base64," + base64.b64encode(payload).decode("ascii"),
        },
    ]

    monkeypatch.setattr("alphadiana.benchmark.hle.load_dataset_with_retry", lambda *args, **kwargs: rows)
    tasks = HLEBenchmark().load_tasks({
        "dataset": "cais/hle",
        "answer_types": ["multipleChoice"],
        "max_tasks": 1,
    })

    assert len(tasks) == 1
    assert tasks[0].attachments["image_1"] == payload
    assert tasks[0].attachments["image_1_mime"] == b"image/png"
