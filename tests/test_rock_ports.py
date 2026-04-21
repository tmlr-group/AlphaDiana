from __future__ import annotations

from types import SimpleNamespace

from pathlib import Path

from alphadiana.utils.rock_ports import (
    PROJECT_ROOT,
    RockPorts,
    check_rock_service_ownership,
    check_rock_services,
    default_rock_ray_tmpdir,
)


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


def test_check_rock_service_ownership_flags_foreign_checkout(monkeypatch, tmp_path) -> None:
    foreign_root = tmp_path / "foreign"
    (foreign_root / "alphadiana").mkdir(parents=True)
    (foreign_root / "scripts").mkdir()

    monkeypatch.setattr(
        "alphadiana.utils.rock_ports._pid_for_port",
        lambda port: 1234 if port == 9120 else 2345,
    )
    monkeypatch.setattr(
        "alphadiana.utils.rock_ports._read_proc_environ",
        lambda pid: {
            "PWD": str(foreign_root / "ref" / "ROCK"),
            "PYTHONPATH": str(foreign_root),
        },
    )
    monkeypatch.setattr(
        "alphadiana.utils.rock_ports._read_proc_cwd",
        lambda pid: str(foreign_root / "ref" / "ROCK"),
    )
    monkeypatch.setattr(
        "alphadiana.utils.rock_ports._read_proc_cmdline",
        lambda pid: f"python -m rock.admin.main --port {pid}",
    )

    ports = RockPorts(
        ray_port=6480,
        ray_dashboard_port=8365,
        ray_client_server_port=30100,
        redis_port=6390,
        admin_port=9120,
        proxy_port=9121,
    )
    results = check_rock_service_ownership(ports, expected_project_root=Path(PROJECT_ROOT))

    assert "foreign checkout owns port 9120" in str(results["admin"])
    assert "foreign checkout owns port 9121" in str(results["proxy"])


def test_default_rock_ray_tmpdir_is_short_tmp_path(tmp_path) -> None:
    ray_tmpdir = default_rock_ray_tmpdir(tmp_path / "AlphaDiana-full2")

    assert ray_tmpdir.startswith("/tmp/")
    assert len(ray_tmpdir) < 40
