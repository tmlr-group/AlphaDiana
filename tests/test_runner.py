"""Tests for TaskDispatcher and Runner."""

import pytest

from alphadiana.runner.task_dispatcher import TaskDispatcher


class _FakeTask:
    """Minimal task-like object for testing."""

    def __init__(self, task_id: str):
        self.task_id = task_id


class TestTaskDispatcherSequential:
    """验证 TaskDispatcher 顺序执行。"""

    def test_dispatch_all_success(self):
        """所有任务成功执行。"""
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

        task_ids = {o["task_id"] for o in outcomes}
        assert task_ids == {"t0", "t1", "t2", "t3", "t4"}

    def test_dispatch_preserves_order(self):
        """顺序执行时保持任务顺序。"""
        tasks = [_FakeTask(f"t{i}") for i in range(3)]
        order = []

        def solve_fn(task):
            order.append(task.task_id)
            return {"task_id": task.task_id}

        dispatcher = TaskDispatcher(max_concurrent=1)
        dispatcher.dispatch(tasks, solve_fn)

        assert order == ["t0", "t1", "t2"]


class TestTaskDispatcherConcurrent:
    """验证 TaskDispatcher 并发执行。"""

    def test_dispatch_concurrent(self):
        """并发执行所有任务完成。"""
        tasks = [_FakeTask(f"t{i}") for i in range(6)]

        def solve_fn(task):
            return {"task_id": task.task_id, "answer": int(task.task_id[1:])}

        dispatcher = TaskDispatcher(max_concurrent=2)
        outcomes = dispatcher.dispatch(tasks, solve_fn)

        assert len(outcomes) == 6
        assert all(o["success"] for o in outcomes)

        task_ids = {o["task_id"] for o in outcomes}
        assert task_ids == {"t0", "t1", "t2", "t3", "t4", "t5"}


class TestTaskDispatcherErrorIsolation:
    """验证错误隔离：一个任务失败不影响其他任务。"""

    def test_error_isolation(self):
        """失败任务不影响成功任务。"""
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

    def test_error_isolation_concurrent(self):
        """并发模式下错误隔离。"""
        tasks = [_FakeTask("ok1"), _FakeTask("fail"), _FakeTask("ok2")]

        def solve_fn(task):
            if task.task_id == "fail":
                raise RuntimeError("boom")
            return {"task_id": task.task_id}

        dispatcher = TaskDispatcher(max_concurrent=3)
        outcomes = dispatcher.dispatch(tasks, solve_fn)

        assert len(outcomes) == 3
        succeeded = [o for o in outcomes if o["success"]]
        failed = [o for o in outcomes if not o["success"]]
        assert len(succeeded) == 2
        assert len(failed) == 1

    def test_empty_tasks(self):
        """空任务列表返回空结果。"""
        dispatcher = TaskDispatcher(max_concurrent=1)
        outcomes = dispatcher.dispatch([], lambda t: {})
        assert outcomes == []

    def test_max_concurrent_clamped(self):
        """max_concurrent < 1 被钳制为 1。"""
        dispatcher = TaskDispatcher(max_concurrent=0)
        assert dispatcher.max_concurrent == 1
        dispatcher2 = TaskDispatcher(max_concurrent=-1)
        assert dispatcher2.max_concurrent == 1
