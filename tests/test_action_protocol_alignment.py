"""Protocol/measurement regression checks; no claims of model-label accuracy."""

import json

import pytest
from action_target_fixtures import attach_targets, fixed_item
from alphadiana.analysis.action_annotations import ActionAnnotations
from alphadiana.analysis.action_protocol import PROTOCOL, prompt_bytes, request_for
from test_general_analysis import judge_options, record, response, source


def artifacts_with_targets(tmp_path, items):
    artifacts = source(
        tmp_path,
        [
            record(
                "task",
                artifact_manifest={
                    "files": {
                        "normalized_trace": "trace.json",
                    }
                },
            )
        ],
        {
            "artifacts/trace.json": json.dumps(
                {"steps": [{"role": "assistant", "content": "fixture"}]}
            )
        },
    )
    attach_targets(artifacts, items)
    return artifacts


def target_path(artifacts):
    return artifacts.file(artifacts.records()[0], "action_targets")


def test_fixed_target_and_context_survive_exactly_without_label_leakage(tmp_path):
    # Crosses the old 4096-character target and 1000-character context limits.
    item = fixed_item("A1", "推导\n" * 3000, before="before" * 600, after="after" * 600)
    item.update(label="Finalization", outcome="Correct", action_pred="Planning")
    artifacts = artifacts_with_targets(tmp_path, [item])
    calls = []
    with ActionAnnotations(
        protocol="human-protocol-v2", **judge_options(tmp_path)
    ) as classifier:

        def create(**kwargs):
            calls.append(kwargs)
            return response({"actions": [{"action_id": "A1", "label": "Reasoning"}]})

        classifier.client.chat.completions.create = create
        measured = classifier.measure([artifacts])
        assert len(calls) == 1
        assert calls[0]["messages"][0]["content"].encode() == prompt_bytes()
        assert json.loads(calls[0]["messages"][1]["content"]) == request_for(item)
        assert len(measured.tables["annotations"]) == 1
        assert measured.tables["transitions"] == []
        method = measured.coverage["method"]
        assert method["protocol"] == PROTOCOL
        assert method["calibration_inherited"] is False
        assert method["human_reviewed"] is False
        assert (
            "Finalization > Tool Use > Verification > Reasoning > Planning > Understanding"
            in classifier.prompt
        )
        assert classifier.measure([artifacts]).tables == measured.tables
        assert len(calls) == 1


@pytest.mark.parametrize(
    "answer",
    [
        {"label": "Reasoning"},
        {"actions": [{"action_id": "WRONG", "label": "Reasoning"}]},
        {"actions": [{"action_id": "A1", "label": "unknown"}]},
        {
            "actions": [
                {"action_id": "A1", "label": "Reasoning"},
                {"action_id": "A1", "label": "Planning"},
            ]
        },
    ],
)
def test_reject_wrong_schema_id_and_labels(tmp_path, answer):
    with ActionAnnotations(
        protocol="human-protocol-v2", **judge_options(tmp_path)
    ) as classifier:
        classifier.client.chat.completions.create = lambda **kw: response(answer)
        with pytest.raises(ValueError):
            classifier._classify(request_for(fixed_item("A1", "target")))
        assert not list(classifier.cache.glob("*.json"))


def test_targets_are_required_instead_of_silently_resegmenting(tmp_path):
    artifacts = artifacts_with_targets(tmp_path, [fixed_item("A1", "target")])
    rows = artifacts.records()
    del rows[0]["artifact_manifest"]["files"]["action_targets"]
    artifacts.path.write_text(json.dumps(rows[0]) + "\n")
    with ActionAnnotations(
        protocol="human-protocol-v2", **judge_options(tmp_path)
    ) as classifier:
        classifier.client.chat.completions.create = lambda **kw: pytest.fail(
            "No model call"
        )
        with pytest.raises(ValueError, match="action_targets"):
            classifier.measure([artifacts])


@pytest.mark.parametrize(
    "mutation",
    ["hash", "duplicate_id", "duplicate_position", "context", "empty", "non_object"],
)
def test_bad_target_manifest_fails_before_model_calls(tmp_path, mutation):
    artifacts = artifacts_with_targets(tmp_path, [fixed_item("A1", "target", 0)])
    path = target_path(artifacts)
    document = json.loads(path.read_text())
    if mutation == "hash":
        document["trace_sha256"] = "0" * 64
    elif mutation == "duplicate_id":
        document["items"].append(fixed_item("A1", "other", 1))
    elif mutation == "duplicate_position":
        document["items"].append(fixed_item("A2", "other", 0))
    elif mutation == "context":
        document["items"][0]["context_text"] = 3
    elif mutation == "empty":
        document["items"] = []
    else:
        document["items"] = ["bad"]
    path.write_text(json.dumps(document))
    with ActionAnnotations(
        protocol="human-protocol-v2", **judge_options(tmp_path)
    ) as classifier:
        classifier.client.chat.completions.create = lambda **kw: pytest.fail(
            "No model call"
        )
        with pytest.raises(ValueError):
            classifier.measure([artifacts])


def test_transitions_use_only_declared_adjacent_positions(tmp_path):
    # Deliberately shuffled UI order, with a gap and an unordered audit item.
    items = [
        fixed_item("C", "c", 4),
        fixed_item("B", "b", 1),
        fixed_item("X", "x"),
        fixed_item("A", "a", 0),
    ]
    artifacts = artifacts_with_targets(tmp_path, items)
    labels = dict(A="Understanding", B="Reasoning", C="Finalization", X="Planning")
    with ActionAnnotations(
        protocol="human-protocol-v2", **judge_options(tmp_path)
    ) as classifier:

        def create(**kw):
            identity = json.loads(kw["messages"][1]["content"])["actions"][0][
                "action_id"
            ]
            return response(
                {"actions": [{"action_id": identity, "label": labels[identity]}]}
            )

        classifier.client.chat.completions.create = create
        transitions = classifier.measure([artifacts]).tables["transitions"]
    assert transitions == [
        {"run": "run", "source": "Understanding", "target": "Reasoning", "count": 1}
    ]


def test_legacy_cache_is_not_reused_and_context_changes_invalidate_cache(tmp_path):
    item = fixed_item("A1", "target")
    with ActionAnnotations(
        **judge_options(tmp_path),
        protocol="custom-spans-v1",
        span_chars=4096,
        taxonomy={"Reasoning": "derive"},
    ) as legacy:
        legacy.client.chat.completions.create = lambda **kw: response(
            {"label": "Reasoning"}
        )
        _, old_digest = legacy._classify({"target": "target"})
    with ActionAnnotations(
        protocol="human-protocol-v2", **judge_options(tmp_path)
    ) as canonical:
        calls = []

        def create(**kw):
            calls.append(kw)
            return response({"actions": [{"action_id": "A1", "label": "Reasoning"}]})

        canonical.client.chat.completions.create = create
        _, first = canonical._classify(request_for(item))
        item["following_text"] = "changed context"
        _, second = canonical._classify(request_for(item))
        assert len({old_digest, first, second}) == 3 and len(calls) == 2


@pytest.mark.parametrize(
    "options", [{"span_chars": 4096}, {"taxonomy": {"Reasoning": "derive"}}]
)
def test_old_options_cannot_silently_override_human_protocol(tmp_path, options):
    with pytest.raises(ValueError, match="explicit custom-spans-v1"):
        ActionAnnotations(**judge_options(tmp_path), **options)
