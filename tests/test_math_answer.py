from alphadiana.utils.math_answer import extract_answer_candidate


def test_extract_answer_candidate_refuses_raw_json_source():
    raw = '{"cost": 0, "duration": 12.3, "exit_code": 0}'

    assert extract_answer_candidate(raw) == ""
    assert extract_answer_candidate(raw, source="raw_json") == ""


def test_extract_answer_candidate_refuses_provider_error_json():
    raw = '{"error": {"message": "400 Bad Request: tool_choice auto unsupported"}}'

    assert extract_answer_candidate(raw) == ""


def test_extract_answer_candidate_refuses_jsonl_metadata():
    raw = '\n'.join([
        '{"type": "step_start", "cost": 0}',
        '{"type": "step_finish", "tokens": {"total": 0}}',
    ])

    assert extract_answer_candidate(raw) == ""


def test_extract_answer_candidate_allows_zero_from_assistant_text():
    assert extract_answer_candidate(r"The answer is \boxed{0}.") == "0"
