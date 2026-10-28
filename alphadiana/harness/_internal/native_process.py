"""Owned subprocess execution for native CLI and JSON bridge harnesses."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path


def run_native(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log: Path,
    timeout: float,
    input_text: str | None = None,
    lease_dir: Path | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Execute a native command; optionally exchange a JSON request/response."""
    capture = input_text is not None
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE if capture else None,
            stdout=subprocess.PIPE if capture else stream,
            stderr=subprocess.PIPE if capture else subprocess.STDOUT,
            text=capture,
            start_new_session=True,
        )
        lease = None
        result = None
        try:
            if lease_dir is not None:
                lease = lease_dir / f"{process.pid}.json"
                lease.write_text(
                    json.dumps(
                        {"pid": process.pid, "start_ticks": _start_ticks(process.pid)}
                    )
                )
            if capture:
                stdout, stderr = process.communicate(input_text, timeout=timeout)
                result = subprocess.CompletedProcess(
                    process.args, process.returncode, stdout, stderr
                )
                stream.write(stdout + stderr)
                result.check_returncode()
                return result
            code = process.wait(timeout=timeout)
            if code:
                raise RuntimeError(f"Native harness exited {code}; diagnostics: {log}")
        finally:
            # Preserve the existing command lifecycle, including descendants
            # that outlive their leader or ignore SIGTERM.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                if capture and result is None:
                    stdout, stderr = process.communicate(timeout=10)
                    stream.write(stdout + stderr)
                if lease is not None:
                    lease.unlink(missing_ok=True)


def validate_process_settings(config, executable_key):
    if {"pi_bin", "dsh_python"}.intersection(config) != {executable_key}:
        raise ValueError(f"process runtime requires exactly one native executable: {executable_key}")
    for key in (executable_key, "process_root"):
        path = Path(config.get(key, ""))
        if not path.is_absolute():
            raise ValueError(f"{key} must be an absolute local path")
    executable = Path(config[executable_key])
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise ValueError(f"{executable_key} must be an installed executable")
    root = Path(config["process_root"])
    if not root.is_dir() or root.is_symlink() or root.stat().st_uid != os.getuid():
        raise ValueError("process_root must be an owned local directory")
    if root.stat().st_mode & 0o077:
        raise ValueError("process_root must have private directory permissions")


def _start_ticks(pid):
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def cleanup_execution(execution_id, process_root):
    """Reclaim recorded leaders and detached groups belonging to this execution."""
    if not re.fullmatch(r"[0-9a-f]{32}", execution_id):
        raise ValueError("invalid process execution identifier")
    directory = Path(process_root) / "leases" / execution_id
    if not directory.exists():
        return
    leases = list(directory.glob("*.json"))
    for path in leases:
        lease = json.loads(path.read_text())
        if set(lease) != {"pid", "start_ticks"} or type(lease["pid"]) is not int:
            raise ValueError("invalid process cleanup lease")
        pid = lease["pid"]
        try:
            if _start_ticks(pid) == lease["start_ticks"] and os.getpgid(pid) == pid:
                os.killpg(pid, signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError):
            pass
    # Pi starts shells in separate process groups. Reclaim them by the inherited
    # execution marker after stopping the bridges, even when a leader still existed.
    marker = f"ALPHADIANA_PROCESS_EXECUTION={execution_id}".encode()
    for member in Path("/proc").glob("[0-9]*"):
        try:
            if marker in (member / "environ").read_bytes().split(b"\0"):
                os.killpg(os.getpgid(int(member.name)), signal.SIGKILL)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    for path in leases:
        path.unlink(missing_ok=True)
    directory.rmdir()


def run_bridge(request, public, root, config, timeout, execution_id):
    """Run the same JSON bridge with an owned work/home and bounded process group."""
    work, home = root / "work", root / "home"
    work.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    request = {**request, "input_dir": str(public), "work_dir": str(work)}
    request["config"] = dict(request["config"])
    if "candidate_entry" in config:
        request["config"]["candidate_path"] = str(
            Path(config["candidate_root"]).resolve() / config["candidate_entry"]
        )
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TMPDIR"}
    }
    environment.update(
        HOME=str(home),
        PYTHONDONTWRITEBYTECODE="1",
        ALPHADIANA_PROCESS_EXECUTION=execution_id,
    )
    bridge = Path(__file__).with_name("deploy") / "bridge.py"
    leases = Path(config["process_root"]) / "leases" / execution_id
    leases.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        return run_native(
            [config.get("dsh_python", sys.executable), str(bridge)],
            cwd=work,
            env=environment,
            log=root / "bridge.log",
            timeout=timeout,
            input_text=json.dumps(request),
            lease_dir=leases,
        )
    finally:
        try:
            leases.rmdir()
        except OSError:
            pass
