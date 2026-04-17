from __future__ import annotations

from types import SimpleNamespace

from alphadiana.utils.rock_ports import RockPorts, check_rock_services


class _DummySocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_check_rock_services_uses_openapi_probes(monkeypatch) -> None:
    requested_urls: list[str] = []

    def _fake_connect(address, timeout=0):
        return _DummySocket()

    def _fake_get(url, timeout=0, trust_env=False):
        requested_urls.append(url)
        if url.endswith(":9120/openapi.json"):
            return SimpleNamespace(status_code=200, text="/apis/envs/sandbox/v1/start_async")
        if url.endswith(":9121/openapi.json"):
            return SimpleNamespace(status_code=200, text="/apis/envs/sandbox/v1/sandboxes")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("socket.create_connection", _fake_connect)
    monkeypatch.setattr("httpx.get", _fake_get)
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr="", stdout=""),
    )

    ports = RockPorts(
        ray_port=6480,
        ray_dashboard_port=8365,
        ray_client_server_port=30100,
        redis_port=6390,
        admin_port=9120,
        proxy_port=9121,
    )
    results = check_rock_services(ports, timeout=1.0)

    assert results["admin"] is True
    assert results["proxy"] is True
    assert requested_urls == [
        "http://127.0.0.1:9120/openapi.json",
        "http://127.0.0.1:9121/openapi.json",
    ]
