"""SandboxPool — manages a pool of pre-created sandbox sessions."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)


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
        """Return a session to the pool, replacing it if reset fails."""
        try:
            reset = getattr(session, "reset", None)
            if callable(reset):
                reset()
        except Exception as exc:
            logger.warning("Session reset failed, destroying and replacing: %s", exc)
            old_session = session
            try:
                old_session.close()
            except Exception:
                logger.warning("Failed to close broken session", exc_info=True)
            # Remove the old (closed) session from _all_sessions.
            with self._lock:
                try:
                    self._all_sessions.remove(old_session)
                except ValueError:
                    pass
            # Create a replacement session to keep pool size stable.
            try:
                session = self._sandbox.create_session()
                with self._lock:
                    self._all_sessions.append(session)
            except Exception:
                logger.error("Failed to create replacement session", exc_info=True)
                # Even if replacement fails, unblock waiting threads.
                with self._lock:
                    self._event.set()
                return
        with self._lock:
            self._available.append(session)
            self._event.set()

    def teardown(self, timeout: float = 30.0) -> None:
        """Close all sessions in the pool with a per-session timeout.

        Sessions are closed concurrently via threads. Any session whose
        ``close()`` does not complete within *timeout* seconds is abandoned
        with a warning (the container will eventually be reclaimed by
        ROCK's ``auto_clear_seconds``).
        """
        threads: list[threading.Thread] = []
        errors: list[str] = []

        def _close(session: Any) -> None:
            sid = getattr(session, "session_id", "?")
            try:
                session.close()
            except Exception as exc:
                errors.append(f"session {sid}: {exc}")
                logger.warning("Session close failed during teardown: %s", exc)

        for session in self._all_sessions:
            t = threading.Thread(target=_close, args=(session,), daemon=True)
            t.start()
            threads.append(t)

        for t in threads:
            t.join(timeout=timeout)
            if t.is_alive():
                logger.error(
                    "Session close timed out after %.0fs — abandoning (will be auto-cleared)",
                    timeout,
                )
