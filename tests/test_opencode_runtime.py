"""Compatibility entry point for OpenCode runtime artifact alias tests."""
from __future__ import annotations

# Re-export the current OpenCode runtime-facing tests under the Phase 10 plan path.
from tests.test_opencode_agent import (  # noqa: F401
    test_solve_preserves_opencode_session_trace_when_present,
    test_solve_text_only_uses_opencode_cli,
)

# artifact_manifest
