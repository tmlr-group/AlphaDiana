from __future__ import annotations

from alphadiana.benchmark.mmmu_pro import MMMUProBenchmark


class _FakeImage:
    def save(self, buf, format: str = "PNG") -> None:
        assert format == "PNG"
        buf.write(b"fake-png")


def _fake_rows():
    return [
        {
            "id": "demo0",
            "question": "Which option is correct for row 0?",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "subject": "history",
            "topic": "demo",
            "question_type": "multiple-choice",
            "image_1": _FakeImage(),
        },
        {
            "id": "demo1",
            "question": "Which option is correct for row 1?",
            "options": ["A", "B", "C", "D"],
            "answer": "B",
            "subject": "physics",
            "topic": "demo",
            "question_type": "multiple-choice",
            "image_1": _FakeImage(),
        },
        {
            "id": "demo2",
            "question": "Which option is correct for row 2?",
            "options": ["A", "B", "C", "D"],
            "answer": "C",
            "subject": "chemistry",
            "topic": "demo",
            "question_type": "multiple-choice",
            "image_1": _FakeImage(),
        }
    ]


def test_mmmu_pro_standard_configs_emit_numbered_attachments(monkeypatch) -> None:
    monkeypatch.setattr(
        "alphadiana.benchmark.mmmu_pro.load_dataset_with_retry",
        lambda *args, **kwargs: _fake_rows(),
    )

    task = MMMUProBenchmark().load_tasks({
        "dataset": "MMMU/MMMU_Pro",
        "data_config": "standard (4 options)",
        "split": "test",
        "max_tasks": 1,
    })[0]

    assert task.attachments["image_1"] == b"fake-png"
    assert task.attachments["image_1_mime"] == b"image/png"


def test_mmmu_pro_dataset_index_selects_single_vision_row(monkeypatch) -> None:
    monkeypatch.setattr(
        "alphadiana.benchmark.mmmu_pro.load_dataset_with_retry",
        lambda *args, **kwargs: _fake_rows(),
    )

    tasks = MMMUProBenchmark().load_tasks({
        "dataset": "MMMU/MMMU_Pro",
        "data_config": "vision",
        "split": "test",
        "dataset_index": 1,
    })

    assert len(tasks) == 1
    assert tasks[0].task_id == "mmmu_pro_demo1"
    assert tasks[0].ground_truth == "B"
    assert tasks[0].metadata["data_config"] == "vision"
    assert tasks[0].attachments["image_1"] == b"fake-png"


def test_mmmu_pro_dataset_indices_select_rows_in_order(monkeypatch) -> None:
    monkeypatch.setattr(
        "alphadiana.benchmark.mmmu_pro.load_dataset_with_retry",
        lambda *args, **kwargs: _fake_rows(),
    )

    tasks = MMMUProBenchmark().load_tasks({
        "dataset": "MMMU/MMMU_Pro",
        "data_config": "vision",
        "split": "test",
        "dataset_indices": [2, 0],
    })

    assert [task.task_id for task in tasks] == ["mmmu_pro_demo2", "mmmu_pro_demo0"]
    assert [task.ground_truth for task in tasks] == ["C", "A"]
    assert [task.metadata["data_config"] for task in tasks] == ["vision", "vision"]
    assert [task.attachments["image_1"] for task in tasks] == [b"fake-png", b"fake-png"]


def test_mmmu_pro_rejects_dataset_index_and_indices_together(monkeypatch) -> None:
    monkeypatch.setattr(
        "alphadiana.benchmark.mmmu_pro.load_dataset_with_retry",
        lambda *args, **kwargs: _fake_rows(),
    )

    try:
        MMMUProBenchmark().load_tasks({
            "dataset": "MMMU/MMMU_Pro",
            "data_config": "vision",
            "split": "test",
            "dataset_index": 0,
            "dataset_indices": [1],
        })
    except ValueError as exc:
        assert "dataset_index" in str(exc)
        assert "dataset_indices" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_mmmu_pro_vision_emits_png_attachments(monkeypatch) -> None:
    monkeypatch.setattr(
        "alphadiana.benchmark.mmmu_pro.load_dataset_with_retry",
        lambda *args, **kwargs: _fake_rows(),
    )

    task = MMMUProBenchmark().load_tasks({
        "dataset": "MMMU/MMMU_Pro",
        "data_config": "vision",
        "split": "test",
        "max_tasks": 1,
    })[0]

    assert task.attachments["image_1"] == b"fake-png"
    assert task.attachments["image_1_mime"] == b"image/png"
