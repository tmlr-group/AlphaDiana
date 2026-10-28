"""Keep incomplete-response and heartbeat guards ahead of scoring."""

from types import SimpleNamespace

import pytest

from alphadiana.harness.base import AgentResponse
from alphadiana.harness.openclaw.integrity import integrity_guard_reason


@pytest.mark.parametrize(
    "metadata,finish,expected",
    [
        ({"session_tainted": True}, "stop", "session_tainted"),
        ({"received_done": False}, "stop", "stream_incomplete"),
        ({}, "incomplete", "finish_reason_incomplete"),
        ({"received_done": False, "openclaw_timeout_scored_zero": True}, "timeout", ""),
    ],
)
def test_metadata_guard_and_supported_timeout(metadata, finish, expected):
    response = AgentResponse("1", metadata=metadata, finish_reason=finish)
    assert (
        integrity_guard_reason(SimpleNamespace(agent_name="openclaw"), response)[0]
        == expected
    )


def test_old_task_and_tool_listing_do_not_taint_current_response():
    response = AgentResponse(
        "1",
        raw_output="1",
        request_messages=[{"role": "user", "content": "current"}],
        trajectory=[
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "HEARTBEAT_OK"},
            {"role": "user", "content": "current"},
            {"role": "tool", "content": "Read HEARTBEAT.md"},
            {"role": "assistant", "content": "1"},
        ],
    )
    config = SimpleNamespace(agent_name="openclaw")
    assert integrity_guard_reason(config, response) == ("", "")
    response.trajectory.append({"role": "assistant", "content": "HEARTBEAT_OK"})
    assert integrity_guard_reason(config, response)[0] == "heartbeat_trace"
