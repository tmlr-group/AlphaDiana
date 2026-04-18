from __future__ import annotations

import inspect
from unittest.mock import patch

from click.testing import CliRunner

from alphadiana.benchmark.registry import BenchmarkRegistry
from alphadiana.benchmark.swebench_pro import (
    DEFAULT_SMOKE_INSTANCE_IDS,
    SWEBenchProBenchmark,
)
from alphadiana.cli import main


def _row(
    *,
    instance_id: str,
    repo: str,
    base_commit: str,
    problem_statement: str,
    dockerhub_tag: str,
    fail_to_pass: list[str] | None = None,
    pass_to_pass: list[str] | None = None,
) -> dict:
    return {
        "repo": repo,
        "instance_id": instance_id,
        "base_commit": base_commit,
        "problem_statement": problem_statement,
        "requirements": f"requirements for {instance_id}",
        "interface": "cli",
        "repo_language": "Python",
        "fail_to_pass": fail_to_pass or ["tests/test_issue.py::test_regression"],
        "pass_to_pass": pass_to_pass or ["tests/test_smoke.py::test_existing_behavior"],
        "before_repo_set_cmd": "python -m pip install -e .",
        "selected_test_files_to_run": ["tests/test_issue.py"],
        "dockerhub_tag": dockerhub_tag,
        "patch": "gold patch should never be exposed",
        "test_patch": "gold test patch should never be exposed",
    }


def _dataset_rows() -> list[dict]:
    return [
        _row(
            instance_id=DEFAULT_SMOKE_INSTANCE_IDS[0],
            repo="NodeBB/NodeBB",
            base_commit="abc123",
            problem_statement="Fix NodeBB bug",
            dockerhub_tag="nodebb-tag",
        ),
        _row(
            instance_id=DEFAULT_SMOKE_INSTANCE_IDS[1],
            repo="qutebrowser/qutebrowser",
            base_commit="def456",
            problem_statement="Fix qutebrowser bug",
            dockerhub_tag="qute-tag",
        ),
        _row(
            instance_id=DEFAULT_SMOKE_INSTANCE_IDS[2],
            repo="ansible/ansible",
            base_commit="ghi789",
            problem_statement="Fix ansible bug",
            dockerhub_tag="ansible-tag",
        ),
        _row(
            instance_id="instance_non_smoke_1",
            repo="ansible/ansible",
            base_commit="jkl012",
            problem_statement="Fix non-smoke ansible bug",
            dockerhub_tag="ansible-tag-2",
        ),
        _row(
            instance_id="instance_non_smoke_2",
            repo="gravitational/teleport",
            base_commit="mno345",
            problem_statement="Fix non-smoke teleport bug",
            dockerhub_tag="teleport-tag",
        ),
    ]


class TestSWEBenchProBenchmark:
    def test_default_config_uses_dataset_split_and_smoke_subset(self):
        bench = SWEBenchProBenchmark()
        rows = _dataset_rows()

        with patch("datasets.load_dataset", return_value=rows) as mock_ld:
            tasks = bench.load_tasks({})

        assert mock_ld.call_args.kwargs["split"] == "test"
        assert mock_ld.call_args.args[0] == "ScaleAI/SWE-bench_Pro"
        assert [task.task_id for task in tasks] == DEFAULT_SMOKE_INSTANCE_IDS[:3]

    def test_subset_all_keeps_non_smoke_rows(self):
        bench = SWEBenchProBenchmark()

        with patch("datasets.load_dataset", return_value=_dataset_rows()):
            tasks = bench.load_tasks({"subset": "all"})

        assert len(tasks) == 5
        assert tasks[-1].task_id == "instance_non_smoke_2"

    def test_repo_filter_and_max_tasks_apply_after_subset_selection(self):
        bench = SWEBenchProBenchmark()

        with patch("datasets.load_dataset", return_value=_dataset_rows()):
            tasks = bench.load_tasks({
                "subset": "all",
                "repos": "ansible/ansible",
                "max_tasks": 1,
            })

        assert len(tasks) == 1
        assert tasks[0].metadata["repo"] == "ansible/ansible"
        assert tasks[0].task_id == DEFAULT_SMOKE_INSTANCE_IDS[2]

    def test_instance_id_filter_preserves_requested_order(self):
        bench = SWEBenchProBenchmark()

        with patch("datasets.load_dataset", return_value=_dataset_rows()):
            tasks = bench.load_tasks({
                "subset": "all",
                "instance_ids": ["instance_non_smoke_2", DEFAULT_SMOKE_INSTANCE_IDS[1]],
            })

        assert [task.task_id for task in tasks] == [
            "instance_non_smoke_2",
            DEFAULT_SMOKE_INSTANCE_IDS[1],
        ]

    def test_task_mapping_keeps_safe_metadata_only(self):
        bench = SWEBenchProBenchmark()

        with patch("datasets.load_dataset", return_value=_dataset_rows()):
            tasks = bench.load_tasks({"subset": "all"})

        task = tasks[0]
        assert task.task_id == DEFAULT_SMOKE_INSTANCE_IDS[0]
        assert task.problem == "Fix NodeBB bug"
        assert task.ground_truth == {
            "instance_id": DEFAULT_SMOKE_INSTANCE_IDS[0],
            "repo": "NodeBB/NodeBB",
            "base_commit": "abc123",
        }
        assert task.metadata["dockerhub_tag"] == "nodebb-tag"
        assert task.metadata["fail_to_pass"] == ["tests/test_issue.py::test_regression"]
        assert task.metadata["pass_to_pass"] == ["tests/test_smoke.py::test_existing_behavior"]
        assert "patch" not in task.metadata
        assert "test_patch" not in task.metadata

    def test_smoke_instance_ids_override(self):
        bench = SWEBenchProBenchmark()

        with patch("datasets.load_dataset", return_value=_dataset_rows()):
            tasks = bench.load_tasks({
                "smoke_instance_ids": ["instance_non_smoke_2"],
            })

        assert [task.task_id for task in tasks] == ["instance_non_smoke_2"]


class TestSWEBenchProBenchmarkDiscovery:
    def test_registry_returns_swebench_pro_os_benchmark(self):
        benchmark_cls = BenchmarkRegistry.get("swebench_pro_os")
        assert benchmark_cls is SWEBenchProBenchmark

    def test_cli_list_benchmarks_includes_swebench_pro_os(self):
        runner = CliRunner()
        result = runner.invoke(main, ["list-benchmarks"])
        assert result.exit_code == 0
        assert "swebench_pro_os" in result.output

    def test_benchmark_uses_retry_loader(self):
        source = inspect.getsource(SWEBenchProBenchmark.load_tasks)
        assert "load_dataset_with_retry" in source

    def test_runner_bootstrap_imports_swebench_pro(self):
        with open("alphadiana/runner/runner.py", encoding="utf-8") as f:
            source = f.read()
        assert 'import alphadiana.benchmark.swebench_pro  # noqa: F401' in source
