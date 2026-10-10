import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "matrix", Path(__file__).parents[2] / "scripts/validation/validate_api_matrix.py"
)
matrix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(matrix)


def test_concurrent_completion_preserves_task_sample_coverage():
    cell = {"task_ids": ["a", "b", "c"]}
    rows = [
        {"task_id": t, "sample_index": s}
        for s in reversed(range(4))
        for t in reversed(cell["task_ids"])
    ]
    assert matrix.validate_records(rows, cell, 4) == cell["task_ids"]
    with pytest.raises(RuntimeError):
        matrix.validate_records(rows[:-1] + [rows[0]], cell, 4)
