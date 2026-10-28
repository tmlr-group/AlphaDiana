"""Agent runtimes own their workspace even when a pinned image lacks it."""
from types import SimpleNamespace
import os
import uuid
import pytest
from alphadiana.engine.container_runtime.agent_runtime import PodmanAgentRuntime, PodmanAgentSpec


def test_workspace_is_created_before_agent_install_and_launch(monkeypatch):
    events = []
    class Transport:
        def run(self, image, **kwargs):
            if kwargs['workdir'] != '/':
                raise RuntimeError('captured Podman exit126: workdir /workspace does not exist')
            events.append('container'); return SimpleNamespace(stdout='owned')
        def exec(self, container, command, **kwargs):
            assert container == 'owned'
            assert command == ['mkdir', '-p', '--', '/workspace']
            assert kwargs['workdir'] == '/' and kwargs['check']
            events.append('workspace'); return SimpleNamespace(stdout='')
    owner = PodmanAgentRuntime(runtime=Transport())
    monkeypatch.setattr(owner, 'reap_orphans', lambda _: 0)
    monkeypatch.setattr(owner, '_inspect_runtime_identity', lambda _: {})
    monkeypatch.setattr(owner, '_copy_files', lambda *a: events.append('files'))
    monkeypatch.setattr(owner, '_run_install_commands', lambda spec, cid: events.append('install'))
    monkeypatch.setattr(owner, '_launch_process', lambda spec, cid: events.append(('launch',spec.workdir)) or '')
    monkeypatch.setattr(owner, '_resolve_api_base', lambda *a: '')
    monkeypatch.setattr(owner, '_wait_until_ready', lambda *a: None)
    monkeypatch.setattr(owner, '_logs', lambda *a: '')
    monkeypatch.setattr(owner, '_collect_text_paths', lambda *a, **kw: {})
    result = owner.start(PodmanAgentSpec('workspace-fixture','sha256:fixture','true',None))
    assert result.container_id == 'owned'
    assert events == ['container','workspace','files','install',('launch','/workspace')]


@pytest.mark.skipif(not os.environ.get('ALPHADIANA_LIFECYCLE_IMAGE'), reason='requires pinned real rootless Podman image')
def test_real_pinned_image_gets_missing_workspace():
    from alphadiana.engine.container_runtime.podman_cli import PodmanCLI
    cli=PodmanCLI(); assert cli.rootless()
    workspace='/workspace-regression-'+uuid.uuid4().hex
    owner=PodmanAgentRuntime(runtime=cli)
    spec=PodmanAgentSpec('workspace-fixture',os.environ['ALPHADIANA_LIFECYCLE_IMAGE'],'',None,
        workdir=workspace,network='none',name_prefix='alphadiana-layout-check',
        install_commands=('test "$PWD" = '+workspace,),safe_config_paths=())
    try:
        result=owner.start(spec)
        assert owner.exec(['pwd']).stdout.strip()==workspace
        assert owner.exec(['test','-d',workspace]).returncode==0
    finally:
        owner.cleanup(check=True)
    assert cli.inspect(result.container_id,check=False).returncode!=0
