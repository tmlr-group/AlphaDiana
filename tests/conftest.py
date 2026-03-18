"""Pytest hooks for readable progress output across test files."""

from __future__ import annotations

from pathlib import Path

import pytest

# Exclude standalone scripts from pytest collection.
collect_ignore = ["test_stream.py"]


def pytest_configure(config: pytest.Config) -> None:
    """Initialize file-level progress state for the current test session."""
    config.addinivalue_line(
        "filterwarnings",
        "ignore:Use arun instead:DeprecationWarning",
    )
    config._file_progress = {
        "files": [],
        "current_file": None,
        "current_index": 0,
    }


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Record collected test files in first-seen order."""
    seen: set[str] = set()
    files: list[str] = []
    for item in items:
        file_path = str(Path(item.fspath))
        if file_path in seen:
            continue
        seen.add(file_path)
        files.append(file_path)
    config._file_progress["files"] = files


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    """Print a file progress header before the first test in each file runs."""
    progress = item.config._file_progress
    file_path = str(Path(item.fspath))

    if progress["current_file"] != file_path:
        progress["current_file"] = file_path
        progress["current_index"] += 1
        total_files = len(progress["files"])
        rel_path = Path(file_path).resolve().relative_to(Path.cwd())
        print(
            f"\n[{progress['current_index']}/{total_files}] Running {rel_path}",
            flush=True,
        )

    yield


@pytest.hookimpl(trylast=True)
def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    """Print a concise status line after each test finishes."""
    if report.when not in {"call", "setup"}:
        return
    if report.when == "setup" and not (report.skipped or report.failed):
        return

    if report.passed:
        status = "PASSED"
    elif report.failed:
        status = "FAILED"
    else:
        status = "SKIPPED"

    print(f"  {status:<7} {report.nodeid}", flush=True)
