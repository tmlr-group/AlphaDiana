from __future__ import annotations

import pytest

from alphadiana.utils.rock_ports import RockPorts, resolve_rock_ports_from_env


def test_resolve_rock_ports_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROCK_RAY_PORT", raising=False)
    monkeypatch.delenv("ROCK_REDIS_PORT", raising=False)
    monkeypatch.delenv("ROCK_ADMIN_PORT", raising=False)
    monkeypatch.delenv("ROCK_PROXY_PORT", raising=False)

    ports = resolve_rock_ports_from_env()

    assert ports.ray_port == 6380
    assert ports.ray_dashboard_port == 8265
    assert ports.ray_client_server_port == 30001
    assert ports.redis_port == 6379
    assert ports.admin_port == 9000
    assert ports.proxy_port == 9001
    assert ports.base_url == "http://127.0.0.1:9000"
    assert ports.proxy_api_url == "http://127.0.0.1:9001/apis/envs/sandbox/v1"


def test_resolve_rock_ports_from_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROCK_RAY_PORT", "6382")
    monkeypatch.setenv("ROCK_RAY_DASHBOARD_PORT", "8266")
    monkeypatch.setenv("ROCK_RAY_CLIENT_SERVER_PORT", "30002")
    monkeypatch.setenv("ROCK_REDIS_PORT", "6383")
    monkeypatch.setenv("ROCK_ADMIN_PORT", "9010")
    monkeypatch.setenv("ROCK_PROXY_PORT", "9011")

    ports = resolve_rock_ports_from_env()

    assert ports.ray_port == 6382
    assert ports.ray_dashboard_port == 8266
    assert ports.ray_client_server_port == 30002
    assert ports.redis_port == 6383
    assert ports.admin_port == 9010
    assert ports.proxy_port == 9011
    assert ports.base_url == "http://127.0.0.1:9010"
    assert ports.proxy_root_url == "http://127.0.0.1:9011"
