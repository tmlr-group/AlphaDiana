from __future__ import annotations

import csv
import json
from pathlib import Path

from alphadiana.analysis.insight_corpus import (
    CORPUS_STATUS_VALUES,
    ENV_CORPUS_VARS,
    CorpusInventoryRow,
    CorpusSpec,
    build_denominator_ledger,
    inventory_corpus_spec,
    load_default_corpus_specs,
    load_env_corpus_specs,
    load_phase15_corpus_specs,
    load_selected_task_records,
    sanitize_path_text,
    write_denominator_ledger,
)
from alphadiana.analysis.result_reader import load_run_bundle


def test_corpus_contracts_and_status_values_are_locked():
    spec = CorpusSpec(
        label="gpqa-openclaw-v2",
        benchmark="gpqa",
        harness="openclaw",
        model_label="Qwen/Qwen3.5-27B",
        results_dir=Path("results"),
        run_id="full_gpqa_v2_openclaw_qwen35_27b_logprobs",
        source_kind="repo",
        public_ref="results/full_gpqa_v2_openclaw_qwen35_27b_logprobs",
        env_var=None,
    )
    row = CorpusInventoryRow(
        corpus_label=spec.label,
        benchmark=spec.benchmark,
        harness=spec.harness,
        model_label=spec.model_label,
        source_kind=spec.source_kind,
        public_ref=spec.public_ref,
        status="unavailable",
        expected_samples=0,
        task_files=0,
        task_records=0,
        selected_records=0,
        valid_scored=0,
        behavioral_correct=0,
        behavioral_wrong=0,
        error_records=0,
        missing_samples=0,
        status_counts={},
        unavailable_reason="missing run artifacts",
    )

    assert CORPUS_STATUS_VALUES == ("unavailable", "manifest_only", "validated_records")
    assert ENV_CORPUS_VARS == (
        "PHASE15_HLE_OPENCODE_LOGPROBS",
        "PHASE15_HLE_ZEROCLAW_LOGPROBS",
        "PHASE15_COLLAB_RESULTS_ROOT",
    )
    assert spec.label == row.corpus_label
    assert row.status == "unavailable"


def test_default_specs_include_repo_relative_gpqa_roots():
    specs = load_default_corpus_specs(Path("results"))

    assert [(spec.label, spec.harness, spec.results_dir, spec.run_id) for spec in specs] == [
        (
            "gpqa-openclaw-v2",
            "openclaw",
            Path("results"),
            "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
        ),
        (
            "gpqa-opencode-v2",
            "opencode",
            Path("results"),
            "full_gpqa_v2_opencode_qwen35_27b_logprobs",
        ),
        (
            "gpqa-zeroclaw-v2",
            "zeroclaw",
            Path("results"),
            "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
        ),
    ]
    assert all(spec.source_kind == "repo" for spec in specs)
    assert all(not spec.public_ref.startswith("/") for spec in specs)


def test_env_specs_use_sanitized_public_refs():
    env = {
        "PHASE15_HLE_OPENCODE_LOGPROBS": "/data2/private/hle-opencode/run-a",
        "PHASE15_HLE_ZEROCLAW_LOGPROBS": "/home/user/hle-zeroclaw/run-b",
        "PHASE15_COLLAB_RESULTS_ROOT": "/mnt/collab/results/run-c",
    }

    specs = load_env_corpus_specs(env)

    assert [spec.label for spec in specs] == [
        "hle-opencode-env",
        "hle-zeroclaw-env",
        "collab-results-env",
    ]
    assert [spec.public_ref for spec in specs] == [
        "PHASE15_HLE_OPENCODE_LOGPROBS",
        "PHASE15_HLE_ZEROCLAW_LOGPROBS",
        "PHASE15_COLLAB_RESULTS_ROOT",
    ]
    assert all(spec.results_dir.is_absolute() for spec in specs)
    assert all("/data" not in spec.public_ref and "/home" not in spec.public_ref for spec in specs)


def test_phase15_specs_include_hf_synced_public_dataset_when_present(tmp_path):
    results_dir = tmp_path / "results"
    (results_dir / "hf-alphadiana-benchmark-results" / "pilot_run").mkdir(parents=True)

    specs = load_phase15_corpus_specs(results_dir, env={}, include_hf_synced=True)

    hf_specs = [spec for spec in specs if spec.source_kind == "hf_synced"]
    assert hf_specs
    assert all(spec.public_ref == "T-MARS/alphadiana-benchmark-results" for spec in hf_specs)
    assert all(str(results_dir) not in spec.public_ref for spec in hf_specs)


def test_sanitize_path_text_removes_local_cache_paths_and_absolute_roots():
    text = (
        "/data0/models/hub/datasets--T-MARS--alphadiana-benchmark-results/snapshots/abc/run "
        "/data0/models/hub/datasets--cais--hle/snapshots/def "
        "/data2/private/file /home/user/file /tmp/file /mnt/team/file /scratch/job"
    )

    sanitized = sanitize_path_text(text)

    assert "T-MARS/alphadiana-benchmark-results" in sanitized
    assert "cais/hle local cache" in sanitized
    assert "/data0" not in sanitized
    assert "/data2" not in sanitized
    assert "/home" not in sanitized
    assert "/tmp" not in sanitized
    assert "/mnt" not in sanitized
    assert "/scratch" not in sanitized


def _write_run(
    results_dir: Path,
    run_id: str,
    *,
    manifest: dict | None = None,
    records: list[dict] | None = None,
    task_records: dict[str, list[dict]] | None = None,
) -> None:
    run_dir = results_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (run_dir / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if records is not None:
        (results_dir / f"{run_id}.jsonl").write_text(
            "\n".join(json.dumps(record) for record in records),
            encoding="utf-8",
        )
    if task_records is not None:
        tasks_dir = run_dir / "tasks"
        tasks_dir.mkdir(exist_ok=True)
        for task_file, samples in task_records.items():
            (tasks_dir / f"{task_file}.json").write_text(json.dumps(samples), encoding="utf-8")


def _spec(results_dir: Path, run_id: str = "run-a") -> CorpusSpec:
    return CorpusSpec(
        label=run_id,
        benchmark="gpqa",
        harness="opencode",
        model_label="Qwen/Qwen3.5-27B",
        results_dir=results_dir,
        run_id=run_id,
        source_kind="repo",
        public_ref=f"results/{run_id}",
        env_var=None,
    )


def test_task_json_sample_list_selects_first_record(tmp_path):
    results_dir = tmp_path / "results"
    _write_run(
        results_dir,
        "run-a",
        manifest={"run_id": "run-a", "expected_sample_count": 1},
        task_records={
            "gpqa_1": [
                {"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": True},
                {"task_id": "gpqa_1", "sample_index": 1, "score_status": "valid_scored", "correct": False},
            ]
        },
    )

    bundle = load_run_bundle(results_dir, "run-a")
    records = load_selected_task_records(bundle)
    row = inventory_corpus_spec(_spec(results_dir))

    assert [(record["task_id"], record["sample_index"], record["correct"]) for record in records] == [
        ("gpqa_1", 0, True)
    ]
    assert row.task_records == 1
    assert row.selected_records == 1
    assert row.valid_scored == 1


def test_duplicate_checkpoint_valid_scored_replaces_preserved_error(tmp_path):
    results_dir = tmp_path / "results"
    _write_run(
        results_dir,
        "run-a",
        manifest={"run_id": "run-a", "expected_sample_count": 1},
        task_records={
            "attempt_1": [
                {"task_id": "gpqa_1", "sample_index": 0, "score_status": "runtime_error", "correct": None}
            ],
            "attempt_2": [
                {"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": False}
            ],
        },
    )

    records = load_selected_task_records(load_run_bundle(results_dir, "run-a"))
    row = inventory_corpus_spec(_spec(results_dir))

    assert [record["score_status"] for record in records] == ["valid_scored"]
    assert row.selected_records == 1
    assert row.valid_scored == 1
    assert row.error_records == 0
    assert row.behavioral_wrong == 1


def test_denominator_ledger_separates_valid_errors_missing_and_wrong(tmp_path):
    results_dir = tmp_path / "results"
    _write_run(
        results_dir,
        "run-a",
        manifest={"run_id": "run-a", "expected_sample_count": 5, "expected_task_count": 5, "num_samples": 1},
        task_records={
            "gpqa_1": [{"task_id": "gpqa_1", "sample_index": 0, "score_status": "valid_scored", "correct": True}],
            "gpqa_2": [{"task_id": "gpqa_2", "sample_index": 0, "score_status": "valid_scored", "correct": False}],
            "gpqa_3": [{"task_id": "gpqa_3", "sample_index": 0, "score_status": "provider_error", "correct": None}],
            "gpqa_4": [{"task_id": "gpqa_4", "sample_index": 0, "score_status": "scorer_error", "correct": None}],
        },
    )

    rows = build_denominator_ledger([_spec(results_dir)])

    assert rows == [
        {
            "corpus_label": "run-a",
            "benchmark": "gpqa",
            "harness": "opencode",
            "model_label": "Qwen/Qwen3.5-27B",
            "source_kind": "repo",
            "public_ref": "results/run-a",
            "status": "validated_records",
            "expected_samples": 5,
            "task_files": 4,
            "task_records": 4,
            "selected_records": 4,
            "valid_scored": 2,
            "behavioral_correct": 1,
            "behavioral_wrong": 1,
            "error_records": 2,
            "missing_samples": 1,
            "status_counts": {"provider_error": 1, "scorer_error": 1, "valid_scored": 2},
            "unavailable_reason": None,
        }
    ]


def test_unavailable_root_is_inventory_row_not_exception(tmp_path):
    row = inventory_corpus_spec(_spec(tmp_path / "missing-results"))

    assert row.status == "unavailable"
    assert row.expected_samples == 0
    assert row.selected_records == 0
    assert row.missing_samples == 0
    assert row.unavailable_reason == "missing run artifacts"


def test_manifest_only_root_has_no_behavior_claims(tmp_path):
    results_dir = tmp_path / "results"
    _write_run(
        results_dir,
        "run-a",
        manifest={"run_id": "run-a", "expected_task_count": 3, "num_samples": 2},
    )

    row = inventory_corpus_spec(_spec(results_dir))

    assert row.status == "manifest_only"
    assert row.expected_samples == 6
    assert row.task_files == 0
    assert row.selected_records == 0
    assert row.valid_scored == 0
    assert row.behavioral_correct == 0
    assert row.behavioral_wrong == 0
    assert row.error_records == 0
    assert row.missing_samples == 6


def test_sanitize_path_text_removes_absolute_paths_and_secret_markers():
    text = (
        "path=/data2/private/root api_key=abc123 "
        "home=/home/user/.config token=sk-testSECRET123 /tmp/cache /mnt/share /scratch/job"
    )

    sanitized = sanitize_path_text(text)

    assert "/data2" not in sanitized
    assert "/home" not in sanitized
    assert "/tmp" not in sanitized
    assert "/mnt" not in sanitized
    assert "/scratch" not in sanitized
    assert "sk-testSECRET123" not in sanitized
    assert "api_key=abc123" not in sanitized


def test_write_denominator_ledger_writes_json_and_csv(tmp_path):
    rows = [
        {
            "corpus_label": "b",
            "status": "unavailable",
            "status_counts": {},
        },
        {
            "corpus_label": "a",
            "status": "validated_records",
            "status_counts": {"valid_scored": 1},
        },
    ]

    paths = write_denominator_ledger(tmp_path / "out", rows)

    assert set(paths) == {"json", "csv"}
    assert json.loads(paths["json"].read_text(encoding="utf-8")) == rows
    with paths["csv"].open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert [row["corpus_label"] for row in csv_rows] == ["b", "a"]
