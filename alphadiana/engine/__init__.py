"""Execution engine public API.

Runner imports the reporting and analysis stack. Keep these exports lazy so
configuration-only commands do not initialize optional runtime-heavy modules.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from alphadiana.engine.runner import Runner
    from alphadiana.engine.task_dispatcher import TaskDispatcher

__all__ = ["Runner", "TaskDispatcher"]


def __getattr__(name: str) -> Any:
    if name == "Runner":
        from alphadiana.engine.runner import Runner

        return Runner
    if name == "TaskDispatcher":
        from alphadiana.engine.task_dispatcher import TaskDispatcher

        return TaskDispatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
