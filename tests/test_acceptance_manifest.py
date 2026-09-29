"""Acceptance fixtures detect content drift and cover the agreed full matrix."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from alphadiana.benchmarks.base import Benchmark, BenchmarkTask
from alphadiana.benchmarks.registry import BenchmarkRegistry
from scripts.validate_api_matrix import task_fingerprint, validate_inputs

ROOT = Path(__file__).resolve().parents[1] / "configs/acceptance/api"


def test_manifest_covers_all_required_combinations():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    fixtures = json.loads((ROOT / "fixtures.json").read_text())
    expected = {
        (b, h)
        for b in ["aime2026", "gpqa", "hle", "imo_answerbench", "mmmu_pro"]
        for h in ["directllm", "openclaw", "zeroclaw", "opencode"]
    }
    expected |= {
        ("swe_mini", h) for h in ["sweagent", "openclaw", "zeroclaw", "opencode"]
    }
    expected |= {
        ("terminal_bench2", h)
        for h in ["terminus2", "openclaw", "zeroclaw", "opencode"]
    }
    assert len(manifest["cells"]) == manifest["expected_cells"] == 28
    assert {c["id"] for c in manifest["cells"]} == {f"{b}_{h}" for b, h in expected}
    for cell in manifest["cells"]:
        assert (ROOT / cell["config"]).is_file()
        assert (
            len(cell["task_ids"])
            == len(set(cell["task_ids"]))
            == cell["expected_tasks"]
            == 3
        )
        assert cell["task_ids"] == list(fixtures[cell["fixture"]]["task_fingerprints"])


def test_fixture_detects_changed_inputs_before_execution(tmp_path, monkeypatch):
    task = BenchmarkTask(
        "fixture", "Question", "Answer", {"test_patch": "original"}, {"image": b"image"}
    )
    fixture = {"metadata_fields": ["test_patch"]}
    fixture["task_fingerprints"] = {task.task_id: task_fingerprint(task, fixture)}

    class FixtureBenchmark(Benchmark):
        def load_tasks(self, config):
            return [task]

    monkeypatch.setitem(
        BenchmarkRegistry._registry, "acceptance_fixture", FixtureBenchmark
    )
    config = SimpleNamespace(benchmark_name="acceptance_fixture", benchmark_config={})
    cell = {"task_ids": ["fixture"]}
    assert validate_inputs(config, cell, fixture) == fixture["task_fingerprints"]
    task.attachments["image"] = b"changed"
    with pytest.raises(ValueError, match="input content"):
        validate_inputs(config, cell, fixture)


def test_task_asset_hash_is_portable_and_rejects_symlinks(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    for root in (first, second):
        (root / "test.sh").write_text("exit 0")
    fixture = {"tree_metadata_field": "task_dir"}
    a = BenchmarkTask("task", "Question", "1", {"task_dir": str(first)})
    b = BenchmarkTask("task", "Question", "1", {"task_dir": str(second)})
    assert task_fingerprint(a, fixture) == task_fingerprint(b, fixture)
    (second / "test.sh").write_text("exit 1")
    assert task_fingerprint(a, fixture) != task_fingerprint(b, fixture)
    (second / "link").symlink_to(first / "test.sh")
    with pytest.raises(ValueError, match="symlinks"):
        task_fingerprint(b, fixture)


def test_all_cell_configs_pass_normal_runner_validation(monkeypatch, tmp_path):
    import re

    from alphadiana.engine.components import register_builtin_components
    from alphadiana.engine.config.experiment_config import ExperimentConfig
    from alphadiana.engine.config.validator import ConfigValidator

    register_builtin_components()
    manifest = json.loads((ROOT / "manifest.json").read_text())
    for cell in manifest["cells"]:
        path = ROOT / cell["config"]
        for name in re.findall(r"\$\{([^}]+)\}", path.read_text()):
            monkeypatch.setenv(name, str(tmp_path / name))
            if name == "AD_ACCEPTANCE_TB2_DIR":
                (tmp_path / name).mkdir(exist_ok=True)
        config = ExperimentConfig.from_yaml(path)
        assert ConfigValidator().validate(config) == [], cell["id"]
