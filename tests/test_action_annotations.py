"""Annotation provenance, validation and cache reuse are explicit."""

import json
from types import SimpleNamespace

import pytest

from alphadiana.analysis.action_annotations import ActionAnnotations


def test_annotations_cache_exact_request_and_reject_invalid_labels(tmp_path):
    annotator = ActionAnnotations(
        model="fixture",
        model_version="revision-1",
        base_url="http://127.0.0.1:1/v1",
        api_key="fixture-key",
        cache_dir=tmp_path,
    )
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"label":"Reasoning"}'),
                )
            ],
            model="fixture",
            id="response-1",
            usage=None,
        )

    annotator.client.chat.completions.create = create
    evidence = {"target": "2 + 2 = 4"}
    label, digest = annotator._classify(evidence)
    assert label == "Reasoning"
    assert annotator._classify(evidence) == (label, digest)
    assert len(requests) == 1
    cached = json.loads((tmp_path / f"{digest}.json").read_text())
    assert cached["request"]["model_version"] == "revision-1"
    assert cached["response_id"] == "response-1"
    cached["answer"]["label"] = "Invented label"
    (tmp_path / f"{digest}.json").write_text(json.dumps(cached))
    with pytest.raises(ValueError, match="Invalid action"):
        annotator._classify(evidence)
    annotator.client.close()


def test_explicit_response_schema_is_detached_and_part_of_cache_identity(tmp_path):
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "label",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"label": {"type": "string", "enum": ["Reasoning"]}},
                "required": ["label"],
                "additionalProperties": False,
            },
        },
    }
    configured = ActionAnnotations(
        model="fixture",
        model_version="revision-1",
        base_url="http://127.0.0.1:1/v1",
        api_key="fixture-key",
        cache_dir=tmp_path,
        response_format=schema,
    )
    default = ActionAnnotations(
        model="fixture",
        model_version="revision-1",
        base_url="http://127.0.0.1:1/v1",
        api_key="fixture-key",
        cache_dir=tmp_path,
    )
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"label":"Reasoning"}'),
                )
            ],
            model="fixture",
            id="response-1",
            usage=None,
        )

    schema["json_schema"]["schema"]["properties"]["label"]["enum"].append(
        "Changed by caller"
    )
    try:
        for annotator in [configured, default]:
            annotator.client.chat.completions.create = create
        evidence = {"target": "2 + 2 = 4"}
        _, strict_digest = configured._classify(evidence)
        _, default_digest = default._classify(evidence)
        assert strict_digest != default_digest and len(calls) == 2
        assert calls[0]["response_format"]["json_schema"]["schema"]["properties"][
            "label"
        ]["enum"] == ["Reasoning"]
        cached = json.loads((tmp_path / f"{strict_digest}.json").read_text())
        assert cached["request"]["response_format"] == calls[0]["response_format"]
        assert (
            configured.measure([]).coverage["method"]["response_format"]
            == calls[0]["response_format"]
        )
        with pytest.raises(ValueError, match="Invalid action"):
            configured._validate(
                {"Reasoning": "A definition rather than the required label key"}
            )
    finally:
        configured.client.close()
        default.client.close()
