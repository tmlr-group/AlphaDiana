from __future__ import annotations

from pathlib import Path

from alphadiana.analysis.insight_corpus import (
    CORPUS_STATUS_VALUES,
    ENV_CORPUS_VARS,
    CorpusInventoryRow,
    CorpusSpec,
    load_default_corpus_specs,
    load_env_corpus_specs,
    load_phase15_corpus_specs,
    sanitize_path_text,
)


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
