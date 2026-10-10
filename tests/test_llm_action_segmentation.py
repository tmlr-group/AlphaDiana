"""The LLM chooses boundaries; the program verifies source mapping and coverage."""

import json

import pytest

from alphadiana.analysis.action_annotations import ActionAnnotations
from alphadiana.analysis.action_protocol import prompt_bytes
from alphadiana.analysis.action_segmentation import (
    PROTOCOL,
    segmentation_prompt,
    source_blocks,
    validate_segments,
)
from alphadiana.analysis.analyzer import Analyzer, ExecutionMetrics
from test_general_analysis import judge_options, record, response, source


def saved_trace(tmp_path, steps, **fields):
    return source(
        tmp_path,
        [
            record(
                "task",
                True,
                problem="Question only",
                ground_truth="SECRET GOLD",
                artifact_manifest={"files": {"normalized_trace": "trace.json"}},
            )
        ],
        {"artifacts/trace.json": json.dumps(dict(steps=steps, **fields))},
    )


def whole_blocks(evidence):
    return {
        "actions": [
            dict(
                source_id=b["source_id"],
                start=0,
                end=b["length"],
                label="Tool Use" if b["kind"] == "tool_call" else "Reasoning",
            )
            for b in evidence["sources"]
            if b["target"]
        ]
    }


def test_default_llm_chooses_boundary_one_call_and_never_reads_fixed_targets(tmp_path):
    text = "计划先列式。继续推导并给出答案。"
    artifacts = saved_trace(tmp_path, [{"role": "assistant", "content": text}])
    calls = []
    with ActionAnnotations(**judge_options(tmp_path)) as plugin:

        def create(**kw):
            calls.append(kw)
            evidence = json.loads(kw["messages"][1]["content"].split("\n", 1)[1])
            assert "SECRET GOLD" not in str(evidence)
            # A model-selected boundary inside one unbroken source paragraph.
            return response(
                {
                    "actions": [
                        dict(
                            source_id="steps/0/content",
                            start=0,
                            end=6,
                            label="Planning",
                        ),
                        dict(
                            source_id="steps/0/content",
                            start=6,
                            end=len(text),
                            label="Finalization",
                        ),
                    ]
                }
            )

        plugin.client.chat.completions.create = create
        result = plugin.measure([artifacts])
        assert len(calls) == 1
        assert plugin.protocol == PROTOCOL
        rows = result.tables["annotations"]
        assert [r["text"] for r in rows] == [text[:6], text[6:]]
        assert rows[0]["span_end"] == 6 and rows[1]["span_start"] == 6
        assert len({r["action_id"] for r in rows}) == 2
        assert result.tables["transitions"] == [
            dict(run="run", source="Planning", target="Finalization", count=1)
        ]
        assert plugin.measure([artifacts]).tables == result.tables
        assert len(calls) == 1
        assert result.coverage["method"]["calibration_inherited"] is False
        assert result.coverage["method"]["unit"] == "LLM-selected source ranges"


def test_protocol_reuses_every_main_label_rule_without_fixed_target_instructions():
    audit = prompt_bytes().decode()
    rules = audit[
        audit.index("- Understanding:") : audit.index("Before selecting the label")
    ]
    assert rules in segmentation_prompt()
    assert "The target boundary is fixed" not in segmentation_prompt()


def test_tools_thinking_and_repeated_events_keep_order_and_observations_are_context(
    tmp_path,
):
    steps = [
        {"role": "user", "content": "solve"},
        {
            "role": "assistant",
            "thinking": "reason",
            "content": "inspect",
            "type": "tool_use",
            "tool_calls": [
                {"id": "call-1", "tool": "bash", "input": {"command": "ls"}}
            ],
        },
        {"role": "tool", "type": "tool_result", "content": "error"},
        {"role": "assistant", "content": "repeat"},
        {"role": "assistant", "content": "repeat"},
    ]
    artifacts = saved_trace(tmp_path, steps, reasoning_steps=[{"content": "reason"}])
    with ActionAnnotations(**judge_options(tmp_path)) as plugin:

        def create(**kw):
            evidence = json.loads(kw["messages"][1]["content"].split("\n", 1)[1])
            assert any(
                b["text"] == "error" and not b["target"] for b in evidence["sources"]
            )
            return response(whole_blocks(evidence))

        plugin.client.chat.completions.create = create
        rows = plugin.measure([artifacts]).tables["annotations"]
        assert [r["source_id"] for r in rows] == [
            "steps/1/thinking",
            "steps/1/content",
            "steps/1/tool_calls/0",
            "steps/3/content",
            "steps/4/content",
        ]
        assert len({r["action_id"] for r in rows}) == 5


@pytest.mark.parametrize(
    "case",
    [
        "gap",
        "overlap",
        "wrong_id",
        "bad_label",
        "bool_offset",
        "extra",
        "missing",
        "split_tool",
    ],
)
def test_invalid_model_segmentation_is_rejected(case):
    blocks = source_blocks({"steps": [{"role": "assistant", "content": "abcdef"}]})
    answer = whole_blocks({"sources": blocks})
    row = answer["actions"][0]
    if case == "gap":
        row["start"] = 1
    elif case == "overlap":
        answer["actions"].append(dict(row))
    elif case == "wrong_id":
        row["source_id"] = "steps/99/content"
    elif case == "bad_label":
        row["label"] = "recover"
    elif case == "bool_offset":
        row["start"] = False
    elif case == "extra":
        row["confidence"] = 1
    elif case == "missing":
        answer["actions"] = []
    elif case == "split_tool":
        blocks[0]["kind"] = "tool_call"
        row["end"] = 3
        row["label"] = "Tool Use"
    with pytest.raises(ValueError):
        validate_segments(answer, blocks)


def test_windowing_preserves_source_coverage_and_cache(tmp_path):
    artifacts = saved_trace(
        tmp_path, [{"role": "assistant", "content": str(i) * 900} for i in range(8)]
    )
    prompt_size = len(segmentation_prompt().encode())
    calls = []
    with ActionAnnotations(
        **judge_options(tmp_path), input_budget=prompt_size + 5500
    ) as plugin:

        def create(**kw):
            calls.append(kw)
            return response(whole_blocks(json.loads(kw["messages"][1]["content"].split("\n", 1)[1])))

        plugin.client.chat.completions.create = create
        measured = plugin.measure([artifacts])
        rows = measured.tables["annotations"]
        assert 1 < len(calls) < 8
        assert len(rows) == 8 and len({r["action_id"] for r in rows}) == 8
        assert [r["source_id"] for r in rows] == [
            f"steps/{i}/content" for i in range(8)
        ]
        assert sum(r["count"] for r in measured.tables["transitions"]) == 7
        before = len(calls)
        plugin.measure([artifacts])
        assert len(calls) == before


def test_oversized_indivisible_source_and_unlinked_reasoning_fail_before_calls(
    tmp_path,
):
    artifacts = saved_trace(tmp_path, [{"role": "assistant", "content": "x" * 10000}])
    with ActionAnnotations(**judge_options(tmp_path), input_budget=100) as plugin:
        plugin.client.chat.completions.create = lambda **kw: pytest.fail(
            "must not call"
        )
        with pytest.raises(ValueError, match="capacity"):
            plugin.measure([artifacts])
    with pytest.raises(ValueError, match="Unlinked"):
        source_blocks(
            {
                "steps": [{"role": "assistant", "content": "answer"}],
                "reasoning_steps": [{"content": "unknown order"}],
            }
        )


def test_offline_execution_has_no_rule_based_action_results(tmp_path, monkeypatch):
    from alphadiana.analysis import action_events

    monkeypatch.setattr(
        action_events,
        "extract_action_event_rows",
        lambda *a, **kw: pytest.fail("legacy called"),
    )
    artifacts = saved_trace(
        tmp_path, [{"role": "assistant", "content": "Final answer 2"}]
    )
    result = ExecutionMetrics().measure([artifacts])
    assert (
        not {"actions", "action_composition", "action_transitions"}
        & result.tables.keys()
    )
    assert not any("action" in f.name for f in result.figures)
    assert "enable judge=True" in result.coverage["run"]["action_labeling"]


def test_failed_llm_does_not_fall_back_to_rules(tmp_path):
    artifacts = saved_trace(
        tmp_path, [{"role": "assistant", "content": "Final answer 2"}]
    )
    with ActionAnnotations(**judge_options(tmp_path)) as plugin:
        plugin.client.chat.completions.create = lambda **kw: response({"actions": []})
        with pytest.raises(ValueError, match="coverage"):
            Analyzer(artifacts, plugins=[plugin]).compute()
        attempts = list((plugin.cache / "failed-attempts").glob("*.json"))
        assert len(attempts) == 1
        assert (
            json.loads(attempts[0].read_text())["choices"][0]["content"]
            == '{"actions": []}'
        )
        assert not list(plugin.cache.glob("*.json"))


@pytest.mark.parametrize('status', ['provider_error', 'runtime_error'])
def test_failed_execution_without_assistant_actions_keeps_other_annotations(tmp_path, status):
    artifacts = source(tmp_path, [
        dict(task_id='blocked', score_status=status, error='failed before response', correct=None, score=None,
             artifact_manifest={'files': {'normalized_trace': 'blocked.json'}}),
        record('answered', False, artifact_manifest={'files': {'normalized_trace': 'answered.json'}}),
    ], {
        'artifacts/blocked.json': json.dumps({'steps': [{'role': 'user', 'content': 'solve'}]}),
        'artifacts/answered.json': json.dumps({'steps': [{'role': 'assistant', 'content': 'actual answer'}]}),
    })
    calls = []
    with ActionAnnotations(**judge_options(tmp_path)) as plugin:
        def create(**kw):
            calls.append(kw)
            return response(whole_blocks(json.loads(kw['messages'][1]['content'].split('\n', 1)[1])))
        plugin.client.chat.completions.create = create
        result = plugin.measure([artifacts])
        assert len(calls) == 1
        assert {r['task_id'] for r in result.tables['annotations']} == {'answered'}
        assert result.coverage['run']['traces_without_assistant_actions'] == [
            {'task_id': 'blocked', 'sample_index': 0, 'score_status': status}
        ]
        assert len(result.coverage['run']['sources']) == 2


def test_valid_wrong_answer_without_saved_actions_still_fails_before_api(tmp_path):
    artifacts = source(tmp_path, [record('answered', False, artifact_manifest={'files': {'normalized_trace': 'trace.json'}})],
                       {'artifacts/trace.json': json.dumps({'steps': [{'role': 'user', 'content': 'solve'}]})})
    with ActionAnnotations(**judge_options(tmp_path)) as plugin:
        plugin.client.chat.completions.create = lambda **kw: pytest.fail('must not call')
        with pytest.raises(ValueError, match='No saved assistant actions'):
            plugin.measure([artifacts])
