"""Reproduce fixed-image startup without any solver or model request."""
import os
import pytest
from alphadiana.harness.zeroclaw.runtime import ZeroClawPodmanRuntimeManager

@pytest.mark.skipif(not os.environ.get('ALPHADIANA_ZEROCLAW_IMAGE'),reason='requires real pinned rootless ZeroClaw image')
def test_real_pinned_zeroclaw_bridge_starts_with_native_python3():
    import httpx
    from alphadiana.engine.container_runtime.podman_cli import PodmanCLI
    manager=ZeroClawPodmanRuntimeManager({'podman_image':os.environ['ALPHADIANA_ZEROCLAW_IMAGE'], 'capture_logprobs':False, 'api_base':'http://127.0.0.1:1/v1', 'api_key':'offline-not-a-key', 'model':'offline-model', 'gateway_token':'fixture-token', 'gateway_startup_timeout':60})
    spec=manager._build_podman_spec()
    runtime=manager._podman_runtime
    try:
        result=runtime.start(spec)
        response=httpx.get(result.api_base+'/models',headers={'Authorization':'Bearer fixture-token'},timeout=5,trust_env=False)
        assert response.status_code==200
        assert response.json()['data'][0]['id']=='offline-model'
        manager._warmup_podman_gateway(result.api_base)
        assert runtime.exec(['python3','-c','import runpy;runpy.run_path("/tmp/zeroclaw_bridge.py",run_name="offline_import")']).returncode==0
    finally:
        runtime.cleanup(check=True)
    assert PodmanCLI().inspect(result.container_id,check=False).returncode!=0


def test_podman_readiness_queries_models_without_solver_probe(monkeypatch):
    from types import SimpleNamespace
    import httpx
    manager=ZeroClawPodmanRuntimeManager({'model':'offline-model','gateway_warmup_initial_delay':0,'gateway_warmup_timeout':1})
    requests=[]
    def get(url,**kwargs):
        requests.append(url)
        return SimpleNamespace(status_code=200,json=lambda:{'data':[{'id':'offline-model'}]})
    def refuse(*args,**kwargs):raise AssertionError('Startup must not call a solver')
    monkeypatch.setattr(httpx,'get',get);monkeypatch.setattr(httpx,'post',refuse)
    manager._warmup_podman_gateway('http://127.0.0.1:1/v1')
    assert requests==['http://127.0.0.1:1/v1/models']
