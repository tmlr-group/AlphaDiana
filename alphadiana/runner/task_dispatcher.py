"""Task dispatcher for sequential or concurrent task execution."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Any, Callable

logger = logging.getLogger(__name__)


class TaskDispatcher:
    """Dispatches benchmark tasks to a solve function, either sequentially
    or concurrently using a thread pool."""

    def __init__(self, max_concurrent: int = 1, cancel_event: threading.Event | None = None) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self._cancel = cancel_event

    @property
    def cancelled(self) -> bool:
        return self._cancel is not None and self._cancel.is_set()

    def dispatch(
        self,
        tasks: list,
        solve_fn: Callable[[Any], dict],
    ) -> list[dict]:
        """Dispatch all tasks through *solve_fn* and return a list of outcomes.

        Each outcome dict contains:
          - task_id: str
          - success: bool
          - result: <return value of solve_fn> (if success)
          - error: str (if not success)
        """
        if self.max_concurrent == 1:
            return self._dispatch_sequential(tasks, solve_fn)
        return self._dispatch_concurrent(tasks, solve_fn)

    @staticmethod
    def _item_id(item: Any) -> str:
        """Extract a human-readable ID from a work item (task or (task, sample_index) tuple)."""
        if isinstance(item, tuple) and len(item) == 2:
            task, si = item
            tid = getattr(task, "task_id", "?")
            return f"{tid}[s{si}]" if si > 0 else tid
        return getattr(item, "task_id", str(id(item)))

    def _dispatch_sequential(
        self,
        tasks: list,
        solve_fn: Callable[[Any], dict],
    ) -> list[dict]:
        outcomes: list[dict] = []
        total = len(tasks)
        for idx, task in enumerate(tasks, 1):
            if self.cancelled:
                logger.info("Dispatch cancelled, stopping after %d/%d tasks", idx - 1, total)
                break
            task_id = self._item_id(task)
            logger.info("Processing task %s (%d/%d)", task_id, idx, total)
            try:
                result = solve_fn(task)
                outcomes.append({
                    "task_id": task_id,
                    "success": True,
                    "result": result,
                })
            except Exception as exc:
                logger.error("Task %s failed: %s", task_id, exc)
                outcomes.append({
                    "task_id": task_id,
                    "success": False,
                    "error": str(exc),
                })
        return outcomes

    def _dispatch_concurrent(
        self,
        tasks: list,
        solve_fn: Callable[[Any], dict],
    ) -> list[dict]:
        outcomes: list[dict] = []
        total = len(tasks)
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            future_to_task: dict[Future, Any] = {}
            for task in tasks:
                if self.cancelled:
                    logger.info("Dispatch cancelled, not submitting remaining tasks")
                    break
                future = executor.submit(solve_fn, task)
                future_to_task[future] = task

            completed = 0
            for future in as_completed(future_to_task):
                completed += 1
                task = future_to_task[future]
                task_id = self._item_id(task)
                logger.info("Completed task %s (%d/%d)", task_id, completed, total)
                try:
                    result = future.result()
                    outcomes.append({
                        "task_id": task_id,
                        "success": True,
                        "result": result,
                    })
                except Exception as exc:
                    logger.error("Task %s failed: %s", task_id, exc)
                    outcomes.append({
                        "task_id": task_id,
                        "success": False,
                        "error": str(exc),
                    })

                # After collecting each result, check cancellation to stop early
                if self.cancelled:
                    # Cancel pending futures (won't interrupt running ones)
                    for f in future_to_task:
                        f.cancel()
                    logger.info("Dispatch cancelled, skipping remaining tasks")
                    break
        return outcomes
