from __future__ import annotations

from alphadiana.benchmark.imo_answerbench import ImoAnswerBenchBenchmark


def test_imo_answerbench_category_filter_uses_dataset_category_field(monkeypatch) -> None:
    rows = [
        {
            "Problem": "P1",
            "Short Answer": "1",
            "Category": "Number theory",
        },
        {
            "Problem": "P2",
            "Short Answer": "2",
            "Category": "Geometry",
        },
    ]
    monkeypatch.setattr(
        "alphadiana.benchmark.imo_answerbench.load_dataset_with_retry",
        lambda *args, **kwargs: rows,
    )

    tasks = ImoAnswerBenchBenchmark().load_tasks({
        "dataset": "Hwilner/imo-answerbench",
        "category": "number theory",
        "max_tasks": 1,
    })

    assert len(tasks) == 1
    assert tasks[0].problem == "P1"


def test_imo_answerbench_dataset_index_selects_single_row(monkeypatch) -> None:
    rows = [
        {"Problem": "P0", "Short Answer": "0", "Category": "Algebra"},
        {"Problem": "P1", "Short Answer": "1", "Category": "Geometry"},
    ]
    monkeypatch.setattr(
        "alphadiana.benchmark.imo_answerbench.load_dataset_with_retry",
        lambda *args, **kwargs: rows,
    )

    tasks = ImoAnswerBenchBenchmark().load_tasks({
        "dataset": "Hwilner/imo-answerbench",
        "dataset_index": 1,
    })

    assert len(tasks) == 1
    assert tasks[0].task_id == "imo_answerbench_1"
    assert tasks[0].problem == "P1"
