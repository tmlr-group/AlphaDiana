"""Cross-entry-point contracts for #109, including supported legacy imports."""

import importlib
import json
import subprocess
import sys
import tracemalloc
from copy import deepcopy
from types import SimpleNamespace

import pytest

from alphadiana.analysis.measurements import ExecutionMetrics
from alphadiana.dashboard.backend.data_loader import _build_summary
from alphadiana.results.store import ResultStore
from alphadiana.results.statistics import compute_reliability_summary
from alphadiana.analysis.export import ReportGenerator
from alphadiana.results.statistics import RunStatistics, latest_samples
from alphadiana.artifacts import Artifacts

FAMILIES = [
    ("benchmarks", "Benchmark", "benchmark"),
    ("harness", "Agent", "agent"),
    ("engine.sandbox", "Sandbox", "sandbox"),
    ("scorer", "Scorer", "scorer"),
    ("metrics", "Metric", "metric"),
]


@pytest.mark.parametrize("path,family,arg", FAMILIES)
def test_registry_identity_keywords_and_replacement(path, family, arg):
    canonical = importlib.import_module("alphadiana.registry")
    legacy = importlib.import_module(f"alphadiana.{path}.registry")
    registry = getattr(canonical, family + "Registry")
    assert registry is getattr(legacy, family + "Registry")
    decorator = getattr(canonical, "register_" + arg)
    assert decorator is getattr(legacy, "register_" + arg)
    before = dict(registry._registry)
    try:
        registry.register("__test_shared", **{arg + "_cls": type("Original", (), {})})
        replacement = type("Replacement", (), {})
        assert decorator("__test_shared")(replacement) is replacement
        assert registry.get("__test_shared") is replacement
        assert registry.list() == sorted(registry._registry)
        with pytest.raises(KeyError, match=family):
            registry.get("__not_registered")
    finally:
        registry._registry.clear()
        registry._registry.update(before)


@pytest.mark.parametrize(
    "first", ["alphadiana.registry", "alphadiana.harness.registry"]
)
def test_fresh_process_discovery_isolated_families_and_optional_imports(first):
    code = f"""
import importlib, sys
importlib.import_module({first!r})
from alphadiana import registry as r
assert not any(name in sys.modules for name in (
    'alphadiana.harness.direct_llm', 'alphadiana.harness.pi.agent',
    'alphadiana.engine.sandbox.podman', 'docker', 'harbor', 'swebench'))
families = [r.BenchmarkRegistry, r.AgentRegistry, r.SandboxRegistry, r.ScorerRegistry, r.MetricRegistry]
assert len({{id(f._registry) for f in families}}) == 5
for i, family in enumerate(families):
    family.register('__shared_key', type('Impl' + str(i), (), {{}}))
assert len({{f.get('__shared_key') for f in families}}) == 5
from alphadiana.registry_loader import load_builtin_components, load_builtin_benchmarks
load_builtin_benchmarks()
assert '__shared_key' in r.BenchmarkRegistry.list()
load_builtin_components()
before = [dict(f._registry) for f in families]
replacement = type('Replacement', (), {{}})
r.AgentRegistry.register('direct_llm', replacement)
load_builtin_components()
assert r.AgentRegistry.get('direct_llm') is replacement
assert before[0] == r.BenchmarkRegistry._registry
from click.testing import CliRunner
from alphadiana.cli import main
result = CliRunner().invoke(main, ['list-benchmarks'])
assert result.exit_code == 0, result.output
assert 'aime' in result.output and '__shared_key' in result.output
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_logprob_attribution_and_sdk_equivalence():
    from alphadiana import logprobs as canonical

    raw = dict(
        token_index=3,
        token="α",
        logprob=-0.2,
        top_logprobs=[dict(token="α", logprob=-0.2), dict(token="β", logprob=-2.6)],
        call_id="call-2",
        call_index=2,
        call_token_index=0,
        follows_tool_result=True,
    )
    original = deepcopy(raw)
    compact = canonical.quantize_record_int16(raw)
    assert compact == canonical.raw_record_to_int16_record(raw)
    for key in ("call_id", "call_index", "call_token_index", "follows_tool_result"):
        assert compact[key] == raw[key]
    assert raw == original
    assert compact["entropy_nats"] == pytest.approx(
        canonical._compute_entropy(raw["top_logprobs"])
    )
    sdk = canonical.top_logprob_to_int16_record(SimpleNamespace(**raw), 3)
    assert sdk == {
        key: compact[key] for key in ("token_index", "token", "top20", "entropy_nats")
    }
    assert canonical._compute_entropy_stats(
        [raw]
    ) == canonical.entropy_stats_from_int16_records([compact])


@pytest.mark.parametrize("named_tasks", [True, False])
def test_all_consumers_share_retries_denominators_and_distinct_units(
    tmp_path, named_tasks
):
    def row(task, sample, correct=False, **extra):
        return dict(
            task_id=task,
            sample_index=sample,
            score=int(correct),
            correct=correct,
            wall_time_sec=2,
            token_usage={"completion_tokens": 4},
            **extra,
        )

    rows = [
        row("a", 0, True),
        row(
            "a",
            0,
            False,
            error={"error_type": "RuntimeError", "message": "failed retry"},
        ),
        row("a", 1),
        row("b", 0, True),
        row(
            "b",
            1,
            metadata={"metric_contribution": 0},
            score_status="agent_empty_output",
        ),
    ]
    original = deepcopy(rows)
    manifest = dict(expected_task_count=3, num_samples=2, expected_sample_count=6)
    if named_tasks:
        manifest["expected_task_ids"] = ["a", "b", "missing"]
    store = ResultStore(str(tmp_path), "run")
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf8")
    store.save_manifest(manifest)
    artifacts = Artifacts(path)
    assert len(artifacts.records(latest=False)) == 5
    assert len(artifacts.records()) == len(store.load()) == 4
    assert store.load() == latest_samples(rows) == artifacts.records()
    core = ReportGenerator().summarize(rows, manifest)
    dashboard = _build_summary("run", rows, manifest)
    reliability = compute_reliability_summary(rows, manifest)
    measured = ExecutionMetrics().measure([artifacts])
    execution = measured.tables["performance"][0]
    assert rows == original
    assert (core.total_tasks, core.completed, core.failed) == (6, 2, 4)  # samples
    assert (dashboard.total_tasks, dashboard.completed, dashboard.failed) == (
        3,
        2,
        1,
    )  # tasks
    assert (
        core.accuracy == dashboard.accuracy == pytest.approx(1 / 3)
    )  # valid + explicit zero
    assert reliability["observed_valid_accuracy"] == 0.5  # only valid
    assert (
        core.accuracy_total
        == dashboard.accuracy_total
        == execution["accuracy"]
        == pytest.approx(1 / 6)
    )
    for summary in (reliability, execution):
        assert summary["pass_at_k"] == core.pass_at_k == pytest.approx(1 / 3)
        assert summary["avg_at_k"] == core.avg_at_k == pytest.approx(1 / 6)
    assert reliability["coverage"] == pytest.approx(4 / 6)
    assert (
        core.missing_samples
        == reliability["missing_samples"]
        == measured.coverage["run"]["missing_samples"]
        == 2
    )
    assert core.missing_tasks == 1
    assert not reliability["pass_power_k_available"]
    assert "actions" not in measured.tables


def test_legacy_k_missing_sample_keys_and_error_after_success():
    rows = [dict(task_id="a", sample_index=1, score=1, correct=True, num_samples=4)]
    base = RunStatistics.from_records(rows)
    assert base.num_samples == 4 and base.expected_sample_count == 4
    assert base.missing_samples == 3 and base.avg_at_k == 0.25
    rows.append(dict(task_id="a", sample_index=1, error={"error_type": "RuntimeError"}))
    stats = compute_reliability_summary(rows, {})
    assert stats["written_records"] == 1 and stats["valid_scored"] == 0
    assert stats["pass_at_k"] == 0


@pytest.mark.parametrize(
    "manifest,rows,missing",
    [
        ({"expected_sample_count": 2}, [{"task_id": "a"}], 1),
        ({"expected_sample_count": 2}, [], 2),
        ({"expected_sample_count": 2}, [{"task_id": "a"}] * 3, 1),
        ({"expected_sample_count": 4, "num_samples": 2}, [{"task_id": "a"}], 3),
        (
            {"expected_task_ids": ["a", "b"], "expected_sample_count": 2},
            [{"task_id": "a", "sample_index": 1}, {"task_id": "other"}],
            2,
        ),
    ],
)
def test_legacy_explicit_sample_count_preserves_missing_coverage(
    tmp_path, manifest, rows, missing
):
    rows = [dict(score=1, correct=True, **row) for row in rows]
    store = ResultStore(str(tmp_path), "run")
    path = tmp_path / "run.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf8")
    store.save_manifest(manifest)
    core = ReportGenerator().summarize(rows, manifest)
    dashboard = _build_summary("run", rows, manifest)
    reliability = compute_reliability_summary(rows, manifest)
    coverage = ExecutionMetrics().measure([Artifacts(path)]).coverage["run"]
    assert core.missing_samples == dashboard.missing_samples == missing
    assert reliability["missing_samples"] == coverage["missing_samples"] == missing
    assert coverage["requested"] == manifest["expected_sample_count"]
    assert coverage["observed"] == len(latest_samples(rows))
    assert not reliability["pass_power_k_available"]


def test_result_store_deduplicates_large_attempt_history_while_reading(
    tmp_path, caplog
):
    # Constructed regression: old attempts must not all stay resident during load.
    store = ResultStore(str(tmp_path), "run")
    sample_count, attempts, output_bytes = 100, 4, 64 * 1024
    with open(store.path, "w", encoding="utf8") as stream:
        for attempt in range(attempts):
            for sample in range(sample_count):
                row = dict(
                    task_id=str(sample),
                    raw_output="x" * output_bytes,
                    score=1,
                    correct=True,
                    attempt=attempt,
                )
                if attempt == attempts - 1:
                    row["error"] = {"error_type": "RuntimeError"}
                stream.write(json.dumps(row) + "\n")
        stream.write("\n{incomplete\n")
    tracemalloc.start()
    try:
        rows = store.load()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(rows) == sample_count
    assert all(row["attempt"] == attempts - 1 for row in rows)
    assert all(row["score_status"] == "runtime_error" for row in rows)
    assert [row["task_id"] for row in rows] == list(map(str, range(sample_count)))
    assert "Skipping malformed JSONL line" in caplog.text
    # Allow over 2x the retained payload for decoder/container overhead; the
    # four-attempt history alone is 25 MiB and must not fit under this bound.
    assert peak < sample_count * output_bytes * 2.5
    assert latest_samples(iter(rows)) == rows


def test_retained_rule_method_is_available_but_never_default(tmp_path):
    from alphadiana.analysis.actions.llm_annotations import ActionAnnotations
    from alphadiana.analysis.actions.llm_annotations import character_targets
    from alphadiana.analysis.actions import keyword_baseline as action_events
    from alphadiana.analysis.actions.llm_annotations import (
        TRAJECTORY_PROTOCOL as PROTOCOL,
    )

    text = "a" * 4100
    spans = character_targets([dict(role="assistant", content=text)], 4096)
    assert [len(evidence["target"]) for evidence, _ in spans] == [4096, 4]
    assert callable(action_events.extract_action_event_rows)
    options = dict(
        cache_dir=tmp_path / "cache",
        model="fixture",
        base_url="http://127.0.0.1:1/v1",
        api_key="fixture",
    )
    with ActionAnnotations(**options) as current:
        assert current.protocol == PROTOCOL
    with ActionAnnotations(
        protocol="custom-spans-v1",
        span_chars=4096,
        taxonomy={"reason": "Historical reasoning"},
        **options,
    ) as legacy:
        assert legacy.name == "legacy_custom_annotated_actions"
        assert legacy._custom_targets([dict(role="assistant", content=text)]) == spans
