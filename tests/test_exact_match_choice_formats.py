"""Real MMMU answers expose label-format failures; contradictory choices stay wrong."""
import json
from pathlib import Path

import pytest

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness.base import AgentResponse
from alphadiana.scorer.exact_match import ExactMatchScorer


def choice_task():
    return BenchmarkTask('choice', 'Pick one.\n\n(A) First option\n(B) Second option', 'B',
                         metadata={'question_type': 'multiple-choice', 'num_options': 2})


def test_captured_mmmu_choice_formats():
    fixture = json.loads((Path(__file__).parent / 'fixtures/mmmu-choice-smoke.json').read_text())
    expected = {'mmmu_pro_validation_Design_19': True, 'mmmu_pro_test_History_1': True, 'mmmu_pro_test_Art_113': False}
    for row in fixture['captured_records']:
        task = BenchmarkTask(row['task_id'], row['problem'], row['ground_truth'], metadata=row['metadata'])
        assert ExactMatchScorer().score(task, AgentResponse(row['predicted'])).correct is expected[row['task_id']]


@pytest.mark.parametrize('answer', [r'\text{B}', r'\text{(B)}', r'\text{(B) Second option}', '(B) Second option'])
def test_declared_choice_accepts_unambiguous_label_and_exact_option(answer):
    assert ExactMatchScorer().score(choice_task(), AgentResponse(answer)).correct is True


@pytest.mark.parametrize('answer', [r'\text{(A) First option}', r'\text{(B) First option}', r'\text{(B) Second option or (A) First option}', r'\text{(B) Second option}\text{(A)}', 'B or A', '(B) invented option', r'\text{(J) Second option}'])
def test_wrong_conflicting_and_undeclared_choices_stay_wrong(answer):
    assert ExactMatchScorer().score(choice_task(), AgentResponse(answer)).correct is False


def test_formatting_support_requires_explicit_choice_metadata():
    task = choice_task()
    task.metadata = {}
    assert ExactMatchScorer().score(task, AgentResponse(r'\text{(B) Second option}')).correct is False


@pytest.mark.parametrize('metadata', [{'question_type': 'free-form', 'num_options': 2}, {'question_type': 'multiple-choice', 'num_options': True}, {'question_type': 'multiple-choice', 'num_options': 11}])
def test_invalid_or_nonchoice_metadata_does_not_enable_option_text(metadata):
    task = choice_task()
    task.metadata = metadata
    assert ExactMatchScorer().score(task, AgentResponse(r'\text{(B) Second option}')).correct is False


def test_existing_bare_and_parenthesized_labels_keep_working():
    task = BenchmarkTask('legacy', 'Pick one.', 'B')
    for answer in ['B', '(B)']:
        assert ExactMatchScorer().score(task, AgentResponse(answer)).correct is True
