"""Plain-text dashboard for real-time evaluation status."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


class PlainTextDashboard:
    """Writes a plain-text status file showing per-task O/X marks.

    Format::

        Last updated: 2026-03-12 14:30:00
        Problem t1: OOX (0 left)
        Problem t2: O-- (2 left)
    """

    def __init__(self, path: str | Path, tasks: list[Any], samples_per_task: int = 1) -> None:
        self._path = Path(path)
        self._samples = samples_per_task
        # Deduplicate task_ids while preserving order.
        seen: set[str] = set()
        unique_ids: list[str] = []
        for t in tasks:
            tid = getattr(t, "task_id", str(t))
            if tid not in seen:
                seen.add(tid)
                unique_ids.append(tid)
        self._task_ids: list[str] = unique_ids
        self._results: dict[str, list[bool]] = {tid: [] for tid in self._task_ids}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._write()

    def update(self, task_id: str, correct: bool) -> None:
        """Record a sample result for *task_id* and rewrite the file."""
        if task_id not in self._results:
            self._results[task_id] = []
        self._results[task_id].append(correct)
        self._write()

    def _write(self) -> None:
        lines: list[str] = []
        lines.append(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        for tid in self._task_ids:
            marks = self._results.get(tid, [])
            mark_str = "".join("O" if c else "X" for c in marks)
            remaining = max(0, self._samples - len(marks))
            mark_str += "-" * remaining
            lines.append(f"Problem {tid}: {mark_str} ({remaining} left)")
        self._path.write_text("\n".join(lines) + "\n", encoding="utf-8")
