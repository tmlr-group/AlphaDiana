"""SandboxPool — manages a pool of pre-created sandbox sessions."""

from __future__ import annotations

import threading
from collections import deque
from typing import Any


class SandboxPool:
    """Fixed-size pool of sandbox sessions for concurrent evaluation."""

    def __init__(self, sandbox: Any, size: int) -> None:
        self._sandbox = sandbox
        self._size = size
        self._lock = threading.Lock()
        self._all_sessions: list[Any] = []
        self._available: deque[Any] = deque()
        self._event = threading.Event()

        for _ in range(size):
            session = sandbox.create_session()
            self._all_sessions.append(session)
            self._available.append(session)

        self._event.set()

    @property
    def size(self) -> int:
        return self._size

    @property
    def available(self) -> int:
        with self._lock:
            return len(self._available)

    def acquire(self, timeout: float | None = None) -> Any:
        """Acquire a session from the pool."""
        deadline = None
        if timeout is not None:
            import time
            deadline = time.monotonic() + timeout

        while True:
            with self._lock:
                if self._available:
                    self._event.clear() if len(self._available) == 1 else None
                    return self._available.popleft()
            if deadline is not None:
                import time
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("No sessions available in pool")
                if not self._event.wait(timeout=remaining):
                    raise TimeoutError("No sessions available in pool")
            else:
                self._event.wait()

    def release(self, session: Any) -> None:
        """Return a session to the pool."""
        reset = getattr(session, "reset", None)
        if callable(reset):
            reset()
        with self._lock:
            self._available.append(session)
            self._event.set()

    def teardown(self) -> None:
        """Close all sessions in the pool."""
        for session in self._all_sessions:
            session.close()
