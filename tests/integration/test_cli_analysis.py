"""CLI analysis shares API measurements, export protection and judge behavior."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

import alphadiana as ad
from alphadiana.cli import main


@pytest.fixture
def artifacts(tmp_path):
    path = tmp_path / "saved.jsonl"
    path.write_text(json.dumps({
        "task_id": "one", "correct": True, "score": 1,
        "wall_time_sec": 2, "token_usage": {"completion_tokens": 8},
    }) + "\n")
    return path


def test_analyzer_json_matches_api_without_enabling_judge(artifacts, monkeypatch):
    monkeypatch.setenv("ANALYSIS_API_KEY", "not-an-opt-in")
    monkeypatch.setenv("ANALYSIS_BASE_URL", "http://127.0.0.1:1")
    result = CliRunner().invoke(main, ["analyzer", str(artifacts)])
    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.output) == ad.analyzer(artifacts).compute()


def test_analyzer_comparison_exports_same_report_as_api(artifacts, tmp_path):
    other = tmp_path / "other.jsonl"
    other.write_text(artifacts.read_text().replace('"correct": true', '"correct": false'))
    output = tmp_path / "figures"
    result = CliRunner().invoke(main, [
        "analyzer", str(artifacts), str(other),
        "--output-dir", str(output), "--formats", "svg", "--formats", "png",
    ])
    assert result.exit_code == 0, (result.output, result.exception)
    report = json.loads((output / "report.json").read_text())
    assert report == ad.analyzer([artifacts, other]).compute()
    receipt = json.loads(result.output)
    assert {Path(p).suffix for p in receipt["files"]} == {".svg", ".png"}
    assert (output / "execution-accuracy.svg").read_text().find("<svg") >= 0
    assert (output / "execution-accuracy.png").read_bytes().startswith(b"\x89PNG")
    original = (output / "report.json").read_bytes()
    repeated = CliRunner().invoke(main, [
        "analyzer", str(artifacts), "--output-dir", str(output),
    ])
    assert repeated.exit_code == 1
    assert "new or empty export directory" in repeated.output
    assert (output / "report.json").read_bytes() == original


def test_analyzer_rejects_duplicate_runs_and_bad_json(artifacts, tmp_path):
    runner = CliRunner()
    duplicate = runner.invoke(main, ["analyzer", str(artifacts), str(artifacts)])
    assert duplicate.exit_code == 1
    assert "Duplicate artifact sources" in duplicate.output
    same_name = tmp_path / "elsewhere" / artifacts.name
    same_name.parent.mkdir()
    same_name.write_text(artifacts.read_text())
    ambiguous = runner.invoke(main, ["analyzer", str(artifacts), str(same_name)])
    assert ambiguous.exit_code == 1
    assert "run names must be unique" in ambiguous.output
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"task_id":')
    malformed = runner.invoke(main, ["analyzer", str(bad)])
    assert malformed.exit_code == 1
    assert "Invalid artifact record" in malformed.output


@pytest.mark.parametrize("arguments", [
    ["--formats", "svg"], ["--output-dir", "figures", "--formats", "invalid"],
])
def test_analyzer_invalid_export_options_fail_before_writing(artifacts, arguments):
    result = CliRunner().invoke(main, ["analyzer", str(artifacts), *arguments])
    assert result.exit_code == 2


def test_analyzer_judge_options_and_provider_failure(artifacts, tmp_path, monkeypatch):
    received = {}

    def configured(paths, **options):
        received.update(paths=paths, **options)
        return SimpleNamespace(compute=lambda: {"annotations": True})

    monkeypatch.setattr(ad, "analyzer", configured)
    result = CliRunner().invoke(main, [
        "analyzer", str(artifacts), "--judge", "--model", "analysis-fixture",
        "--base-url", "http://127.0.0.1:1/v1", "--cache-dir", str(tmp_path / "cache"),
    ])
    assert result.exit_code == 0
    assert received == {
        "paths": (str(artifacts),), "judge": True, "model": "analysis-fixture",
        "base_url": "http://127.0.0.1:1/v1", "api_key": None,
        "cache_dir": str(tmp_path / "cache"),
    }

    def failed(*args, **kwargs):
        raise RuntimeError("analysis provider timed out")

    monkeypatch.setattr(ad, "analyzer", failed)
    result = CliRunner().invoke(main, ["analyzer", str(artifacts), "--judge"])
    assert result.exit_code == 1
    assert "analysis provider timed out" in result.output
