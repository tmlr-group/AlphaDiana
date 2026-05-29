from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_mock_dataset(rows: list[dict], *, column_names: list[str] | None = None):
    ds = MagicMock()
    ds.__iter__ = lambda self: iter(rows)
    ds.__len__ = lambda self: len(rows)
    ds.__getitem__ = lambda self, i: rows[i]
    ds.column_names = column_names or (list(rows[0].keys()) if rows else [])
    return ds


class _FakeImage:
    def __init__(self, payload: bytes = b"fake-png") -> None:
        self.payload = payload

    def save(self, buf, format: str = "PNG") -> None:
        assert format == "PNG"
        buf.write(self.payload)


def _swebench_pro_row(instance_id: str) -> dict:
    return {
        "repo": "owner/repo",
        "instance_id": instance_id,
        "base_commit": "abc123",
        "problem_statement": "Fix the issue",
        "dockerhub_tag": "repo:tag",
    }


@patch("alphadiana.benchmark.hle.load_dataset_with_retry")
def test_hle_category_field_defaults_to_category(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {
            "id": "1",
            "question": "Q?",
            "answer": "A",
            "category": "Math",
            "answer_type": "multipleChoice",
            "image": None,
        }
    ])
    from alphadiana.benchmark.hle import HLEBenchmark

    tasks = HLEBenchmark().load_tasks({"dataset": "cais/hle", "category": "Math"})

    assert len(tasks) == 1
    assert tasks[0].metadata.get("category") == "Math"


@patch("alphadiana.benchmark.hle.load_dataset_with_retry")
def test_hle_fails_fast_when_category_field_missing(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"id": "1", "question": "Q?", "answer": "A", "answer_type": "multipleChoice", "image": None}
    ])
    from alphadiana.benchmark.hle import HLEBenchmark

    with pytest.raises(ValueError, match="category.*Available fields"):
        HLEBenchmark().load_tasks({"dataset": "cais/hle", "category": "Math"})


@patch("alphadiana.benchmark.hle.load_dataset_with_retry")
def test_hle_non_data_uri_image_raises_or_attaches(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {
            "id": "1",
            "question": "Q?",
            "answer": "A",
            "category": "Math",
            "answer_type": "multipleChoice",
            "image": "https://example.com/x.png",
        }
    ])
    from alphadiana.benchmark.hle import HLEBenchmark

    with pytest.raises((NotImplementedError, RuntimeError, TypeError), match="image|URL"):
        HLEBenchmark().load_tasks({"dataset": "cais/hle"})


@patch("alphadiana.benchmark.hle.load_dataset_with_retry")
def test_hle_data_uri_image_attached(mock_load):
    payload = b"png-bytes"
    mock_load.return_value = _make_mock_dataset([
        {
            "id": "1",
            "question": "Q?",
            "answer": "A",
            "category": "Math",
            "answer_type": "multipleChoice",
            "image": "data:image/png;base64," + base64.b64encode(payload).decode("ascii"),
        }
    ])
    from alphadiana.benchmark.hle import HLEBenchmark

    task = HLEBenchmark().load_tasks({"dataset": "cais/hle"})[0]

    assert task.attachments["image_1"] == payload
    assert task.attachments["image_1_mime"] == b"image/png"


@patch("alphadiana.benchmark.hle.load_dataset_with_retry")
def test_hle_dataset_index_range_checked(mock_load):
    rows = [
        {"question": f"Q{i}", "answer": "A", "category": "Math", "answer_type": "multipleChoice", "image": None}
        for i in range(10)
    ]
    mock_load.return_value = _make_mock_dataset(rows)
    from alphadiana.benchmark.hle import HLEBenchmark

    with pytest.raises(IndexError, match="dataset_index=999.*out of range"):
        HLEBenchmark().load_tasks({"dataset": "cais/hle", "dataset_index": 999})


@patch("alphadiana.benchmark.hle.load_dataset_with_retry")
def test_hle_max_tasks_zero_returns_empty(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"question": "Q?", "answer": "A", "category": "Math", "answer_type": "multipleChoice", "image": None}
    ])
    from alphadiana.benchmark.hle import HLEBenchmark

    assert HLEBenchmark().load_tasks({"dataset": "cais/hle", "max_tasks": 0}) == []


@patch("alphadiana.benchmark.imo_answerbench.load_dataset_with_retry")
def test_imo_fails_fast_category_field_absent(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"Problem": "P", "Short Answer": "A", "Category": "Algebra"}
    ])
    from alphadiana.benchmark.imo_answerbench import ImoAnswerBenchBenchmark

    with pytest.raises(ValueError, match="category.*Available fields"):
        ImoAnswerBenchBenchmark().load_tasks({
            "dataset": "Hwilner/imo-answerbench",
            "category": "Algebra",
            "category_field": "category",
        })


@patch("alphadiana.benchmark.imo_answerbench.load_dataset_with_retry")
def test_imo_uses_problem_id_as_task_id(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"Problem": "P", "Short Answer": "A", "Category": "Algebra", "Problem ID": "IMO-2024-1"}
    ])
    from alphadiana.benchmark.imo_answerbench import ImoAnswerBenchBenchmark

    task = ImoAnswerBenchBenchmark().load_tasks({"dataset": "Hwilner/imo-answerbench"})[0]

    assert task.task_id == "IMO-2024-1"


@patch("alphadiana.benchmark.imo_answerbench.load_dataset_with_retry")
def test_imo_metadata_includes_subcategory_source_problem_id(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {
            "Problem": "P",
            "Short Answer": "A",
            "Category": "Algebra",
            "Subcategory": "Inequalities",
            "Source": "IMO 2024",
            "Problem ID": "IMO-2024-1",
        }
    ])
    from alphadiana.benchmark.imo_answerbench import ImoAnswerBenchBenchmark

    task = ImoAnswerBenchBenchmark().load_tasks({"dataset": "Hwilner/imo-answerbench"})[0]

    assert task.metadata["Subcategory"] == "Inequalities"
    assert task.metadata["Source"] == "IMO 2024"
    assert task.metadata["Problem ID"] == "IMO-2024-1"


@patch("alphadiana.benchmark.imo_answerbench.load_dataset_with_retry")
def test_imo_dataset_index_range_checked(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"Problem": f"P{i}", "Short Answer": "A", "Category": "Algebra"} for i in range(2)
    ])
    from alphadiana.benchmark.imo_answerbench import ImoAnswerBenchBenchmark

    with pytest.raises(IndexError, match="dataset_index=9.*out of range"):
        ImoAnswerBenchBenchmark().load_tasks({"dataset": "Hwilner/imo-answerbench", "dataset_index": 9})


@patch("alphadiana.benchmark.imo_answerbench.load_dataset_with_retry")
def test_imo_max_tasks_zero_returns_empty(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"Problem": "P", "Short Answer": "A", "Category": "Algebra"}
    ])
    from alphadiana.benchmark.imo_answerbench import ImoAnswerBenchBenchmark

    assert ImoAnswerBenchBenchmark().load_tasks({"dataset": "Hwilner/imo-answerbench", "max_tasks": 0}) == []


@patch("alphadiana.benchmark.base.load_dataset")
def test_base_retry_does_not_retry_permission_error(mock_ld):
    mock_ld.side_effect = PermissionError("auth")
    from alphadiana.benchmark.base import load_dataset_with_retry

    with pytest.raises(PermissionError):
        load_dataset_with_retry("repo", split="test", max_retries=3)
    assert mock_ld.call_count == 1


@patch("alphadiana.benchmark.base.load_dataset")
def test_base_retry_does_not_retry_filenotfound(mock_ld):
    mock_ld.side_effect = FileNotFoundError("missing")
    from alphadiana.benchmark.base import load_dataset_with_retry

    with pytest.raises(FileNotFoundError):
        load_dataset_with_retry("repo", split="test", max_retries=3)
    assert mock_ld.call_count == 1


@patch("alphadiana.benchmark.base.load_dataset")
def test_base_retry_does_not_retry_value_error(mock_ld):
    mock_ld.side_effect = ValueError("bad config")
    from alphadiana.benchmark.base import load_dataset_with_retry

    with pytest.raises(ValueError):
        load_dataset_with_retry("repo", split="test", max_retries=3)
    assert mock_ld.call_count == 1


@patch("alphadiana.benchmark.base.time.sleep", lambda _: None)
@patch("alphadiana.benchmark.base.load_dataset")
def test_base_retry_retries_transient_network_error(mock_ld):
    mock_ld.side_effect = [ConnectionError("network"), ConnectionError("network"), ["ok"]]
    from alphadiana.benchmark.base import load_dataset_with_retry

    assert load_dataset_with_retry("repo", split="test", max_retries=3) == ["ok"]
    assert mock_ld.call_count == 3


@patch("alphadiana.benchmark.base.load_dataset")
def test_base_readonly_cache_detected(mock_ld):
    mock_ld.side_effect = OSError("Read-only file system: /data0/datasets/.lock")
    from alphadiana.benchmark.base import load_dataset_with_retry

    with pytest.raises(RuntimeError, match=r"HF_DATASETS_CACHE|read.?only|read only"):
        load_dataset_with_retry("repo", split="test")
    assert mock_ld.call_count == 1


@patch("alphadiana.benchmark.mmmu_pro.load_dataset_with_retry")
def test_mmmu_pro_standard_attaches_image_fields(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {
            "id": "demo",
            "question": "Which?",
            "options": ["A", "B", "C", "D"],
            "answer": "A",
            "image_1": _FakeImage(b"one"),
            "image_2": _FakeImage(b"two"),
        }
    ])
    from alphadiana.benchmark.mmmu_pro import MMMUProBenchmark

    task = MMMUProBenchmark().load_tasks({"data_config": "standard (4 options)"})[0]

    assert task.attachments["image_1"] == b"one"
    assert task.attachments["image_2"] == b"two"


@patch("alphadiana.benchmark.mmmu_pro.load_dataset_with_retry")
def test_mmmu_pro_auth_error_message(mock_load):
    mock_load.side_effect = PermissionError("401 unauthorized: token required")
    from alphadiana.benchmark.mmmu_pro import MMMUProBenchmark

    with pytest.raises(RuntimeError, match="authentication"):
        MMMUProBenchmark().load_tasks({})


@patch("alphadiana.benchmark.mmmu_pro.load_dataset_with_retry")
def test_mmmu_pro_network_error_message(mock_load):
    mock_load.side_effect = ConnectionError("connection reset")
    from alphadiana.benchmark.mmmu_pro import MMMUProBenchmark

    with pytest.raises(RuntimeError, match="network"):
        MMMUProBenchmark().load_tasks({})


@patch("alphadiana.benchmark.mmmu_pro.load_dataset_with_retry")
def test_mmmu_pro_config_error_message(mock_load):
    mock_load.side_effect = ValueError("BuilderConfig 'bad' not found")
    from alphadiana.benchmark.mmmu_pro import MMMUProBenchmark

    with pytest.raises(RuntimeError, match="config"):
        MMMUProBenchmark().load_tasks({"data_config": "bad"})


@patch("datasets.load_dataset")
def test_moltbench_utils_level_load_failure_raises(mock_load):
    mock_load.side_effect = RuntimeError("HF unavailable")
    from alphadiana.benchmark.moltbench_utils import load_kernelbench_problems

    with pytest.raises(RuntimeError, match="Failed to load level 1"):
        load_kernelbench_problems([1])


@patch("datasets.load_dataset")
def test_moltbench_utils_removes_broken_fallback(mock_load):
    mock_load.side_effect = [ValueError("Unknown split"), [{"problem_id": 1, "code": "x"}]]
    from alphadiana.benchmark.moltbench_utils import load_kernelbench_problems

    with pytest.raises(RuntimeError, match="Failed to load level 1"):
        load_kernelbench_problems([1])
    assert mock_load.call_count == 1


def test_moltbench_utils_log_parsing_fallback(tmp_path: Path):
    (tmp_path / "benchmark.log").write_text("correctness: PASS\nscore: 1.25\nspeedup: 1.25x\n", encoding="utf-8")
    from alphadiana.benchmark.moltbench_utils import parse_results_json

    parsed = parse_results_json(tmp_path)

    assert parsed["correctness"] == "PASS"
    assert parsed["score"] == 1.25
    assert parsed["speedup"] == 1.25


@patch("alphadiana.benchmark.swebench_pro.load_dataset_with_retry")
def test_swebench_pro_smoke_missing_id_raises(mock_load):
    mock_load.return_value = _make_mock_dataset([_swebench_pro_row("present")])
    from alphadiana.benchmark.swebench_pro import SWEBenchProBenchmark

    with pytest.raises((LookupError, ValueError, KeyError), match="missing-id"):
        SWEBenchProBenchmark().load_tasks({"smoke_instance_ids": ["missing-id"]})


@patch("alphadiana.benchmark.swebench_pro.load_dataset_with_retry")
def test_swebench_pro_explicit_id_missing_raises(mock_load):
    mock_load.return_value = _make_mock_dataset([_swebench_pro_row("present")])
    from alphadiana.benchmark.swebench_pro import SWEBenchProBenchmark

    with pytest.raises((LookupError, ValueError, KeyError), match="missing-id"):
        SWEBenchProBenchmark().load_tasks({"subset": "all", "instance_ids": ["present", "missing-id"]})


@patch("alphadiana.benchmark.swe_bench.load_dataset_with_retry")
def test_swe_bench_validates_row_at_task_construction(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"repo": "owner/repo", "base_commit": "abc123", "problem_statement": "Fix"}
    ])
    from alphadiana.benchmark.swe_bench import SWEBenchBenchmark

    with pytest.raises(ValueError, match="instance_id"):
        SWEBenchBenchmark().load_tasks({})


@patch("alphadiana.benchmark.aime.load_dataset_with_retry")
def test_aime_uses_problem_idx_when_present(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"problem": "P", "answer": "42", "problem_idx": 7}
    ])
    from alphadiana.benchmark.aime import AIMEBenchmark

    task = AIMEBenchmark().load_tasks({"dataset": "MathArena/aime_2026"})[0]

    assert task.task_id == "aime_7"


@patch("alphadiana.benchmark.aime.load_dataset_with_retry")
def test_aime_max_tasks_zero_returns_empty(mock_load):
    mock_load.return_value = _make_mock_dataset([{"problem": "P", "answer": "A", "problem_idx": 1}])
    from alphadiana.benchmark.aime import AIMEBenchmark

    assert AIMEBenchmark().load_tasks({"dataset": "MathArena/aime_2026", "max_tasks": 0}) == []


@patch("alphadiana.benchmark.aime.load_dataset_with_retry")
def test_aime_sliced_split_does_not_append_max_tasks(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"problem": "P17", "answer": "243", "problem_idx": 17}
    ])
    from alphadiana.benchmark.aime import AIMEBenchmark

    tasks = AIMEBenchmark().load_tasks({
        "dataset": "MathArena/aime_2026",
        "split": "train[16:17]",
        "max_tasks": 1,
    })

    assert tasks[0].task_id == "aime_17"
    assert mock_load.call_args.kwargs["split"] == "train[16:17]"


@patch("alphadiana.benchmark.aime.load_dataset_with_retry")
def test_aime_dataset_index_selects_raw_row_and_ignores_max_tasks(mock_load):
    mock_load.return_value = _make_mock_dataset([
        {"problem": "P1", "answer": "1", "problem_idx": 1},
        {"problem": "P17", "answer": "243", "problem_idx": 17},
    ])
    from alphadiana.benchmark.aime import AIMEBenchmark

    tasks = AIMEBenchmark().load_tasks({
        "dataset": "MathArena/aime_2026",
        "split": "train",
        "dataset_index": 1,
        "max_tasks": 1,
    })

    assert len(tasks) == 1
    assert tasks[0].task_id == "aime_17"
    assert tasks[0].metadata["index"] == 1
    assert mock_load.call_args.kwargs["split"] == "train"


@patch("alphadiana.benchmark.gpqa.load_dataset_with_retry")
def test_gpqa_validates_answer_values(mock_load):
    mock_load.return_value = _make_mock_dataset(
        [{"Question": "Q", "Correct Answer": "", "Incorrect Answer 1": "", "Incorrect Answer 2": "", "Incorrect Answer 3": ""}],
        column_names=["Question", "Correct Answer", "Incorrect Answer 1", "Incorrect Answer 2", "Incorrect Answer 3"],
    )
    from alphadiana.benchmark.gpqa import GPQADiamondBenchmark

    with pytest.raises(ValueError, match="answer"):
        GPQADiamondBenchmark().load_tasks({})


def test_gpqa_docstring_says_test_split():
    source = Path("alphadiana/benchmark/gpqa.py").read_text(encoding="utf-8")
    config_section = source.split("Config keys:", 1)[1].split('"""', 1)[0]

    assert "Dataset split (default: \"test\")" in config_section
    assert "Dataset split (default: \"train\")" not in config_section
