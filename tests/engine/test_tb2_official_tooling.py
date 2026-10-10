"""Official verifier scripts own their interpreter and dependency pins."""
from types import SimpleNamespace

from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.engine.sandbox import terminal_bench2 as common


def test_verifier_executes_official_script_without_replacing_tools(monkeypatch):
    owner = common.TerminalBench2ContainerMixin()
    owner._setup_container_config({'container_engine': 'docker'})
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout='official output', stderr='', returncode=0)
    monkeypatch.setattr(common.subprocess, 'run', run)
    assert owner._run_tests('owned-container') == 'official output'
    assert calls == [['docker', 'exec', 'owned-container', 'bash', '/tests/test.sh']]


def test_docker_task_forwards_reachable_dependency_proxy(tmp_path, monkeypatch):
    owner = common.TerminalBench2ContainerMixin()
    owner._setup_container_config({'container_engine': 'docker'})
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:18891')
    monkeypatch.setenv('NO_PROXY', 'genai.hkbu.edu.hk')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout='a' * 64, stderr='', returncode=0)
    monkeypatch.setattr(common.subprocess, 'run', run)
    task = BenchmarkTask('task', 'fixture', '1', {})
    owner._start_container('owned-image', tmp_path, task)
    command = calls[0]
    environment = [command[index + 1] for index, value in enumerate(command[:-1]) if value == '-e']
    assert 'HTTPS_PROXY=http://host.docker.internal:18891' in environment
    bypass = next(value for value in environment if value.startswith('NO_PROXY='))
    assert 'genai.hkbu.edu.hk' in bypass and 'host.docker.internal' in bypass


def test_docker_proxy_forwarding_can_be_disabled(tmp_path, monkeypatch):
    owner = common.TerminalBench2ContainerMixin()
    owner._setup_container_config({'container_engine': 'docker', 'forward_proxy_env': False})
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:18891')
    calls = []
    monkeypatch.setattr(common.subprocess, 'run', lambda command, **kw: calls.append(command) or SimpleNamespace(stdout='a' * 64, stderr='', returncode=0))
    owner._start_container('owned-image', tmp_path, BenchmarkTask('task', 'fixture', '1', {}))
    assert '-e' not in calls[0]
