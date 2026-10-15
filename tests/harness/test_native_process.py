"""Native adapter termination must cover tools after their leader exits."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from alphadiana.harness._internal.native_process import run_native
from alphadiana.benchmarks.base import BenchmarkTask
from alphadiana.harness._internal import podman_agent
from alphadiana.harness.pi.agent import PiAgent


pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="requires Linux process groups and /proc"
)


def _running(pid):
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0]
    except (FileNotFoundError, ProcessLookupError):
        return False
    return state not in {"Z", "X"}


@pytest.mark.parametrize("exit_mode", ["timeout", "success", "error"])
@pytest.mark.parametrize("capture", [False, True])
def test_native_cleanup_kills_descendant_ignoring_sigterm(tmp_path, exit_mode, capture):
    pid_file = tmp_path / "child.pid"
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    parent = (
        "import subprocess,sys,time; from pathlib import Path\n"
        "print('native-stdout', flush=True); print('native-stderr', file=sys.stderr, flush=True)\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "deadline = time.monotonic() + 5\n"
        f"while not Path({str(pid_file)!r}).exists():\n"
        "    if time.monotonic() > deadline: raise RuntimeError('child not ready')\n"
        "    time.sleep(0.01)\n"
        + (
            "time.sleep(60)\n"
            if exit_mode == "timeout"
            else f"sys.exit({2 if exit_mode == 'error' else 0})\n"
        )
    )
    try:
        kwargs = dict(
            cwd=tmp_path, env=dict(os.environ), log=tmp_path / "native.log", timeout=2
        )
        if capture:
            kwargs["input_text"] = ""
        if exit_mode == "success":
            result = run_native([sys.executable, "-c", parent], **kwargs)
            if capture:
                assert result.returncode == 0
            else:
                assert result is None
        else:
            error = (
                subprocess.TimeoutExpired if exit_mode == "timeout"
                else subprocess.CalledProcessError if capture else RuntimeError
            )
            with pytest.raises(error):
                run_native([sys.executable, "-c", parent], **kwargs)
        log = (tmp_path / "native.log").read_text()
        assert log.count("native-stdout") == log.count("native-stderr") == 1
        pid = int(pid_file.read_text())
        deadline = time.monotonic() + 3
        while _running(pid) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not _running(pid), "Native tool remained alive after adapter returned"
    finally:
        if pid_file.exists():
            pid = int(pid_file.read_text())
            if _running(pid):
                os.kill(pid, signal.SIGKILL)


def settings(tmp_path, program):
    binary = tmp_path / "pi"
    binary.write_text("#!/usr/bin/env python3\n" + program)
    binary.chmod(0o700)
    root = tmp_path / "processes"
    root.mkdir(mode=0o700)
    return {
        "runtime": "process",
        "pi_bin": str(binary),
        "process_root": str(root),
        "api_base": "http://127.0.0.1:1/v1",
        "model": "fixture",
        "timeout": 5,
    }


def no_podman(*args, **kwargs):
    raise AssertionError("process mode invoked Podman")


def test_real_process_attachment_and_trace_without_podman(tmp_path, monkeypatch):
    monkeypatch.setattr(podman_agent, "PodmanCLI", no_podman)
    cfg = settings(
        tmp_path,
        """import json,os,sys
from pathlib import Path
assert sys.stdin.read() == "task"
assert Path("attachments/public.txt").read_text() == "hello"
assert Path(os.environ["HOME"]).parent.parent == Path.cwd().parent.parent
message={"role":"assistant","content":[{"type":"text","text":"answer"}],"stopReason":"stop"}
print(json.dumps({"type":"message_end","message":message}))
""",
    )
    agent = PiAgent()
    agent.setup(cfg)
    try:
        result = agent.solve(
            BenchmarkTask("task", "task", None, attachments={"public.txt": b"hello"})
        )
        assert result.answer == "answer"
        assert result.trajectory[-1]["content"] == "answer"
        assert result.metadata["runtime"] == "process"
        assert "image" not in result.metadata
        assert result.sandbox_metadata["isolation_mode"] == "host_process_workspace"
        assert not list(Path(cfg["process_root"]).glob("diana-harness-*"))
        assert not list(Path(cfg["process_root"]).glob("leases/*/*.json"))
    finally:
        agent.teardown()


def test_timeout_kills_spawned_tool_process(tmp_path, monkeypatch):
    monkeypatch.setattr(podman_agent, "PodmanCLI", no_podman)
    marker = tmp_path / "tool-pid"
    cfg = settings(
        tmp_path,
        f"""import subprocess,time
from pathlib import Path
child=subprocess.Popen(["sleep","120"])
Path({str(marker)!r}).write_text(str(child.pid))
time.sleep(120)
""",
    )
    cfg["timeout"] = 0.5
    agent = PiAgent()
    agent.setup(cfg)
    result = agent.solve(BenchmarkTask("task", "task", None))
    assert result.finish_reason == "timeout"
    assert marker.exists()
    pid = int(marker.read_text())
    for _ in range(30):
        stat = Path(f"/proc/{pid}/stat")
        if not stat.exists() or stat.read_text().rsplit(")", 1)[1].split()[0] == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail("tool remained alive after timeout")
    assert not list(Path(cfg["process_root"]).glob("leases/*/*.json"))


def test_default_mode_still_requires_image():
    with pytest.raises(ValueError, match="image"):
        PiAgent().setup({"api_base": "http://localhost:1", "model": "fixture"})


def test_explicit_mode_rejects_mixed_authority(tmp_path):
    cfg = settings(tmp_path, "")
    with pytest.raises(ValueError, match="not an image"):
        PiAgent().setup({**cfg, "image": "runtime:fixture"})


def test_deepseek_process_uses_owned_sdk_paths_without_podman(tmp_path, monkeypatch):
    from alphadiana.harness.deepseek_harness.agent import DeepSeekHarnessAgent

    monkeypatch.setattr(podman_agent, "PodmanCLI", no_podman)
    executable = tmp_path / "dsh-python"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        + """import json,os,sys,types
from pathlib import Path
from types import SimpleNamespace
class Harness:
    def __init__(self, **options):
        assert options["cwd"] == str(Path.cwd())
        assert options["runtime_cwd"] == str(Path.cwd())
        assert options["dsh_home"] == str(Path(os.environ["HOME"]) / "dsh-home")
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def run(self, problem, session_id):
        assert problem == "task"
        return SimpleNamespace(final_response="answer", finish_reason="stop", events=[])
module=types.ModuleType("deepseek_harness")
module.DeepSeekHarness=Harness
sys.modules["deepseek_harness"]=module
exec(compile(Path(sys.argv[1]).read_text(), sys.argv[1], "exec"))
"""
    )
    executable.chmod(0o700)
    root = tmp_path / "processes"
    root.mkdir(mode=0o700)
    agent = DeepSeekHarnessAgent()
    agent.setup(
        {
            "runtime": "process",
            "dsh_python": str(executable),
            "process_root": str(root),
            "api_base": "http://127.0.0.1:1/v1",
            "model": "fixture",
            "timeout": 5,
        }
    )
    try:
        result = agent.solve(BenchmarkTask("task", "task", None))
        assert result.answer == "answer"
        assert result.metadata["runtime"] == "process"
        assert not list(root.glob("leases/*/*.json"))
    finally:
        agent.teardown()


def test_process_rejects_two_executable_authorities(tmp_path):
    cfg = settings(tmp_path, "")
    cfg["dsh_python"] = cfg["pi_bin"]
    with pytest.raises(ValueError, match="exactly one"):
        PiAgent().setup(cfg)


@pytest.mark.parametrize("owned", [True, False])
@pytest.mark.parametrize("detached", [False, True])
def test_outer_cleanup_after_group_leader_exits(tmp_path, owned, detached):
    from alphadiana.harness._internal.native_process import (
        cleanup_execution,
        _start_ticks,
    )

    ident = os.urandom(16).hex()
    marker = tmp_path / "child-pid"
    env = {**os.environ, "ALPHADIANA_PROCESS_EXECUTION": ident if owned else "b" * 32}
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"""import subprocess,time
from pathlib import Path
child=subprocess.Popen(['sleep','120'], start_new_session={detached!r})
Path({str(marker)!r}).write_text(str(child.pid))
time.sleep(120)
""",
        ],
        env=env,
        start_new_session=True,
    )
    child = None
    try:
        for _ in range(100):
            if marker.exists():
                break
            time.sleep(0.02)
        assert marker.exists()
        child = int(marker.read_text())
        leases = tmp_path / "leases" / ident
        leases.mkdir(parents=True)
        (leases / f"{parent.pid}.json").write_text(
            json.dumps({"pid": parent.pid, "start_ticks": _start_ticks(parent.pid)})
        )
        parent.kill()
        parent.wait()
        cleanup_execution(ident, tmp_path)
        for _ in range(30):
            if not _running(child):
                break
            time.sleep(0.02)
        assert _running(child) is not owned
        assert not leases.exists()
    finally:
        try:
            os.killpg(parent.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        parent.wait()
        if child is not None and _running(child):
            os.kill(child, signal.SIGKILL)


@pytest.mark.skipif(not os.environ.get("ALPHADIANA_PI_BIN"), reason="Set ALPHADIANA_PI_BIN to an installed Pi executable")
@pytest.mark.parametrize("leader_exited", [False, True])
def test_outer_cleanup_reclaims_real_pi_detached_shell(tmp_path, leader_exited):
    from alphadiana.harness._internal.native_process import cleanup_execution
    from scripts.validation.native_model_fixture import model_server

    root = tmp_path / "processes"
    root.mkdir(mode=0o700)
    marker = tmp_path / "shell.pid"
    ident = os.urandom(16).hex()
    sibling = subprocess.Popen(
        ["sleep", "60"], start_new_session=True,
        env={**os.environ, "ALPHADIANA_PROCESS_EXECUTION": "b" * 32},
    )
    bridge_pid = shell_pid = None
    with model_server(shell_command=f"echo $$ > {marker}; exec sleep 60") as (endpoint, _):
        config = {
            "runtime": "process", "pi_bin": os.environ["ALPHADIANA_PI_BIN"],
            "process_root": str(root), "_execution_id": ident,
            "model": "fixture-v1", "timeout": 30,
            "api_base": endpoint.replace("host.containers.internal", "127.0.0.1"),
        }
        evaluator = subprocess.Popen(
            [sys.executable, "-c",
             "import json,sys; from alphadiana.harness.pi.agent import PiAgent; "
             "from alphadiana.benchmarks.base import BenchmarkTask; "
             "agent=PiAgent(); agent.setup(json.loads(sys.argv[1])); "
             "agent.solve(BenchmarkTask('cleanup', 'Run the shell task.', None))",
             json.dumps(config)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert marker.exists(), "real Pi shell did not start"
            shell_pid = int(marker.read_text())
            lease = next((root / "leases" / ident).glob("*.json"))
            bridge_pid = json.loads(lease.read_text())["pid"]
            assert os.getpgid(shell_pid) != bridge_pid
            evaluator.kill()
            evaluator.wait()
            if leader_exited:
                os.killpg(bridge_pid, signal.SIGKILL)
                deadline = time.monotonic() + 3
                while _running(bridge_pid) and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert not _running(bridge_pid)
            cleanup_execution(ident, root)
            deadline = time.monotonic() + 3
            while _running(shell_pid) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not _running(shell_pid), "Pi shell survived execution cleanup"
            assert sibling.poll() is None, "cleanup killed another execution"
            assert not (root / "leases" / ident).exists()
            cleanup_execution(ident, root)
        finally:
            evaluator.kill()
            evaluator.wait()
            # Keep this regression safe even when the implementation leaks.
            for lease in root.glob("leases/*/*.json"):
                pid = json.loads(lease.read_text())["pid"]
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for pid in (bridge_pid, shell_pid, sibling.pid):
                if pid is not None:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            sibling.wait()


@pytest.mark.skipif(not os.environ.get("ALPHADIANA_PI_BIN"), reason="Set ALPHADIANA_PI_BIN to an installed Pi executable")
@pytest.mark.parametrize("relative", [False, True])
def test_installed_pi_process_loads_candidate_from_caller_directory(tmp_path, monkeypatch, relative):
    from scripts.validation.native_model_fixture import model_server

    monkeypatch.chdir(tmp_path)
    root = tmp_path / "processes"
    root.mkdir(mode=0o700)
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "extension.ts").write_text(
        'import {writeFileSync} from "node:fs"; export default function(pi) {'
        'pi.on("session_start", () => {writeFileSync("marker.txt", "CANDIDATE_GOOD");});}'
    )
    with model_server() as (endpoint, requests):
        agent = PiAgent()
        agent.setup({
            "runtime": "process", "pi_bin": os.environ["ALPHADIANA_PI_BIN"],
            "process_root": str(root), "model": "fixture-v1", "timeout": 30,
            "api_base": endpoint.replace("host.containers.internal", "127.0.0.1"),
            "candidate_root": "candidate" if relative else str(candidate),
            "candidate_entry": "extension.ts",
            "candidate_digest": podman_agent.tree_digest(candidate),
        })
        try:
            result = agent.solve(BenchmarkTask("candidate", "Read marker using the shell.", None))
            assert result.answer == "1"
            assert len(requests) == 2
            assert any(
                message.get("role") == "tool" and "CANDIDATE_GOOD" in str(message.get("content"))
                for message in requests[-1]["messages"]
            )
        finally:
            agent.teardown()


@pytest.mark.skipif(not os.environ.get("ALPHADIANA_PI_BIN"), reason="Set ALPHADIANA_PI_BIN to an installed Pi executable")
@pytest.mark.parametrize("finalize", [None, False, True])
def test_installed_pi_process_executes_tools_and_captures_native_tokens(tmp_path, finalize):
    from scripts.validation.native_model_fixture import model_server
    from tests.harness.test_pi_training import plan
    from alphadiana.harness._internal.training import CaptureConfig

    root = tmp_path / "processes"
    root.mkdir(mode=0o700)
    with model_server(native_capture=finalize is not None,
                      policy_publication="published-1", model_revision="revision-1") as (endpoint, requests):
        config = {
            "runtime": "process",
            "pi_bin": os.environ["ALPHADIANA_PI_BIN"],
            "process_root": str(root),
            "api_base": endpoint.replace("host.containers.internal", "127.0.0.1"),
            "model": "fixture-v1",
            "timeout": 30,
            "context_window": 65536,
            "max_tokens": 32000,
        }
        if finalize is None:
            config["presence_penalty"] = 1.5
        if finalize is not None:
            training = CaptureConfig(plan(), "task-a", 0, "fixture-v1", "attempt-1",
                                     max_calls=2, finalize_at_budget=finalize).to_dict()
            training.pop("model_name")
            training.pop("attempt_id")
            config["training"] = training
        agent = PiAgent()
        agent.setup(config)
        try:
            response = agent.solve(BenchmarkTask("task-a", "Read marker using the shell.", None))
            assert response.answer == "0"
            assert len(requests) == 2
            if finalize is None:
                assert all(r.get("presence_penalty") == 1.5 for r in requests)
                assert all(
                    0 < r.get("max_tokens", r.get("max_completion_tokens", 0)) <= 32000
                    for r in requests
                ), requests
            else:
                assert all(
                    r["presence_penalty"] == 0 and r["max_tokens"] == 3
                    for r in requests
                )
            assert any(m.get("role") == "tool" and "TOOL_RAN" in str(m.get("content"))
                       for m in requests[-1]["messages"])
            assert response.metadata["runtime"] == "process"
            if finalize is not None:
                capture = response.metadata["training_capture"]
                assert len(capture["turns"]) == 2
                assert capture["tokens_native"] is True
                assert (requests[-1].get("tool_choice") == "none") is finalize
        finally:
            agent.teardown()
        assert not list(root.glob("diana-harness-*"))
        assert not list(root.glob("leases/*/*.json"))


@pytest.mark.parametrize("harness,expected,wrong", [
    ("pi", "pi_bin", "dsh_python"),
    ("deepseek_harness", "dsh_python", "pi_bin"),
])
def test_process_validation_requires_the_selected_harness_executable(tmp_path, harness, expected, wrong):
    cfg = settings(tmp_path, "")
    binary = cfg.pop("pi_bin")
    cfg[wrong] = binary
    with pytest.raises(ValueError, match=expected):
        podman_agent.validate_config(cfg, harness=harness)


def test_process_non_deadline_timeout_is_an_error_not_a_scored_zero(tmp_path, monkeypatch):
    def fail(*args):
        raise TimeoutError("fixture I/O failure")

    monkeypatch.setattr(podman_agent, "run_bridge", fail)
    agent = PiAgent()
    agent.setup(settings(tmp_path, ""))
    try:
        with pytest.raises(podman_agent.NativeHarnessError):
            agent.solve(BenchmarkTask("task", "task", None))
    finally:
        agent.teardown()


def test_process_failure_keeps_diagnostics_out_of_the_public_error(tmp_path):
    config = settings(tmp_path, 'import sys; print("private diagnostic", file=sys.stderr); sys.exit(7)')
    agent = PiAgent()
    agent.setup(config)
    try:
        with pytest.raises(podman_agent.NativeHarnessError) as error:
            agent.solve(BenchmarkTask("task", "task", None))
        assert error.value.error_type == "native_harness_execution"
        assert "private diagnostic" not in str(error.value)
        assert not list(Path(config["process_root"]).glob("leases/*/*.json"))
    finally:
        agent.teardown()
