from pathlib import Path

from alphadiana.analysis.io.result_store import ResultStore
from alphadiana.engine.config.experiment_config import ExperimentConfig
from alphadiana.harness.opencode.agent import _resolve_skill_folder as resolve_opencode_skill
from alphadiana.harness.zeroclaw.agent import _resolve_skill_folder as resolve_zeroclaw_skill


REPO_ROOT = Path(__file__).parents[1]


def test_macro_release_matrix_has_two_benchmarks_and_four_harnesses():
    root = REPO_ROOT / "configs" / "macro_runs"
    paths = sorted(root.glob("*.yaml"))

    assert len(paths) == 8
    configs = [ExperimentConfig.from_yaml(str(path)) for path in paths]
    assert {config.benchmark_name for config in configs} == {"aime", "gpqa_diamond"}
    assert {config.agent_name for config in configs} == {
        "direct_llm",
        "opencode",
        "openclaw",
        "zeroclaw",
    }

    cells = {(config.benchmark_name, config.agent_name) for config in configs}
    assert len(cells) == 8
    assert {config.max_concurrent for config in configs} == {1}
    assert {config.num_samples for config in configs} == {1}
    assert all(config.strict_report for config in configs)
    assert all(config.strict_isolation for config in configs)


def test_release_skill_bare_names_resolve_to_packaged_directories():
    for name in ("advanced-maths", "anthropic-bundle"):
        for resolver in (resolve_opencode_skill, resolve_zeroclaw_skill):
            path = Path(resolver(name) or "")
            assert path == REPO_ROOT / "alphadiana" / "harness" / "skills" / name
            assert (path / "SKILL.md").is_file()


def test_release_docs_only_reference_existing_skill_root():
    paths = [
        REPO_ROOT / "configs" / "micro_runs" / "Skill" / "README.md",
        *sorted((REPO_ROOT / "configs" / "micro_runs" / "Skill").glob("*.yaml")),
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "alphadiana/skills/" not in text


def test_result_store_score_rank_supports_shard_deduplication():
    assert ResultStore._record_score_rank({"score_status": "runtime_error"}) == 0
    assert ResultStore._record_score_rank(
        {"score_status": "runtime_error", "score": 0, "correct": False}
    ) == 1
    assert ResultStore._record_score_rank({"score": 0, "correct": False}) == 2
