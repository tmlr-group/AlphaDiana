from __future__ import annotations

from alphadiana.benchmark.mmmu_pro import MMMUProBenchmark


class _FakeImage:
    def save(self, buf, format: str = "PNG") -> None:
        assert format == "PNG"
        buf.write(b"fake-png")


def _fake_rows():
    return [
        {
            "id": "demo",
            "question": "Which option is correct?",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "subject": "history",
            "topic": "demo",
            "question_type": "multiple-choice",
            "image_1": _FakeImage(),
        }
    ]


def test_mmmu_pro_standard_configs_do_not_emit_attachments(monkeypatch) -> None:
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

    assert task.attachments == {}


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
