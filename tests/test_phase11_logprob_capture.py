from __future__ import annotations

from types import SimpleNamespace

from alphadiana.agent.logprob_capture import (
    apply_openai_logprob_request,
    extract_openai_logprob_records,
    finalize_logprob_capture,
    raw_token_logprob_dict_to_record,
    resolve_logprob_capture_config,
)


def test_resolve_logprob_capture_config_enabled_defaults():
    capture = resolve_logprob_capture_config({"capture_logprobs": True})
    assert capture == {"enabled": True, "top_logprobs": 20}


def test_apply_openai_logprob_request_adds_flags():
    payload = {"model": "test"}
    apply_openai_logprob_request(payload, {"enabled": True, "top_logprobs": 20})
    assert payload["logprobs"] is True
    assert payload["top_logprobs"] == 20


def test_raw_token_logprob_dict_to_record_shape():
    record = raw_token_logprob_dict_to_record(
        {
            "token": "A",
            "logprob": -0.1,
            "top_logprobs": [{"token": "A", "logprob": -0.1}],
        },
        0,
    )
    assert record == {
        "token_index": 0,
        "token": "A",
        "logprob": -0.1,
        "top_logprobs": [{"token": "A", "logprob": -0.1}],
    }


def test_extract_openai_logprob_records_supports_dict_and_object_payloads():
    dict_payload = {
        "choices": [
            {
                "logprobs": {
                    "content": [
                        {
                            "token": "A",
                            "logprob": -0.1,
                            "top_logprobs": [{"token": "A", "logprob": -0.1}],
                        }
                    ]
                }
            }
        ]
    }
    obj_payload = SimpleNamespace(
        choices=[
            SimpleNamespace(
                logprobs=SimpleNamespace(
                    content=[
                        SimpleNamespace(
                            token="B",
                            logprob=-0.2,
                            top_logprobs=[SimpleNamespace(token="B", logprob=-0.2)],
                        )
                    ]
                )
            )
        ]
    )

    dict_records, next_index = extract_openai_logprob_records(dict_payload, start_index=0)
    obj_records, next_index_2 = extract_openai_logprob_records(obj_payload, start_index=next_index)

    assert dict_records[0]["token_index"] == 0
    assert dict_records[0]["token"] == "A"
    assert obj_records[0]["token_index"] == 1
    assert obj_records[0]["token"] == "B"
    assert next_index_2 == 2


def test_finalize_logprob_capture_captured():
    record = {
        "token_index": 0,
        "token": "A",
        "logprob": -0.1,
        "top_logprobs": [{"token": "A", "logprob": -0.1}],
    }
    token_entropy_stats, metadata = finalize_logprob_capture(
        harness="direct_llm",
        enabled=True,
        records=[record],
        metadata={},
    )
    assert token_entropy_stats["n_tokens"] == 1
    assert metadata["logprobs_capture_status"] == "captured"
    assert metadata["logprob_capture_harness"] == "direct_llm"
    assert metadata["logprob_records"] == [record]


def test_finalize_logprob_capture_requested_missing_without_records():
    token_entropy_stats, metadata = finalize_logprob_capture(
        harness="openclaw",
        enabled=True,
        records=[],
        metadata={},
    )
    assert token_entropy_stats["n_tokens"] == 0
    assert metadata["logprobs_capture_status"] == "requested_missing"
    assert metadata["logprob_capture_harness"] == "openclaw"
    assert "logprob_records" not in metadata


def test_finalize_logprob_capture_not_requested():
    token_entropy_stats, metadata = finalize_logprob_capture(
        harness="direct_llm",
        enabled=False,
        records=[{"token_index": 0, "token": "A", "logprob": -0.1, "top_logprobs": []}],
        metadata={},
    )
    assert token_entropy_stats["n_tokens"] == 0
    assert metadata["logprobs_capture_status"] == "not_requested"
    assert metadata["logprob_capture_harness"] == "direct_llm"
    assert "logprob_records" not in metadata
