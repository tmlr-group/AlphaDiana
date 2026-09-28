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
