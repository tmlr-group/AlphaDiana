import importlib
import re
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO / rel_path).read_text()


def test_zeroclaw_bridge_default_host_is_loopback(monkeypatch):
    monkeypatch.delenv("ZEROCLAW_BRIDGE_HOST", raising=False)
    import zeroclaw_deploy.zeroclaw_bridge as mod

    importlib.reload(mod)
    assert mod.HOST == "127.0.0.1"


def test_zeroclaw_bridge_allows_explicit_override(monkeypatch, caplog):
    monkeypatch.setenv("ZEROCLAW_BRIDGE_HOST", "0.0.0.0")
    import zeroclaw_deploy.zeroclaw_bridge as mod

    caplog.clear()
    importlib.reload(mod)
    assert mod.HOST == "0.0.0.0"
    assert any(
        "unsafe" in record.message.lower()
        or "public" in record.message.lower()
        or "0.0.0.0" in record.message
        for record in caplog.records
    )


def test_swebench_container_port_publish_includes_hostip():
    content = _read("alphadiana/sandbox/swebench_container.py")
    assert re.search(r"HostIp[\"']?\s*:\s*[\"']127\.0\.0\.1[\"']", content)


def test_openclaw_container_runtime_custom_bind_host_is_loopback():
    content = _read("alphadiana/agent/openclaw_container_runtime.py")
    assert '"customBindHost"] = "127.0.0.1"' in content


def test_openclaw_container_runtime_no_force_zero():
    content = _read("alphadiana/agent/openclaw_container_runtime.py")
    assert "0.0.0.0" not in content


def test_dashboard_run_sh_prod_uses_loopback():
    content = _read("alphadiana/dashboard/run.sh")
    assert '--host 127.0.0.1 --port "$BACKEND_PORT"' in content
    assert "--host 0.0.0.0" not in content


def test_dashboard_run_sh_dev_uses_loopback():
    content = _read("alphadiana/dashboard/run.sh")
    assert content.count("--host 127.0.0.1") >= 2


def test_quickstart_redis_port_mapping_is_loopback():
    content = _read("scripts/quickstart.sh")
    assert re.search(r'-p\s+"?\$\{?ROCK_REDIS_PORT\}?:6379"?', content) is None
    assert re.search(r"127\.0\.0\.1:\$\{ROCK_REDIS_PORT\}:6379", content)


def test_setup_alphadiana_rock_redis_port_is_loopback():
    content = _read("scripts/setup_alphadiana_rock.sh")
    assert re.search(r"127\.0\.0\.1:\$ROCK_REDIS_PORT:6379", content)
    assert not re.search(r"(?<!127\.0\.0\.1:)\$ROCK_REDIS_PORT:6379", content)


def test_terminal_bench2_default_no_network_host():
    content = _read("alphadiana/agent/terminal_bench2_common.py")
    assert 'config.get("controller_network", "host")' not in content
    assert '--network", network' in content


def test_terminal_bench2_default_no_docker_socket():
    content = _read("alphadiana/agent/terminal_bench2_common.py")
    assert 'config.get("controller_mount_docker_socket", True)' not in content


def test_terminal_bench2_unsafe_opt_in_enables_host():
    content = _read("alphadiana/agent/terminal_bench2_common.py")
    assert "unsafe_network_host" in content
