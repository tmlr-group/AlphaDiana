"""Tests for the task dispatcher."""

import pytest

from alphadiana.runner.task_dispatcher import TaskDispatcher


class _FakeTask:
    """Minimal task-like object for testing."""

    def __init__(self, task_id: str):
        self.task_id = task_id


def test_task_dispatcher_sequential():
    """Dispatch tasks sequentially with a mock solve function."""
    tasks = [_FakeTask(f"t{i}") for i in range(5)]
    results_collected = []

    def solve_fn(task):
        result = {"task_id": task.task_id, "answer": 42}
        results_collected.append(result)
        return result

    dispatcher = TaskDispatcher(max_concurrent=1)
    outcomes = dispatcher.dispatch(tasks, solve_fn)

    assert len(outcomes) == 5
    assert all(o["success"] for o in outcomes)
    assert len(results_collected) == 5

    # Verify task_ids are present.
    task_ids = {o["task_id"] for o in outcomes}
    assert task_ids == {"t0", "t1", "t2", "t3", "t4"}


def test_task_dispatcher_concurrent():
    """Dispatch tasks concurrently with max_concurrent=2."""
    tasks = [_FakeTask(f"t{i}") for i in range(6)]

    def solve_fn(task):
        return {"task_id": task.task_id, "answer": int(task.task_id[1:])}

    dispatcher = TaskDispatcher(max_concurrent=2)
    outcomes = dispatcher.dispatch(tasks, solve_fn)

    assert len(outcomes) == 6
    assert all(o["success"] for o in outcomes)

    # All task_ids should be present (order may differ due to concurrency).
    task_ids = {o["task_id"] for o in outcomes}
    assert task_ids == {"t0", "t1", "t2", "t3", "t4", "t5"}


def test_task_dispatcher_error_isolation():
    """Errors in one task should not affect other tasks."""
    tasks = [_FakeTask("ok1"), _FakeTask("fail"), _FakeTask("ok2")]

    def solve_fn(task):
        if task.task_id == "fail":
            raise ValueError("intentional failure")
        return {"task_id": task.task_id}

    dispatcher = TaskDispatcher(max_concurrent=1)
    outcomes = dispatcher.dispatch(tasks, solve_fn)

    assert len(outcomes) == 3
    succeeded = [o for o in outcomes if o["success"]]
    failed = [o for o in outcomes if not o["success"]]
    assert len(succeeded) == 2
    assert len(failed) == 1
    assert failed[0]["task_id"] == "fail"
    assert "intentional failure" in failed[0]["error"]
