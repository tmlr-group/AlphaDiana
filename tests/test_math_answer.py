from alphadiana.utils.math_answer import (
    extract_answer_candidate,
    extract_numeric_answer_candidate,
    parse_numeric_answer,
)


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


def test_generic_extraction_preserves_legacy_unit_answer_span():
    raw = "**Answer: 204 minutes** (or 3 hours and 24 minutes)"

    assert extract_answer_candidate(raw) == "204 minutes** (or 3 hours and 24 minutes)"


def test_numeric_extraction_uses_primary_numeric_answer_before_alternative():
    raw = "**Answer: 204 minutes** (or 3 hours and 24 minutes)"

    assert extract_numeric_answer_candidate(raw) == "204"
    assert parse_numeric_answer(raw) == 204.0


def test_numeric_extraction_handles_markdown_answer_span():
    raw = "The final answer is **204 minutes** (or 3 hours and 24 minutes)."

    assert extract_numeric_answer_candidate(raw) == "204"
    assert parse_numeric_answer(raw) == 204.0


def test_numeric_extraction_handles_markdown_answer_with_trailing_period():
    raw = "**204 minutes**."

    assert extract_numeric_answer_candidate(raw) == "204"
    assert parse_numeric_answer(raw) == 204.0


def test_numeric_parser_preserves_legacy_long_output_last_number_fallback():
    raw = (
        "Let me verify this solution by working through the problem carefully. "
        "The intersection points are at x=3 and x=7, giving us 49"
    )

    assert extract_numeric_answer_candidate(raw) == "49"
    assert parse_numeric_answer(raw) == 49.0
