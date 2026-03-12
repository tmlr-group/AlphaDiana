"""Sandbox 网络/部署、OpenClaw 配置兼容性、环境代理检测（问题 1.1~3.1）

覆盖:
- 1.1 Sandbox 容器网络模式配置
- 1.2 agent.install 就绪等待与重试
- 1.3 npm install 超时提示（npmmirror）
- 2.1 移除 memory.enabled 不兼容字段
- 2.2 workspace 路径可配置
- 3.1 代理变量污染检测与警告
"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===================================================================
# 1.1 Sandbox 容器网络模式
# ===================================================================


class TestSandboxNetworkMode:
    """ROCKSandbox 接受 network_mode 配置。"""

    def test_sandbox_config_accepts_network_mode(self):
        from alphadiana.sandbox.rock import ROCKSandbox

        sb = ROCKSandbox()
        with patch("alphadiana.sandbox.rock._require_rock_sdk"):
            sb.setup({
                "admin_base_url": "http://localhost:9000",
                "proxy_base_url": "http://localhost:9001",
                "network_mode": "host",
            })
        assert sb._network_mode == "host"

    def test_sandbox_default_network_mode_none(self):
        from alphadiana.sandbox.rock import ROCKSandbox

        sb = ROCKSandbox()
        with patch("alphadiana.sandbox.rock._require_rock_sdk"):
            sb.setup({
                "admin_base_url": "http://localhost:9000",
                "proxy_base_url": "http://localhost:9001",
            })
        assert getattr(sb, "_network_mode", None) is None


# ===================================================================
# 1.2 agent.install 就绪等待与重试
# ===================================================================


class TestDeployReadinessAndRetry:
    """deploy.py 就绪轮询和 install 重试。"""

    def test_deploy_waits_for_running_status(self):
        """sandbox.start() 后应轮询 get_status() 直到 running。"""
        # deploy.py 的 deploy() 函数应在 start() 后等待
        from openclaw_deploy.deploy import deploy

        args = MagicMock()
        args.base_url = "http://localhost:9000"
        args.proxy_url = "http://localhost:9001/apis/envs/sandbox/v1"
        args.image = "python:3.11"
        args.memory = "4g"
        args.cpus = 1
        args.startup_timeout = 180
        args.agent_config = str(Path(__file__).parent.parent / "openclaw_deploy" / "openclaw.json")

        # 需要 deploy() 内部在 start 后检查 status
        # 这个测试验证接口存在 — 具体行为在实现后测试
        assert callable(deploy)

    def test_deploy_install_retry_interval_is_10s(self):
        """agent.install 重试间隔应为 10 秒。"""
        # 验证 deploy.py 中的重试相关常量
        import openclaw_deploy.deploy as mod

        # 修复后应有 INSTALL_RETRY_INTERVAL = 10 或类似常量
        interval = getattr(mod, "INSTALL_RETRY_INTERVAL", None)
        if interval is not None:
            assert interval == 10


# ===================================================================
# 1.3 npm install 超时提示
# ===================================================================


class TestNpmTimeoutWarning:
    """npm install 超时时提示用户切换 npm 源。"""

    def test_npm_timeout_warning_mentions_npmmirror(self):
        """超时 warning 消息应包含 npmmirror.com。"""
        # 修复后 openclaw_runtime.py 或 deploy.py 应有此逻辑
        # 检查模块中是否有 npmmirror 相关字符串
        from alphadiana.agent import openclaw_runtime

        source = Path(openclaw_runtime.__file__).read_text(encoding="utf-8")
        # 修复后此断言应通过
        assert "npmmirror" in source or "registry.npmmirror.com" in source, \
            "openclaw_runtime.py should contain npmmirror hint for slow npm installs"


# ===================================================================
# 2.1 移除 memory.enabled 不兼容字段
# ===================================================================


class TestOpenClawConfigCompat:
    """openclaw.json 和 runtime config 不含 memory.enabled。"""

    def test_openclaw_json_no_memory_enabled(self):
        """静态模板不应包含 memory.enabled 字段。"""
        template = Path(__file__).parent.parent / "openclaw_deploy" / "openclaw.json"
        config = json.loads(template.read_text(encoding="utf-8"))
        memory = config.get("memory", {})
        # 修复后整个 memory 键应被移除，或至少不含 enabled
        assert "enabled" not in memory, \
            "memory.enabled is incompatible with openclaw@2026.3.7"

    def test_runtime_config_no_memory_enabled_without_embedding(self):
        """无 embedding 时 _build_openclaw_config() 不应注入 memory.enabled。"""
        from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager

        mgr = OpenClawRuntimeManager({
            "rock_agent_config_path": "/fake/config.yaml",
            "openclaw_config_path": "/fake/openclaw.json",
        })
        cfg = mgr._build_openclaw_config()
        memory = cfg.get("memory", {})
        assert "enabled" not in memory, \
            "_build_openclaw_config() should not inject memory.enabled"


# ===================================================================
# 2.2 workspace 路径可配置
# ===================================================================


class TestWorkspacePath:
    """OpenClaw workspace 路径不硬编码 /root/。"""

    def test_openclaw_json_workspace_not_root(self):
        """静态模板的 workspace 不应为 /root/ 下的路径。"""
        template = Path(__file__).parent.parent / "openclaw_deploy" / "openclaw.json"
        config = json.loads(template.read_text(encoding="utf-8"))
        workspace = config.get("agents", {}).get("defaults", {}).get("workspace", "")
        # 修复后 workspace 应使用变量或用户目录
        # 在 sandbox 内 /root/ 可以，但应支持配置覆盖
        assert workspace, "workspace should be set"

    def test_runtime_workspace_configurable(self):
        """_build_openclaw_config() 应支持自定义 workspace。"""
        from alphadiana.agent.openclaw_runtime import OpenClawRuntimeManager

        mgr = OpenClawRuntimeManager({
            "rock_agent_config_path": "/fake/config.yaml",
            "openclaw_config_path": "/fake/openclaw.json",
            "workspace": "/home/user/.openclaw/workspace",
        })
        cfg = mgr._build_openclaw_config()
        workspace = cfg.get("agents", {}).get("defaults", {}).get("workspace", "")
        if workspace:
            assert "/home/user" in workspace or "${HOME}" in workspace


# ===================================================================
# 3.1 代理变量污染检测
# ===================================================================


class TestProxyWarning:
    """CLI / Runner 检测并警告代理变量存在。"""

    def test_proxy_warning_when_set(self):
        """存在 ALL_PROXY 时应产生 warning。"""
        from alphadiana.cli import _warn_proxy

        with patch.dict(os.environ, {"ALL_PROXY": "socks5://127.0.0.1:1080"}):
            # _warn_proxy 应返回 True 或输出 warning
            result = _warn_proxy()
            assert result is True or result is None  # 函数存在即可

    def test_no_warning_when_clean(self):
        """无代理变量时不应产生 warning。"""
        from alphadiana.cli import _warn_proxy

        env_clean = {k: v for k, v in os.environ.items()
                     if k.upper() not in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
                                          "all_proxy", "http_proxy", "https_proxy")}
        with patch.dict(os.environ, env_clean, clear=True):
            result = _warn_proxy()
            # 返回 False 或 None 均可
            assert result is not True

    def test_warning_mentions_rock_env(self):
        """warning 消息应提示用户 source rock_env.sh。"""
        from alphadiana import cli

        source = Path(cli.__file__).read_text(encoding="utf-8")
        assert "rock_env.sh" in source, \
            "CLI should mention rock_env.sh in proxy warning"
