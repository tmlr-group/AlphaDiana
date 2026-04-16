from alphadiana.agent.opencode import _extract_event_texts, _extract_strict_answer


def test_extract_event_texts_reads_nested_part_text():
    obj = {
        "type": "text",
        "part": {
            "type": "text",
            "text": "Nested assistant text",
        },
    }
    assert _extract_event_texts(obj) == ["Nested assistant text"]


def test_extract_strict_answer_ignores_partial_reasoning_numbers():
    text = "Trying N=3, then N=6, but still checking cases."
    assert _extract_strict_answer(text) == ""


def test_extract_strict_answer_accepts_boxed_answer():
    text = "Done.\n\\boxed{3}"
    assert _extract_strict_answer(text) == "3"
