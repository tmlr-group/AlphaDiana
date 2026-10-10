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


def test_matrix_rejects_extra_persisted_attempts(tmp_path, monkeypatch):
    """The command must inspect history even when latest views have full coverage."""
    import json
    import sys
    from types import SimpleNamespace

    from alphadiana.results.artifacts import Artifacts
    from alphadiana.engine.config.experiment_config import ExperimentConfig

    cell = {'id': 'cell', 'config': 'config.yaml', 'task_ids': ['a', 'b', 'c']}
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'model': 'fixture-model', 'cells': [cell]}))
    (tmp_path / 'config.yaml').write_text('fixture')
    config = ExperimentConfig('direct_llm', '', 'aime', 'aime',
                              agent_config={'model': 'fixture-model'}, task_retries=0)
    monkeypatch.setattr(matrix.ExperimentConfig, 'from_yaml', lambda _: config)
    monkeypatch.setattr(matrix, 'source_state', lambda: {'commit': 'fixture', 'dirty': False})
    rows = [{'task_id': task, 'sample_index': 0, 'correct': correct, 'score': float(correct),
             'predicted': 'answer'} for correct in [False, True] for task in cell['task_ids']]
    path = tmp_path / 'saved.jsonl'
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    artifacts = Artifacts(path)
    assert len(artifacts.records()) == 3 and len(artifacts.records(latest=False)) == 6
    monkeypatch.setattr(matrix, 'execute', lambda _: artifacts)
    monkeypatch.setattr(matrix.ad, 'analyzer', lambda _: SimpleNamespace(compute=lambda: {}))
    output = tmp_path / 'receipt'
    monkeypatch.setattr(sys, 'argv', ['matrix', str(manifest), '--output', str(output)])
    assert matrix.main() is True
    receipt = json.loads((output / 'receipts.jsonl').read_text())
    assert receipt['status'] == 'failed' and receipt['records'] == 6
