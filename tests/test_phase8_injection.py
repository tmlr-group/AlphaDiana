"""Phase 8.5 shell injection and path traversal regression tests."""

from __future__ import annotations

import shlex
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_local_sandbox_run_no_shell(tmp_path):
    from alphadiana.sandbox.local import LocalSession

    sandbox = LocalSession(workdir=str(tmp_path))
    with patch("alphadiana.sandbox.local.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        sandbox.execute(["echo", "hi"])

    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("shell", False) is False


def test_local_sandbox_run_metachar_not_interpreted(tmp_path):
    from alphadiana.sandbox.local import LocalSession

    sandbox = LocalSession(workdir=str(tmp_path))
    with patch("alphadiana.sandbox.local.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        sandbox.execute(["echo", "; rm -rf /tmp/evil"])

    args, kwargs = mock_run.call_args
    assert args[0] == ["echo", "; rm -rf /tmp/evil"]
    assert kwargs.get("shell", False) is False


def test_local_sandbox_upload_rejects_dotdot(tmp_path):
    from alphadiana.sandbox.local import LocalSession

    sandbox = LocalSession(workdir=str(tmp_path))
    with pytest.raises(ValueError, match=r"traversal|escape|\.\."):
        sandbox.upload("../escape.txt", b"x")


def test_local_sandbox_upload_rejects_absolute_outside(tmp_path):
    from alphadiana.sandbox.local import LocalSession

    sandbox = LocalSession(workdir=str(tmp_path))
    with pytest.raises(ValueError, match=r"traversal|absolute|escape|invalid"):
        sandbox.upload("/etc/shadow", b"x")


def test_local_sandbox_upload_allows_nested_relative(tmp_path):
    from alphadiana.sandbox.local import LocalSession

    sandbox = LocalSession(workdir=str(tmp_path))
    sandbox.upload("subdir/file.txt", b"x")
    assert (tmp_path / "subdir" / "file.txt").read_bytes() == b"x"


def test_local_sandbox_download_rejects_dotdot(tmp_path):
    from alphadiana.sandbox.local import LocalSession

    sandbox = LocalSession(workdir=str(tmp_path))
    with pytest.raises(ValueError, match=r"traversal|escape|\.\."):
        sandbox.download("../escape.txt")


def test_zeroclaw_bridge_attachment_rejects_dotdot(tmp_path):
    from zeroclaw_deploy.zeroclaw_bridge import _write_attachments

    attachments = [{"path": "../../../etc/passwd", "data": b"x"}]
    with pytest.raises(ValueError, match=r"attachment|traversal|escape|invalid|\.\."):
        _write_attachments(tmp_path, attachments)


def test_zeroclaw_bridge_attachment_rejects_absolute(tmp_path):
    from zeroclaw_deploy.zeroclaw_bridge import _write_attachments

    attachments = [{"path": "/etc/passwd", "data": b"x"}]
    with pytest.raises(ValueError, match=r"attachment|absolute|traversal|escape|invalid"):
        _write_attachments(tmp_path, attachments)


def test_zeroclaw_bridge_attachment_allows_workspace_relative(tmp_path):
    from zeroclaw_deploy.zeroclaw_bridge import _write_attachments

    attachments = [{"path": "outputs/result.txt", "data": b"ok", "mime": "text/plain"}]
    manifest = _write_attachments(tmp_path, attachments)
    assert (tmp_path / "outputs" / "result.txt").read_bytes() == b"ok"
    assert manifest[0]["path"] == "outputs/result.txt"


def test_swebench_container_paths_quoted():
    from alphadiana.sandbox.swebench_container import SWEBenchContainerSession

    container = SWEBenchContainerSession.__new__(SWEBenchContainerSession)
    container._container = MagicMock()
    container._container.put_archive.return_value = True
    container._workdir = "/testbed"
    container._user = "root"
    parent = "/tmp/has space; echo pwned"

    container.upload(f"{parent}/file.txt", b"x")

    args, kwargs = container._container.exec_run.call_args
    cmd = args[0] if args else kwargs.get("cmd", "")
    shell_command = cmd[2] if isinstance(cmd, list) and cmd[:2] == ["/bin/bash", "-lc"] else cmd
    assert shlex.quote(parent) in shell_command or (
        isinstance(cmd, list) and cmd[:2] != ["/bin/bash", "-lc"]
    )


def test_swebench_container_metacharacter_no_injection():
    from alphadiana.sandbox.swebench_container import SWEBenchContainerSession

    container = SWEBenchContainerSession.__new__(SWEBenchContainerSession)
    container._container = MagicMock()
    container._workdir = "/testbed"
    container._user = "root"
    target = "/tmp/evil; rm -rf /tmp/pwned"
    container._container.exec_run.return_value = SimpleNamespace(exit_code=0, output=(b"x", b""))

    container.read_text(target)

    args, kwargs = container._container.exec_run.call_args
    cmd = args[0] if args else kwargs.get("cmd", "")
    shell_command = cmd[2] if isinstance(cmd, list) and cmd[:2] == ["/bin/bash", "-lc"] else cmd
    assert shlex.quote(target) in shell_command or (
        isinstance(cmd, list) and cmd[:2] != ["/bin/bash", "-lc"]
    )


def test_zeroclaw_runtime_path_interpolation_quoted(tmp_path):
    from alphadiana.agent.zeroclaw_runtime import ZeroClawRuntimeManager

    bridge = tmp_path / "bridge template.py"
    bridge.write_text("print('bridge')", encoding="utf-8")
    sandbox = MagicMock()
    sandbox.sandbox_id = "sandbox-1"
    sandbox.upload.return_value = None
    sandbox.execute.side_effect = [
        SimpleNamespace(exit_code=0, stdout="", stderr=""),
        SimpleNamespace(exit_code=0, stdout="zeroclaw 1.0", stderr=""),
        SimpleNamespace(exit_code=0, stdout="", stderr=""),
        SimpleNamespace(exit_code=0, stdout="", stderr=""),
    ]
    manager = ZeroClawRuntimeManager(
        {
            "bridge_template_path": str(bridge),
            "remote_bridge_path": "/tmp/bridge path.py",
            "bridge_log_path": "/tmp/bridge log.txt",
            "bridge_pidfile": "/tmp/bridge pid.txt",
            "gateway_startup_timeout": 0,
        }
    )

    with (
        patch.object(manager, "_wait_for_gateway", return_value=None),
        patch.object(manager, "_warmup_gateway", return_value=None),
    ):
        manager.ensure_ready(sandbox)

    start_command = sandbox.execute.call_args_list[2].args[0]
    assert shlex.quote("/tmp/bridge path.py") in start_command
    assert shlex.quote("/tmp/bridge log.txt") in start_command


def test_openclaw_container_runtime_env_quoted():
    from alphadiana.agent.openclaw_container_runtime import OpenClawContainerRuntimeManager

    manager = OpenClawContainerRuntimeManager(
        {
            "gateway_token": "OPEN_CLAW-1",
            "openclaw_home": "/tmp/open claw",
            "workspace_path": "/tmp/work space",
            "gateway_log_path": "/tmp/gateway log.txt",
        }
    )
    command = manager._build_start_gateway_command()

    assert shlex.quote("/tmp/open claw") in command
    assert shlex.quote("/tmp/work space") in command
    assert f"OPENCLAW_GATEWAY_TOKEN={shlex.quote('OPEN_CLAW-1')}" in command


def test_openclaw_container_runtime_rejects_bad_token_charset():
    from alphadiana.agent.openclaw_container_runtime import OpenClawContainerRuntimeManager

    with pytest.raises(ValueError, match=r"token|charset|invalid"):
        OpenClawContainerRuntimeManager({"gateway_token": "abc;rm -rf /"})
