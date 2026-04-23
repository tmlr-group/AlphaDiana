from __future__ import annotations

import json
from pathlib import Path

from alphadiana.agent.opencode import extract_opencode_logprob_records

RUN_ID = "phase11_opencode_gpqa_diamond_qwen35_27b_logprobs_smoke"
TASK_PATH = Path(f"results/{RUN_ID}/tasks/gpqa_0.json")


def _load_task_record() -> dict:
    payload = json.loads(TASK_PATH.read_text(encoding="utf-8"))
    return payload[0] if isinstance(payload, list) else payload


def test_phase11_opencode_requested_missing_fixture() -> None:
    record = _load_task_record()
    assert record["run_id"] == RUN_ID
    assert record["metadata"]["logprobs_capture_status"] == "requested_missing"
    assert record["token_entropy_stats"]["n_tokens"] == 0
    assert not record["logprobs_path"]
    assert not record["logprobs_int16_path"]


def _logprob_payload(token: str, value: float) -> dict:
    return {
        "choices": [
            {
                "logprobs": {
                    "content": [
                        {
                            "token": token,
                            "logprob": value,
                            "top_logprobs": [{"token": token, "logprob": value}],
                        }
                    ]
                }
            }
        ]
    }


def test_extract_opencode_logprobs_from_envelope_variants() -> None:
    events = [
        {
            "type": "trace",
            "response": {
                "provider_response": {
                    "payload": json.dumps({"data": _logprob_payload("alpha", -0.1)})
                }
            },
        }
    ]
    session_trace = json.dumps(
        {"message": {"part": {"payload": {"data": _logprob_payload("beta", -0.2)}}}}
    )
    stdout = json.dumps(
        {"part": {"response": {"provider_response": {"data": _logprob_payload("gamma", -0.3)}}}}
    )

    records = extract_opencode_logprob_records(
        events=events,
        session_trace=session_trace,
        stdout=stdout,
    )

    assert [record["token"] for record in records] == ["alpha", "beta", "gamma"]
    assert [record["token_index"] for record in records] == [0, 1, 2]
