from __future__ import annotations

from pathlib import Path

import pytest

from alphadiana.benchmark.terminal_bench2 import TerminalBench2Benchmark


def _write_task(root: Path, name: str, image: str) -> None:
    task_dir = root / name
    task_dir.mkdir(parents=True)
    (task_dir / "instruction.md").write_text(f"Solve {name}\n", encoding="utf-8")
    (task_dir / "task.toml").write_text(
        f"""
version = "1.0"

[metadata]
category = "smoke"
difficulty = "easy"

[environment]
docker_image = "{image}"
""".strip(),
        encoding="utf-8",
    )


def test_terminal_bench2_task_ids_select_official_task_metadata(tmp_path: Path) -> None:
    _write_task(tmp_path, "db-wal-recovery", "alexgshaw/db-wal-recovery:20251031")
    _write_task(tmp_path, "other-task", "example/other:latest")

    tasks = TerminalBench2Benchmark().load_tasks({
        "tasks_dir": str(tmp_path),
        "task_ids": ["tb2_db-wal-recovery"],
    })

    assert [task.task_id for task in tasks] == ["tb2_db-wal-recovery"]
    assert tasks[0].metadata["docker_image"] == "alexgshaw/db-wal-recovery:20251031"


def test_terminal_bench2_task_ids_raise_when_missing(tmp_path: Path) -> None:
    _write_task(tmp_path, "other-task", "example/other:latest")

    with pytest.raises(ValueError, match="db-wal-recovery"):
        TerminalBench2Benchmark().load_tasks({
            "tasks_dir": str(tmp_path),
            "task_ids": ["db-wal-recovery"],
        })
