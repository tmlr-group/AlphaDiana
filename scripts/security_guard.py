#!/usr/bin/env python3
"""
security_guard.py — AlphaDiana/ROCK 服务安全守护进程

用法：
  python3 security_guard.py --check          # 启动前安全检查（返回非0则禁止启动）
  python3 security_guard.py --daemon         # 后台持续监控
  python3 security_guard.py --check --daemon # 先检查，通过后进入监控模式

集成到 start_openclaw.sh：
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

# ─────────────────────────────── 颜色输出 ───────────────────────────────────

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

# ─────────────────────────────── 工具函数 ───────────────────────────────────

def run(cmd: list[str], timeout: int = 5) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return -1, "", ""


def redis_cmd(port: int, *args, password: Optional[str] = None) -> tuple[bool, str]:
    """向 Redis 发送命令，返回 (成功, 输出)"""
    cmd = ["redis-cli", "-p", str(port)]
    if password:
        cmd += ["-a", password, "--no-auth-warning"]
    cmd += list(args)
    rc, out, err = run(cmd, timeout=3)
    return rc == 0, out


def get_docker_containers() -> list[dict]:
    """获取所有运行中的 Docker 容器信息"""
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
    """检查容器端口是否绑定到公网（非127.0.0.1）"""
    bindings = port_bindings.get(container_port, []) or []
    for b in bindings:
        host_ip = b.get("HostIp", "")
        if host_ip in ("", "0.0.0.0", "::"):
            return True
    return False


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
    """获取来自 local_port 的出站连接目标"""
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


# ─────────────────────────────── 检查项 ────────────────────────────────────

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
            lines.append(f"         修复: {self.fix}")
        return "\n".join(lines)


def check_redis_security(port: int, container_name: str = "") -> list[SecurityIssue]:
    issues = []
    label = f"Redis(:{port})" + (f" [{container_name}]" if container_name else "")

    # 检查是否可连接（无密码直连）
    success, out = redis_cmd(port, "PING")
    if not success:
        return issues  # 连不上则跳过
    if "NOAUTH" in out:
        return issues  # 已要求认证，后续无密码 CONFIG 检查不可用

    # 1. 无密码
    password = get_redis_config(port, "requirepass")
    if not password:
        issues.append(SecurityIssue(
            "CRITICAL",
            f"{label} 未设置密码",
            "任何能访问该端口的人均可执行 SLAVEOF 等危险命令",
            f"redis-cli -p {port} CONFIG SET requirepass '强密码'"
        ))

    # 2. protected-mode 关闭
    pmode = get_redis_config(port, "protected-mode")
    if pmode == "no":
        issues.append(SecurityIssue(
            "HIGH",
            f"{label} protected-mode 已关闭",
            "Redis 默认保护机制被禁用，允许非本地无密码访问",
            f"redis-cli -p {port} CONFIG SET protected-mode yes"
        ))

    # 3. bind 包含公网接口
    bind = get_redis_config(port, "bind")
    if bind and ("0.0.0.0" in bind or bind.startswith("*") or "* " in bind):
        issues.append(SecurityIssue(
            "HIGH",
            f"{label} 绑定到所有网络接口 (bind={bind!r})",
            "Redis 监听 0.0.0.0，公网可直接访问",
            f"在 redis.conf 中设置 bind 127.0.0.1 或重启容器时用 -p 127.0.0.1:{port}:6379"
        ))

    # 4. 检查当前是否为 slave（已遭 SLAVEOF 攻击）
    repl = get_redis_replication_info(port)
    role = repl.get("role", "")
    if role == "slave":
        master_host = repl.get("master_host", "?")
        master_port = repl.get("master_port", "?")
        issues.append(SecurityIssue(
            "CRITICAL",
            f"{label} 当前角色为 SLAVE！",
            f"Redis 正在从 {master_host}:{master_port} 同步数据，可能遭受 Rogue Master 攻击",
            f"立即执行: redis-cli -p {port} SLAVEOF NO ONE"
        ))

    return issues


def check_docker_redis_exposure() -> list[SecurityIssue]:
    """检查所有 Docker 容器中的 Redis 服务暴露情况"""
    issues = []
    containers = get_docker_containers()
    for c in containers:
        name = c.get("Names", "unknown")
        ports = c.get("Ports", "")
        # 快速判断：是否有 6379 端口对外暴露
        if "6379" not in ports:
            continue

        inspect = get_container_inspect(c.get("ID", ""))
        if not inspect:
            continue

        port_bindings = inspect.get("HostConfig", {}).get("PortBindings", {}) or {}
        if is_port_bound_public(port_bindings):
            # 进一步检查该容器的 Redis 是否有密码
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
                    f"容器 {name!r} 将 Redis 暴露到公网",
                    f"端口绑定: {ports}",
                    "重新启动容器时使用 -p 127.0.0.1:<port>:6379"
                ))
    return issues


def check_services_binding_public() -> list[SecurityIssue]:
    """检查是否有服务绑定到 0.0.0.0 且无认证"""
    issues = []
    rc, out, _ = run(["ps", "aux"], timeout=5)
    if rc != 0:
        return issues

    dangerous_patterns = [
        (r"uvicorn.*--host\s+0\.0\.0\.0", "FastAPI/Uvicorn 后端", "添加认证中间件或改为 --host 127.0.0.1"),
        (r"--host\s+0\.0\.0\.0.*uvicorn", "FastAPI/Uvicorn 后端", "添加认证中间件或改为 --host 127.0.0.1"),
    ]

    for line in out.splitlines():
        for pattern, svc_name, fix in dangerous_patterns:
            if re.search(pattern, line) and "grep" not in line:
                port_match = re.search(r"--port\s+(\d+)", line)
                port_info = f" (端口 {port_match.group(1)})" if port_match else ""
                issues.append(SecurityIssue(
                    "WARN",
                    f"{svc_name}{port_info} 绑定到 0.0.0.0（公网可访问）",
                    "该服务无身份验证，公网用户可直接调用 API",
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
    检查 OpenClaw / ROCK 服务的安全配置：
    1. OPENCLAW_GATEWAY_TOKEN 是否为默认弱 Token
    2. ROCK Admin/Proxy 是否绑定到公网
    3. 沙箱容器是否有端口暴露到公网
    """
    issues = []

    # ── 1. Token 强度检查 ────────────────────────────────────────────────────
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "")
    if token in _WEAK_TOKENS:
        issues.append(SecurityIssue(
            "HIGH",
            "OPENCLAW_GATEWAY_TOKEN 使用默认弱 Token",
            f"当前 Token 为 {token!r}，攻击者可直接调用 OpenClaw Gateway API",
            "在 rock_agent_config.yaml 的 env.OPENCLAW_GATEWAY_TOKEN 中设置高强度随机 Token"
        ))

    # 也检查 rock_agent_config.yaml 中是否还是默认值
    _config_paths = [
        Path(__file__).parent.parent / "openclaw_deploy" / "rock_agent_config.yaml",
        Path(__file__).parent.parent / "openclaw_deploy" / "rock_agent_config.prebuilt.yaml",
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
                        f"配置文件 {cfg_path.name} 中使用默认弱 Token {cfg_token!r}",
                        "任何人都可以用此 Token 向 OpenClaw Gateway 发出请求",
                        f"修改 {cfg_path} 中的 OPENCLAW_GATEWAY_TOKEN 为高强度随机字符串"
                    ))

    # ── 2. ROCK Admin / Proxy 绑定检查 ──────────────────────────────────────
    rock_bind = os.environ.get("ROCK_BIND_HOST", "127.0.0.1")
    rock_http = os.environ.get("ROCK_HTTP_HOST", rock_bind)
    admin_port = int(os.environ.get("ROCK_ADMIN_PORT", "9000"))
    proxy_port = int(os.environ.get("ROCK_PROXY_PORT", "9001"))

    if rock_http not in ("127.0.0.1", "localhost", "::1"):
        issues.append(SecurityIssue(
            "CRITICAL",
            f"ROCK Admin/Proxy 绑定到公网地址 {rock_http!r}",
            f"ROCK Admin(:{admin_port}) 和 Proxy(:{proxy_port}) 将对公网开放，"
            "可管理所有沙箱并转发任意端口",
            "设置环境变量 ROCK_HTTP_HOST=127.0.0.1 或 ROCK_BIND_HOST=127.0.0.1"
        ))

    # ── 3. 沙箱容器端口暴露检查 ──────────────────────────────────────────────
    containers = get_docker_containers()
    for c in containers:
        name = c.get("Names", "unknown")
        image = c.get("Image", "")
        ports_str = c.get("Ports", "")

        is_sandbox = any(image.startswith(p) for p in _OPENCLAW_IMAGE_PREFIXES)
        if not is_sandbox:
            continue

        # 沙箱容器不应该有任何端口直接暴露到宿主机
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
                        f"OpenClaw 沙箱容器 {name!r} 有端口直接映射到宿主机",
                        f"暴露端口: {', '.join(exposed)}。沙箱应通过 ROCK Proxy 访问，不应直接暴露",
                        "移除 -p 端口映射，通过 ROCK Proxy portforward 访问沙箱服务"
                    ))

    # ── 4. 检查 ROCK Admin/Proxy 是否实际监听在公网接口 ─────────────────────
    rc, ss_out, _ = run(["ss", "-tlnp"], timeout=3)
    if rc == 0:
        for port_num, svc_name in [(admin_port, "ROCK Admin"), (proxy_port, "ROCK Proxy")]:
            for line in ss_out.splitlines():
                if f":{port_num}" in line:
                    # 检查监听地址
                    m = re.search(r"(\S+):(\d+)\s", line)
                    if m:
                        listen_addr = m.group(1)
                        if listen_addr not in ("127.0.0.1", "::1", "[::1]"):
                            issues.append(SecurityIssue(
                                "HIGH",
                                f"{svc_name}(:{port_num}) 实际监听在 {listen_addr}",
                                "ROCK 管理接口未限制到本地，公网可直接访问沙箱 API",
                                "重启时确保 ROCK_BIND_HOST=127.0.0.1"
                            ))
                    break

    return issues


def check_redis_slave_attack(ports: list[int]) -> list[SecurityIssue]:
    """实时检测 Redis 是否遭受 SLAVEOF 攻击（守护进程中循环调用）"""
    issues = []
    for port in ports:
        repl = get_redis_replication_info(port)
        role = repl.get("role", "")
        if role == "slave":
            master_host = repl.get("master_host", "?")
            master_port = repl.get("master_port", "?")
            issues.append(SecurityIssue(
                "CRITICAL",
                f"Redis(:{port}) 遭受 SLAVEOF 攻击！",
                f"正在从外部主库 {master_host}:{master_port} 同步——可能正在接收恶意 ELF 文件",
                f"redis-cli -p {port} SLAVEOF NO ONE"
            ))
    return issues


# ─────────────────────────────── 预检模式 ──────────────────────────────────

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
                f"内核 keyring 配额过低: {path} = {value}",
                "rootless podman 在高并发场景下会触发 'disk quota exceeded' (session keyring 满)",
                f"sudo sysctl -w kernel.keys.maxkeys=2000 kernel.keys.maxbytes=200000  "
                f"(并写入 /etc/sysctl.d/ 持久化)",
            ))
    return issues


def run_preflight_check() -> bool:
    """
    执行所有启动前安全检查。
    返回 True 表示安全可以继续，False 表示存在严重风险禁止启动。
    """
    banner("AlphaDiana 安全预检")

    # 从环境变量读取 Redis 端口（与 rock_env.sh 保持一致）
    redis_port = int(os.environ.get("ROCK_REDIS_PORT", "6379"))

    all_issues: list[SecurityIssue] = []

    info(f"检查 Redis(:{redis_port}) 安全配置...")
    all_issues += check_redis_security(redis_port)

    info("检查 Docker 容器 Redis 暴露情况...")
    all_issues += check_docker_redis_exposure()

    info("检查服务公网绑定...")
    all_issues += check_services_binding_public()

    info("检查 OpenClaw / ROCK 服务安全配置...")
    all_issues += check_openclaw_security()

    info("检查内核 keyring 配额 (rootless podman) ...")
    all_issues += check_kernel_keyring_capacity()

    if not all_issues:
        ok("所有安全检查通过，允许启动。")
        return True

    critical = [i for i in all_issues if i.severity == "CRITICAL"]
    high = [i for i in all_issues if i.severity == "HIGH"]
    warns = [i for i in all_issues if i.severity == "WARN"]

    print()
    for issue in all_issues:
        print(issue)
        print()

    print(f"{BOLD}检查结果: {RED}{len(critical)} 个严重问题{RESET}  "
          f"{YELLOW}{len(high)} 个高危问题{RESET}  "
          f"{len(warns)} 个警告{RESET}")

    if critical or high:
        print(f"\n{RED}{BOLD}⚠  存在严重安全风险，禁止启动服务！{RESET}")
        print(f"{RED}   请先修复上述问题，或设置环境变量 SECURITY_GUARD_BYPASS=1 强制跳过（不推荐）{RESET}\n")
        if os.environ.get("SECURITY_GUARD_BYPASS") == "1":
            warn("SECURITY_GUARD_BYPASS=1 已设置，强制跳过安全检查（风险自负）")
            return True
        return False

    warn("存在安全警告，建议修复后再启动。本次允许继续。")
    return True


# ──────────────────────────── 守护进程模式 ──────────────────────────────────

def get_monitored_redis_ports() -> list[int]:
    """获取当前所有运行中的 Redis 端口列表"""
    ports = set()

    # 从环境变量获取
    env_port = os.environ.get("ROCK_REDIS_PORT")
    if env_port and env_port.isdigit():
        ports.add(int(env_port))

    # 从 ss 扫描已知 Redis 端口
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
    banner("AlphaDiana 安全守护进程启动")
    info(f"监控间隔: {interval}秒  PID: {os.getpid()}")
    info("监控项: Redis SLAVEOF攻击 | Docker容器暴露 | 服务公网绑定")
    print()

    # 记录已告警的问题，避免重复告警
    alerted: set[str] = set()

    def handle_signal(sig, frame):
        print(f"\n{CYAN}[INFO]  守护进程收到信号 {sig}，退出。{RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    cycle = 0
    while True:
        cycle += 1
        ports = get_monitored_redis_ports()
        issues: list[SecurityIssue] = []

        # 1. 检测 SLAVEOF 攻击（最高优先级，每轮必查）
        issues += check_redis_slave_attack(ports)

        # 2. 每 6 轮（约 1 分钟）做一次完整检查
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

                # SLAVEOF 攻击：自动执行 SLAVEOF NO ONE
                if "SLAVEOF" in issue.title or "SLAVE" in issue.title:
                    port_match = re.search(r":(\d+)", issue.title)
                    if port_match:
                        p = int(port_match.group(1))
                        error(f"自动执行 SLAVEOF NO ONE on :{p}...")
                        rc, out, err = run(["redis-cli", "-p", str(p), "SLAVEOF", "NO", "ONE"])
                        if rc == 0:
                            ok(f"Redis(:{p}) 已恢复为 Master 模式")
                        else:
                            error(f"自动恢复失败: {err}")
        else:
            if cycle % 6 == 1 and not issues:
                ok(f"[轮次 {cycle}] 所有检查通过")

        # 清除已解决的告警（下一个完整轮次重新检测）
        if cycle % 60 == 0:
            alerted.clear()

        time.sleep(interval)


# ─────────────────────────────── 入口 ──────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AlphaDiana/ROCK 安全守护进程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 启动前检查（集成到 start_openclaw.sh）:
  python3 security_guard.py --check || exit 1

  # 后台持续监控（每10秒一次）:
  python3 security_guard.py --daemon

  # 仅检查 + 后台监控:
  python3 security_guard.py --check --daemon

  # 强制跳过严重问题（不推荐）:
  SECURITY_GUARD_BYPASS=1 python3 security_guard.py --check
        """
    )
    parser.add_argument("--check", action="store_true", help="执行启动前安全检查")
    parser.add_argument("--daemon", action="store_true", help="进入持续监控守护进程模式")
    parser.add_argument("--interval", type=int, default=10, help="守护进程检查间隔（秒，默认10）")
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
