from copy import deepcopy

import pytest

from alphadiana.harness.opencode.agent import _extract_opencode_error


def recovered_events():
    return [
        {
            "type": "error",
            "sessionID": "s1",
            "error": {
                "name": "ContextOverflowError",
                "data": {"message": "maximum context length"},
            },
        },
        {
            "type": "step_start",
            "sessionID": "s1",
            "part": {"sessionID": "s1", "messageID": "final"},
        },
        {
            "type": "text",
            "sessionID": "s1",
            "part": {
                "sessionID": "s1",
                "messageID": "final",
                "text": "The task is complete.",
            },
        },
        {
            "type": "step_finish",
            "sessionID": "s1",
            "part": {"sessionID": "s1", "messageID": "final", "reason": "stop"},
        },
    ]


def test_native_completed_recovery_does_not_become_adapter_failure():
    events = recovered_events()
    original = deepcopy(events)
    assert _extract_opencode_error(events, returncode=0) is None
    assert events == original  # Historical errors remain in retained raw events.


@pytest.mark.parametrize(
    "fault",
    [
        "nonzero",
        "unknown_exit",
        "different_session",
        "no_text",
        "unfinished",
        "later_error",
    ],
)
def test_partial_or_unverified_recovery_still_fails(fault):
    events = recovered_events()
    code = 0
    if fault == "nonzero":
        code = 1
    elif fault == "unknown_exit":
        code = None
    elif fault == "different_session":
        events[0]["sessionID"] = "another-session"
    elif fault == "no_text":
        events[2]["part"]["text"] = ""
    elif fault == "unfinished":
        events.append({"type": "step_start", "sessionID": "s1"})
    else:
        events.append(deepcopy(events[0]))
    assert (
        _extract_opencode_error(events, returncode=code)["name"]
        == "ContextOverflowError"
    )
