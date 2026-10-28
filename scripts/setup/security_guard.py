#!/usr/bin/env python3
"""
security_guard.py — AlphaDiana/ROCK service security daemon

Usage:
  python3 security_guard.py --check          # Preflight security check (nonzero exit blocks startup)
  python3 security_guard.py --daemon         # Continuous background monitoring
  python3 security_guard.py --check --daemon # Check first, then monitor if the check passes

Integration with start_openclaw.sh:
  python3 "${SCRIPT_DIR}/security_guard.py" --check || exit 1
"""

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ─────────────────────────────── Colored output ───────────────────────────────────

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

def warn(msg: str):
    print(f"{YELLOW}[WARN]  {msg}{RESET}", file=sys.stderr)

def error(msg: str):
    print(f"{RED}{BOLD}[ERROR] {msg}{RESET}", file=sys.stderr)

def ok(msg: str):
    print(f"{GREEN}[OK]    {msg}{RESET}")

def info(msg: str):
    print(f"{CYAN}[INFO]  {msg}{RESET}")

def banner(msg: str):
    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  {msg}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")

# ─────────────────────────────── Utility functions ───────────────────────────────────

def run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return -1, "", ""


def redis_cmd(port: int, *args, password: Optional[str] = None) -> tuple[bool, str]:
    """Send a Redis command and return (success, output)."""
    cmd = ["redis-cli", "-p", str(port)]
    if password:
        cmd += ["-a", password, "--no-auth-warning"]
    cmd += list(args)
    rc, out, err = run(cmd, timeout=3)
    return rc == 0, out


def get_docker_containers() -> list[dict]:
    """Get information about all running Docker containers."""
    rc, out, _ = run(["docker", "ps", "--format", "{{json .}}"], timeout=5)
    if rc != 0 or not out:
        return []
    containers = []
    for line in out.splitlines():
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return containers


def get_container_inspect(container_id: str) -> Optional[dict]:
    rc, out, _ = run(["docker", "inspect", container_id], timeout=5)
    if rc != 0:
        return None
    try:
        return json.loads(out)[0]
    except (json.JSONDecodeError, IndexError):
        return None


def is_port_bound_public(port_bindings: dict, container_port: str = "6379/tcp") -> bool:
    """Check whether container ports are bound publicly (outside 127.0.0.1)."""
    bindings = port_bindings.get(container_port, []) or []
    for b in bindings:
        host_ip = b.get("HostIp", "")
        if host_ip in ("", "0.0.0.0", "::"):
            return True
    return False


def is_loopback_host_ip(host_ip: str) -> bool:
    return host_ip in ("127.0.0.1", "localhost", "::1", "[::1]") or host_ip.startswith("127.")


def is_redis_host_port_loopback_published(port: int) -> bool:
    """Return True when Redis is Docker-published only on a loopback host IP."""
    matched = False
    for c in get_docker_containers():
        ports = c.get("Ports", "")
        if "6379" not in ports:
            continue
        inspect = get_container_inspect(c.get("ID", ""))
        if not inspect:
            continue
        port_bindings = inspect.get("HostConfig", {}).get("PortBindings", {}) or {}
        for binding in (port_bindings.get("6379/tcp") or []):
            if str(binding.get("HostPort", "")) != str(port):
                continue
            matched = True
            if not is_loopback_host_ip(binding.get("HostIp", "")):
                return False
    return matched


def get_redis_config(port: int, key: str) -> str:
    ok_flag, out = redis_cmd(port, "CONFIG", "GET", key)
    if not ok_flag or not out:
        return ""
    lines = out.splitlines()
    return lines[1] if len(lines) >= 2 else ""


def get_redis_replication_info(port: int) -> dict:
    ok_flag, out = redis_cmd(port, "INFO", "replication")
    if not ok_flag:
        return {}
    result = {}
    for line in out.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


def get_outbound_connections_from_port(local_port: int) -> list[str]:
    """Get outbound connection destinations from local_port."""
    rc, out, _ = run(["ss", "-tnp"], timeout=3)
    if rc != 0:
        return []
    targets = []
    for line in out.splitlines():
        if f":{local_port}" in line and "ESTAB" in line:
            parts = line.split()
            if len(parts) >= 5:
                targets.append(parts[4])  # peer address
    return targets


# ─────────────────────────────── Security checks ────────────────────────────────────

class SecurityIssue:
    def __init__(self, severity: str, title: str, detail: str, fix: str = ""):
        self.severity = severity  # "CRITICAL" | "HIGH" | "WARN"
        self.title = title
        self.detail = detail
        self.fix = fix

    def __str__(self):
        color = RED if self.severity in ("CRITICAL", "HIGH") else YELLOW
        lines = [f"{color}{BOLD}[{self.severity}] {self.title}{RESET}",
                 f"         {self.detail}"]
        if self.fix:
            lines.append(f"         Fix: {self.fix}")
        return "\n".join(lines)


def check_redis_security(
    port: int,
    container_name: str = "",
    *,
    loopback_only_docker_publish: bool = False,
) -> list[SecurityIssue]:
    issues = []
    label = f"Redis(:{port})" + (f" [{container_name}]" if container_name else "")

    # Check connectivity without a password.
    success, out = redis_cmd(port, "PING")
    if not success:
        return issues  # Skip when unavailable.
    if "NOAUTH" in out:
        return issues  # Authentication is required; unauthenticated CONFIG checks are unavailable.

    # 1. Missing password
    password = get_redis_config(port, "requirepass")
    if not password and not loopback_only_docker_publish:
        issues.append(SecurityIssue(
            "CRITICAL",
            f"{label} has no password configured",
            "Anyone with access to this port can run dangerous commands such as SLAVEOF",
            f"redis-cli -p {port} CONFIG SET requirepass 'REPLACE_WITH_STRONG_PASSWORD'"
        ))

    # 2. Disabled protected-mode
    pmode = get_redis_config(port, "protected-mode")
    if pmode == "no" and not loopback_only_docker_publish:
        issues.append(SecurityIssue(
            "HIGH",
            f"{label} has protected-mode disabled",
            "Redis default protection is disabled, allowing unauthenticated non-local access",
            f"redis-cli -p {port} CONFIG SET protected-mode yes"
        ))

    # 3. Public bind interfaces
    bind = get_redis_config(port, "bind")
    if (
        bind
        and ("0.0.0.0" in bind or bind.startswith("*") or "* " in bind)
        and not loopback_only_docker_publish
    ):
        issues.append(SecurityIssue(
            "HIGH",
            f"{label} binds to all network interfaces (bind={bind!r})",
            "Redis listens on 0.0.0.0 and is publicly accessible",
            f"Set bind 127.0.0.1 in redis.conf or restart the container with -p 127.0.0.1:{port}:6379"
        ))

    # 4. Check for a slave role indicating a SLAVEOF attack.
    repl = get_redis_replication_info(port)
    role = repl.get("role", "")
    if role == "slave":
        master_host = repl.get("master_host", "?")
        master_port = repl.get("master_port", "?")
        issues.append(SecurityIssue(
            "CRITICAL",
            f"{label} currently has the SLAVE role!",
            f"Redis is syncing from {master_host}:{master_port}, possibly due to a Rogue Master attack",
            f"Run immediately: redis-cli -p {port} SLAVEOF NO ONE"
        ))

    return issues


def check_docker_redis_exposure() -> list[SecurityIssue]:
    """Check Redis exposure in all Docker containers."""
    issues = []
    containers = get_docker_containers()
    for c in containers:
        name = c.get("Names", "unknown")
        ports = c.get("Ports", "")
        # Quick check for exposed port 6379.
        if "6379" not in ports:
            continue

        inspect = get_container_inspect(c.get("ID", ""))
        if not inspect:
            continue

        port_bindings = inspect.get("HostConfig", {}).get("PortBindings", {}) or {}
        if is_port_bound_public(port_bindings):
            # Check whether the container Redis requires a password.
            host_port = None
            for binding in (port_bindings.get("6379/tcp") or []):
                hp = binding.get("HostPort")
                if hp:
                    host_port = int(hp)
                    break
            if host_port:
                sub = check_redis_security(host_port, container_name=name)
                issues.extend(sub)
            else:
                issues.append(SecurityIssue(
                    "HIGH",
                    f"Container {name!r} exposes Redis publicly",
                    f"Port bindings: {ports}",
                    "Restart the container with -p 127.0.0.1:<port>:6379"
                ))
    return issues


def check_services_binding_public() -> list[SecurityIssue]:
    """Check for unauthenticated services bound to 0.0.0.0."""
    issues = []
    rc, out, _ = run(["ps", "aux"], timeout=5)
    if rc != 0:
        return issues

    dangerous_patterns = [
        (r"uvicorn.*--host\s+0\.0\.0\.0", "FastAPI/Uvicorn backend", "Add authentication middleware or use --host 127.0.0.1"),
        (r"--host\s+0\.0\.0\.0.*uvicorn", "FastAPI/Uvicorn backend", "Add authentication middleware or use --host 127.0.0.1"),
    ]

    for line in out.splitlines():
        for pattern, svc_name, fix in dangerous_patterns:
            if re.search(pattern, line) and "grep" not in line:
                port_match = re.search(r"--port\s+(\d+)", line)
                port_info = f" (port {port_match.group(1)})" if port_match else ""
                issues.append(SecurityIssue(
                    "WARN",
                    f"{svc_name}{port_info} binds to 0.0.0.0 (publicly accessible)",
                    "The service has no authentication; public users can call its API directly",
                    fix
                ))
                break

    return issues


# OpenClaw sandbox image prefix — used to identify sandbox containers
_OPENCLAW_IMAGE_PREFIXES = (
    "ghcr.io/tsrigo/openclaw",
    "openclaw",
)

# Known weak / default tokens that must not be used in production
_WEAK_TOKENS = {"OPENCLAW", "openclaw", "test", "token", "default", "changeme", "secret", ""}


def check_openclaw_security() -> list[SecurityIssue]:
    """
    Check OpenClaw / ROCK service security configuration:
    1. Whether OPENCLAW_GATEWAY_TOKEN is a weak default token
    2. Whether ROCK Admin/Proxy binds publicly
    3. Whether sandbox container ports are exposed publicly
    """
    issues = []

    # ── 1. Token strength check ────────────────────────────────────────────────────
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if token in _WEAK_TOKENS:
        issues.append(SecurityIssue(
            "HIGH",
            "OPENCLAW_GATEWAY_TOKEN uses a weak default token",
            f"The current token is {token!r}; attackers can call the OpenClaw Gateway API directly",
            "Set a strong random token in rock_agent_config.yaml at env.OPENCLAW_GATEWAY_TOKEN"
        ))

    # Also check rock_agent_config.yaml for default values.
    deploy_dir = Path(__file__).resolve().parents[2] / "alphadiana" / "harness" / "openclaw" / "deploy"
    _config_paths = [
        deploy_dir / "rock_agent_config.yaml",
        deploy_dir / "rock_agent_config.prebuilt.yaml",
    ]
    for cfg_path in _config_paths:
        if cfg_path.exists():
            content = cfg_path.read_text()
            m = re.search(r'OPENCLAW_GATEWAY_TOKEN:\s*["\']?(\S+?)["\']?\s*(?:#|$)', content, re.MULTILINE)
            if m:
                cfg_token = m.group(1).strip('"\'')
                if cfg_token in _WEAK_TOKENS:
                    issues.append(SecurityIssue(
                        "HIGH",
                        f"Configuration file {cfg_path.name} uses weak default token {cfg_token!r}",
                        "Anyone can use this token to send requests to OpenClaw Gateway",
                        f"Set OPENCLAW_GATEWAY_TOKEN in {cfg_path} to a strong random string"
                    ))

    # ── 2. ROCK Admin / Proxy binding check ──────────────────────────────────────
    rock_bind = os.environ.get("ROCK_BIND_HOST", "127.0.0.1")
    rock_http = os.environ.get("ROCK_HTTP_HOST", rock_bind)
    admin_port = int(os.environ.get("ROCK_ADMIN_PORT", "9000"))
    proxy_port = int(os.environ.get("ROCK_PROXY_PORT", "9001"))

    if rock_http not in ("127.0.0.1", "localhost", "::1"):
        issues.append(SecurityIssue(
            "CRITICAL",
            f"ROCK Admin/Proxy binds to public address {rock_http!r}",
            f"ROCK Admin(:{admin_port}) and Proxy(:{proxy_port}) are publicly accessible; "
            "they can manage all sandboxes and forward arbitrary ports",
            "Set ROCK_HTTP_HOST=127.0.0.1 or ROCK_BIND_HOST=127.0.0.1"
        ))

    # ── 3. Sandbox container port exposure check ──────────────────────────────────────────────
    containers = get_docker_containers()
    for c in containers:
        name = c.get("Names", "unknown")
        image = c.get("Image", "")
        ports_str = c.get("Ports", "")

        is_sandbox = any(image.startswith(p) for p in _OPENCLAW_IMAGE_PREFIXES)
        if not is_sandbox:
            continue

        # Sandbox containers should not expose ports directly to the host.
        if ports_str and "->" in ports_str:
            inspect = get_container_inspect(c.get("ID", ""))
            if inspect:
                pb = inspect.get("HostConfig", {}).get("PortBindings", {}) or {}
                exposed = [
                    f"{b.get('HostIp','0.0.0.0')}:{b.get('HostPort','?')}"
                    for bindings in pb.values()
                    for b in (bindings or [])
                    if b.get("HostPort")
                ]
                if exposed:
                    issues.append(SecurityIssue(
                        "HIGH",
                        f"OpenClaw sandbox container {name!r} has ports mapped directly to the host",
                        f"Exposed ports: {', '.join(exposed)}. Access sandboxes through ROCK Proxy instead of exposing them directly",
                        "Remove -p port mappings and access sandbox services through ROCK Proxy portforward"
                    ))

    # ── 4. Check whether ROCK Admin/Proxy actually listens on public interfaces. ─────────────────────
    rc, ss_out, _ = run(["ss", "-tlnp"], timeout=3)
    if rc == 0:
        for port_num, svc_name in [(admin_port, "ROCK Admin"), (proxy_port, "ROCK Proxy")]:
            for line in ss_out.splitlines():
                if f":{port_num}" in line:
                    # Check the listening address.
                    m = re.search(r"(\S+):(\d+)\s", line)
                    if m:
                        listen_addr = m.group(1)
                        if listen_addr not in ("127.0.0.1", "::1", "[::1]"):
                            issues.append(SecurityIssue(
                                "HIGH",
                                f"{svc_name}(:{port_num}) actually listens on {listen_addr}",
                                "ROCK management is not restricted to localhost; the sandbox API is publicly accessible",
                                "Ensure ROCK_BIND_HOST=127.0.0.1 when restarting"
                            ))
                    break

    return issues


def check_redis_slave_attack(ports: list[int]) -> list[SecurityIssue]:
    """Detect Redis SLAVEOF attacks during each daemon cycle."""
    issues = []
    for port in ports:
        repl = get_redis_replication_info(port)
        role = repl.get("role", "")
        if role == "slave":
            master_host = repl.get("master_host", "?")
            master_port = repl.get("master_port", "?")
            issues.append(SecurityIssue(
                "CRITICAL",
                f"Redis(:{port}) is under a SLAVEOF attack!",
                f"Syncing from external master {master_host}:{master_port}; possibly receiving a malicious ELF file",
                f"redis-cli -p {port} SLAVEOF NO ONE"
            ))
    return issues


# ─────────────────────────────── Preflight mode ──────────────────────────────────

def check_kernel_keyring_capacity() -> list[SecurityIssue]:
    """Warn when the rootless-podman session-keyring quota is too small.

    Rootless ``podman run`` allocates a session keyring per container. Under
    high task churn (e.g. OpenCode's fresh-per-task model on short benchmarks)
    the default per-uid quota (``kernel.keys.maxkeys=200``,
    ``kernel.keys.maxbytes=20000``) is exhausted before old keys GC. The
    failure surfaces as ``error during container init: unable to join session
    keyring: disk quota exceeded`` and looks like a disk issue but is not
    (finding #12 of the Podman PR validation note).
    """
    issues: list[SecurityIssue] = []
    if sys.platform != "linux":
        return issues
    paths_min = {
        "/proc/sys/kernel/keys/maxkeys": 1000,
        "/proc/sys/kernel/keys/maxbytes": 200_000,
    }
    for path, minimum in paths_min.items():
        try:
            with open(path, encoding="utf-8") as fh:
                value = int(fh.read().strip() or "0")
        except (OSError, ValueError):
            continue
        if value < minimum:
            issues.append(SecurityIssue(
                "WARN",
                f"Kernel keyring quota is too low: {path} = {value}",
                "High-concurrency rootless podman can trigger 'disk quota exceeded' (session keyring full)",
                f"sudo sysctl -w kernel.keys.maxkeys=2000 kernel.keys.maxbytes=200000  "
                f"(also persist the settings in /etc/sysctl.d/)",
            ))
    return issues


def run_preflight_check() -> bool:
    """
    Run all preflight security checks.
    Return True to permit startup or False to block startup due to severe risks.
    """
    banner("AlphaDiana security preflight")

    # Read the Redis port from the environment, matching rock_env.sh.
    redis_port = int(os.environ.get("ROCK_REDIS_PORT", "6379"))

    all_issues: list[SecurityIssue] = []

    info(f"Checking Redis(:{redis_port}) security configuration...")
    all_issues += check_redis_security(
        redis_port,
        loopback_only_docker_publish=is_redis_host_port_loopback_published(redis_port),
    )

    info("Checking Redis exposure in Docker containers...")
    all_issues += check_docker_redis_exposure()

    info("Checking public service bindings...")
    all_issues += check_services_binding_public()

    info("Checking OpenClaw / ROCK service security configuration...")
    all_issues += check_openclaw_security()

    info("Checking kernel keyring quotas (rootless podman)...")
    all_issues += check_kernel_keyring_capacity()

    if not all_issues:
        ok("All security checks passed; startup is permitted.")
        return True

    critical = [i for i in all_issues if i.severity == "CRITICAL"]
    high = [i for i in all_issues if i.severity == "HIGH"]
    warns = [i for i in all_issues if i.severity == "WARN"]

    print()
    for issue in all_issues:
        print(issue)
        print()

    print(f"{BOLD}Check results: {RED}{len(critical)} critical issues{RESET}  "
          f"{YELLOW}{len(high)} high-risk issues{RESET}  "
          f"{len(warns)} warnings{RESET}")

    if critical or high:
        print(f"\n{RED}{BOLD}⚠  Severe security risks detected; service startup is blocked!{RESET}")
        print(f"{RED}   Fix the issues above or set SECURITY_GUARD_BYPASS=1 to bypass checks (not recommended){RESET}\n")
        if os.environ.get("SECURITY_GUARD_BYPASS") == "1":
            warn("SECURITY_GUARD_BYPASS=1 is set; bypassing security checks at your own risk")
            return True
        return False

    warn("Security warnings detected; resolve them before startup if possible. Continuing is permitted.")
    return True


# ──────────────────────────── Daemon mode ──────────────────────────────────

def get_monitored_redis_ports() -> list[int]:
    """Get the list of currently running Redis ports."""
    ports = set()

    # Read from the environment.
    env_port = os.environ.get("ROCK_REDIS_PORT")
    if env_port and env_port.isdigit():
        ports.add(int(env_port))

    # Find known Redis ports in ss output.
    rc, out, _ = run(["ss", "-tlnp"], timeout=3)
    if rc == 0:
        for line in out.splitlines():
            m = re.search(r":(\d+)\s", line)
            if m:
                p = int(m.group(1))
                if p in (6379, 6380, 6381, 6382, 20104):
                    ports.add(p)

    return sorted(ports)


def run_daemon(interval: int = 10):
    banner("Starting the AlphaDiana security daemon")
    info(f"Monitoring interval: {interval}s  PID: {os.getpid()}")
    info("Monitoring: Redis SLAVEOF attacks | Docker container exposure | Public service bindings")
    print()

    # Track reported issues to avoid duplicate alerts.
    alerted: set[str] = set()

    def handle_signal(sig, frame):
        print(f"\n{CYAN}[INFO]  Daemon received signal {sig}; exiting.{RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    cycle = 0
    while True:
        cycle += 1
        ports = get_monitored_redis_ports()
        issues: list[SecurityIssue] = []

        # 1. Detect SLAVEOF attacks on every cycle (highest priority).
        issues += check_redis_slave_attack(ports)

        # 2. Run a full check every six cycles (about one minute).
        if cycle % 6 == 1:
            issues += check_docker_redis_exposure()
            issues += check_services_binding_public()
            issues += check_openclaw_security()

        for issue in issues:
            key = f"{issue.severity}:{issue.title}"
            if key not in alerted:
                print(issue)
                print()
                alerted.add(key)

                # SLAVEOF attack: automatically run SLAVEOF NO ONE.
                if "SLAVEOF" in issue.title or "SLAVE" in issue.title:
                    port_match = re.search(r":(\d+)", issue.title)
                    if port_match:
                        p = int(port_match.group(1))
                        error(f"Automatically running SLAVEOF NO ONE on :{p}...")
                        rc, out, err = run(["redis-cli", "-p", str(p), "SLAVEOF", "NO", "ONE"])
                        if rc == 0:
                            ok(f"Redis(:{p}) has returned to Master mode")
                        else:
                            error(f"Automatic recovery failed: {err}")
        else:
            if cycle % 6 == 1 and not issues:
                ok(f"[Cycle {cycle}] All checks passed")

        # Clear resolved alerts; detect them again on the next full cycle.
        if cycle % 60 == 0:
            alerted.clear()

        time.sleep(interval)


# ─────────────────────────────── Entry point ──────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AlphaDiana/ROCK security daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Preflight check (integrated with start_openclaw.sh):
  python3 security_guard.py --check || exit 1

  # Continuous background monitoring (every 10 seconds):
  python3 security_guard.py --daemon

  # Preflight check + background monitoring:
  python3 security_guard.py --check --daemon

  # Bypass severe issues (not recommended):
  SECURITY_GUARD_BYPASS=1 python3 security_guard.py --check
        """
    )
    parser.add_argument("--check", action="store_true", help="Run preflight security checks")
    parser.add_argument("--daemon", action="store_true", help="Run the continuous monitoring daemon")
    parser.add_argument("--interval", type=int, default=10, help="Daemon check interval in seconds (default: 10)")
    args = parser.parse_args()

    if not args.check and not args.daemon:
        parser.print_help()
        sys.exit(0)

    if args.check:
        passed = run_preflight_check()
        if not passed:
            sys.exit(1)

    if args.daemon:
        run_daemon(interval=args.interval)


if __name__ == "__main__":
    main()
