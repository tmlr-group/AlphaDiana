from __future__ import annotations

from scripts import security_guard


def test_check_redis_security_allows_loopback_only_docker_publish(monkeypatch) -> None:
    monkeypatch.setattr(security_guard, "redis_cmd", lambda port, *args, password=None: (True, "PONG"))

    def fake_config(port: int, key: str) -> str:
        return {
            "requirepass": "",
            "protected-mode": "no",
            "bind": "* -::*",
        }[key]

    monkeypatch.setattr(security_guard, "get_redis_config", fake_config)
    monkeypatch.setattr(security_guard, "get_redis_replication_info", lambda port: {"role": "master"})

    issues = security_guard.check_redis_security(6381, loopback_only_docker_publish=True)

    assert issues == []


def test_check_redis_security_rejects_public_unauthenticated_redis(monkeypatch) -> None:
    monkeypatch.setattr(security_guard, "redis_cmd", lambda port, *args, password=None: (True, "PONG"))

    def fake_config(port: int, key: str) -> str:
        return {
            "requirepass": "",
            "protected-mode": "no",
            "bind": "* -::*",
        }[key]

    monkeypatch.setattr(security_guard, "get_redis_config", fake_config)
    monkeypatch.setattr(security_guard, "get_redis_replication_info", lambda port: {"role": "master"})

    issues = security_guard.check_redis_security(6381)

    assert [issue.severity for issue in issues] == ["CRITICAL", "HIGH", "HIGH"]


def test_redis_host_port_loopback_detection_rejects_public_binding(monkeypatch) -> None:
    monkeypatch.setattr(
        security_guard,
        "get_docker_containers",
        lambda: [{"ID": "abc", "Ports": "0.0.0.0:6381->6379/tcp"}],
    )
    monkeypatch.setattr(
        security_guard,
        "get_container_inspect",
        lambda container_id: {
            "HostConfig": {
                "PortBindings": {
                    "6379/tcp": [{"HostIp": "0.0.0.0", "HostPort": "6381"}]
                }
            }
        },
    )

    assert security_guard.is_redis_host_port_loopback_published(6381) is False
