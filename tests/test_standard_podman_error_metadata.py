from __future__ import annotations

from alphadiana.agent.base import AgentResponse
from alphadiana.runner.runner import (
    _agent_error_provenance_metadata,
    _apply_error_provenance_metadata,
)


class _PodmanMetadataAgent:
    def error_provenance_metadata(self) -> dict:
        return {
            "container_engine": "podman",
            "adapter_name": "demo-podman",
            "container_id": "cid-123",
        }


def test_runner_applies_standard_agent_error_provenance_without_overwriting_failure_metadata() -> None:
    response = AgentResponse(
        answer=None,
        metadata={
            "failure_reason": "empty_response",
            "container_id": "existing-cid",
        },
    )

    _apply_error_provenance_metadata(
        response,
        _agent_error_provenance_metadata(_PodmanMetadataAgent()),
    )

    assert response.metadata["failure_reason"] == "empty_response"
    assert response.metadata["container_engine"] == "podman"
    assert response.metadata["adapter_name"] == "demo-podman"
    assert response.metadata["container_id"] == "existing-cid"
