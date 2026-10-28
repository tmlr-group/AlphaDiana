"""English alerts retain startup blocking and SLAVEOF recovery behavior."""

import pytest

from scripts.setup import security_guard as guard


@pytest.mark.parametrize("severity,expected", [("CRITICAL", False), ("HIGH", False), ("WARN", True)])
def test_english_preflight_alert_preserves_startup_decision(monkeypatch, capsys, severity, expected):
    monkeypatch.delenv("SECURITY_GUARD_BYPASS", raising=False)
    monkeypatch.setattr(guard, "is_redis_host_port_loopback_published", lambda port: False)
    monkeypatch.setattr(guard, "check_redis_security", lambda *a, **kw: [
        guard.SecurityIssue(severity, "Redis has no password", "Unauthenticated access", "Set a password")
    ])
    for name in ("check_docker_redis_exposure", "check_services_binding_public",
                 "check_openclaw_security", "check_kernel_keyring_capacity"):
        monkeypatch.setattr(guard, name, lambda: [])
    assert guard.run_preflight_check() is expected
    output = capsys.readouterr().out
    assert "Check results:" in output
    assert "Fix: Set a password" in output
    if not expected:
        assert "service startup is blocked!" in output


def test_translated_slave_alert_still_triggers_recovery_command(monkeypatch, capsys):
    monkeypatch.setattr(guard, "get_monitored_redis_ports", lambda: [6379])
    monkeypatch.setattr(guard, "get_redis_replication_info", lambda port: {
        "role": "slave", "master_host": "attacker", "master_port": "6380",
    })
    for name in ("check_docker_redis_exposure", "check_services_binding_public", "check_openclaw_security"):
        monkeypatch.setattr(guard, name, lambda: [])
    monkeypatch.setattr(guard.signal, "signal", lambda *args: None)
    commands = []
    monkeypatch.setattr(guard, "run", lambda cmd: (commands.append(cmd) or (0, "OK", "")))

    def stop_after_one_cycle(seconds):
        raise InterruptedError("test cycle complete")

    monkeypatch.setattr(guard.time, "sleep", stop_after_one_cycle)
    with pytest.raises(InterruptedError, match="test cycle complete"):
        guard.run_daemon()
    assert commands == [["redis-cli", "-p", "6379", "SLAVEOF", "NO", "ONE"]]
    assert "Redis(:6379) has returned to Master mode" in capsys.readouterr().out
