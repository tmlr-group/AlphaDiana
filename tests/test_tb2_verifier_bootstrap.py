"""A verifier dependency failure is not a measured wrong answer."""
import json
from types import SimpleNamespace
import pytest
from alphadiana.artifacts import Artifacts
from alphadiana.analysis.analyzer import ExecutionMetrics
from alphadiana.benchmarks.terminal_bench2.harness.common import TerminalBench2ContainerMixin

@pytest.mark.parametrize('output', [
    '/usr/local/bin/uvx: line 35: python3: command not found',
    '/usr/bin/python3: No module named pip',
])
def test_saved_bootstrap_zero_is_not_a_valid_score(tmp_path, output):
    p=tmp_path/'saved.jsonl'
    p.write_text(json.dumps({'task_id':'task','benchmark_name':'terminal_bench2','score_status':'valid_scored',
                            'predicted':'0','correct':False,'score':0,
                            'metadata':{'verifier_status':'ok','test_output':output}})+'\n')
    source=Artifacts(p)
    for _ in range(2):
        row=source.records()[0]
        assert row['score_status']=='verifier_error'
        assert row['score']==0 and row['correct'] is False  # Retained forensic fields.
        assert ExecutionMetrics().measure([source]).tables['performance'][0]['scored']==0

@pytest.mark.parametrize('output,expected_status,expected_reward', [
    ('/usr/local/bin/uvx: line 35: python3: command not found','bootstrap_error',None),
    ('/usr/bin/python3: No module named pip','bootstrap_error',None),
    ('======================== 7 failed in 0.09s ========================','ok','0'),
])
def test_runtime_keeps_dependency_failure_separate_from_test_failure(tmp_path, monkeypatch, output, expected_status, expected_reward):
    owner=TerminalBench2ContainerMixin();owner._setup_container_config({'container_engine':'docker'})
    runtime=SimpleNamespace(logs_dir=tmp_path,container_id='fixture',task=SimpleNamespace(task_id='task',metadata={}))
    monkeypatch.setattr(owner,'_probe_running_verifier',lambda container_id: (False,'ok'))
    def run_tests(container_id, timeout_sec):
        (tmp_path/'verifier').mkdir();(tmp_path/'verifier/reward.txt').write_text('0')
        return output
    monkeypatch.setattr(owner,'_run_tests',run_tests)
    result=owner._run_verifier_and_read_reward(runtime)
    assert result.status==expected_status and result.reward==expected_reward
    assert (tmp_path/'verifier/reward.txt').read_text()=='0'


def test_fresh_verified_result_supersedes_old_bootstrap_error(tmp_path):
    p=tmp_path/'rescored.jsonl'
    p.write_text(json.dumps({'task_id':'task','benchmark_name':'terminal_bench2','score_status':'valid_scored',
                            'predicted':'0','correct':True,'score':1,
                            'metadata':{'verifier_status':'ok','test_output':'/usr/bin/python3: No module named pip'},
                            'score_metadata':{'replayed':True,'verifier_status':'ok'},
                            'rescoring':{'environment_allowed':True,'scorer_predicted':'1'}})+'\n')
    assert Artifacts(p).records()[0]['score_status']=='valid_scored'


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('summary,expected_status', [(None, 'bootstrap_error'), ({'tests': 0}, 'bootstrap_error'), ({'tests': 7, 'failed': 7}, 'ok')])
def test_declared_official_test_report_must_exist_before_accepting_zero(tmp_path, monkeypatch, cached, summary, expected_status):
    task_dir = tmp_path / 'task'
    (task_dir / 'tests').mkdir(parents=True)
    (task_dir / 'tests/test.sh').write_text('uvx -p 3.13 pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py\n')
    logs = tmp_path / 'logs'
    (logs / 'verifier').mkdir(parents=True)
    def write_results():
        (logs / 'verifier/reward.txt').write_text('0')
        if summary is not None:
            (logs / 'verifier/ctrf.json').write_text(json.dumps({'results': {'summary': summary}}))
    if cached:
        write_results()
    owner = TerminalBench2ContainerMixin()
    owner._setup_container_config({'container_engine': 'docker'})
    runtime = SimpleNamespace(logs_dir=logs, container_id='fixture', task=SimpleNamespace(task_id='task', metadata={'task_dir': str(task_dir)}))
    monkeypatch.setattr(owner, '_probe_running_verifier', lambda container_id: (False, 'ok'))
    def run_tests(container_id, timeout_sec):
        write_results()
        return 'error: Failed to download Python from GitHub' if summary is None else 'pytest finished'
    monkeypatch.setattr(owner, '_run_tests', run_tests)
    result = owner._run_verifier_and_read_reward(runtime)
    assert result.status == expected_status
    assert result.reward == ('0' if expected_status == 'ok' else None)


def test_container_owned_reward_directory_need_not_be_host_writable(tmp_path, monkeypatch):
    owner = TerminalBench2ContainerMixin()
    owner._setup_container_config({'container_engine': 'docker'})
    runtime = SimpleNamespace(logs_dir=tmp_path, container_id='fixture', task=SimpleNamespace(task_id='task', metadata={}))
    verifier = tmp_path / 'verifier'
    verifier.mkdir()
    monkeypatch.setattr(owner, '_probe_running_verifier', lambda container_id: (False, 'ok'))
    def run_tests(container_id, timeout_sec):
        (verifier / 'reward.txt').write_text('0')
        verifier.chmod(0o500)
        return '7 failed in 0.09s'
    monkeypatch.setattr(owner, '_run_tests', run_tests)
    try:
        result = owner._run_verifier_and_read_reward(runtime)
        assert result.status == 'ok' and result.reward == '0'
    finally:
        verifier.chmod(0o700)
