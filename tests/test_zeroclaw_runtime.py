"""Compatibility entry point for ZeroClaw runtime artifact alias tests."""
from __future__ import annotations

# Re-export the current ZeroClaw runtime-facing tests under the Phase 10 plan path.
from tests.test_zeroclaw_agent import (  # noqa: F401
    test_build_cli_response_promotes_runtime_trace_into_native_fields,
    test_zeroclaw_sandbox_solve_uses_uploaded_workspace_and_returns_answer,
)

# artifact_manifest
