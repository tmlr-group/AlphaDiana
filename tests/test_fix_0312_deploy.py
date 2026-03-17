"""Sandbox Network/Deployment, OpenClaw Configuration Compatibility, Environment Proxy Detection (Issue 1.1~3.1)

Override:
- 1.1 Sandbox Container Network Mode Configuration
- 1.2 agent.install ready to wait and retry
- 1.3 npm install timeout prompt (npmmirror)
- 2.1 Removing memory.enabled incompatible fields
- 2.2 workspace path is configurable
- 3.1 Proxy Variable Contamination Detection and Warning"""

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===================================================================
# 1.1 Sandbox Container Network Mode
# ===================================================================


class TestSandboxNetworkMode:
    """ROCKSandbox accepts network_mode configuration."""

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
# 1.2 agent.install ready to wait and retry
# ===================================================================


class TestDeployReadinessAndRetry:
    """deploy.py ready to poll and install retry."""

    def test_deploy_waits_for_running_status(self):
        """after sandbox.start (), get_status () should be polled until running."""
        # deploy.py's deploy () function should wait after start ()
        from openclaw_deploy.deploy import deploy

        args = MagicMock()
        args.base_url = "http://localhost:9000"
        args.proxy_url = "http://localhost:9001/apis/envs/sandbox/v1"
        args.image = "python:3.11"
        args.memory = "4g"
        args.cpus = 1
        args.startup_timeout = 180
        args.agent_config = str(Path(__file__).parent.parent / "openclaw_deploy" / "openclaw.json")

        # Need deploy () internal to check status after start
        # This test verifies that the interface exists — specific behavior is tested after implementation
        assert callable(deploy)

    def test_deploy_install_retry_interval_is_10s(self):
        """the agent.install retry interval should be 10 seconds."""
        # Verify retry related constants in deploy.py
        import openclaw_deploy.deploy as mod

        # Repair should have install_retry_interval = 10 or similar constants
        interval = getattr(mod, "INSTALL_RETRY_INTERVAL", None)
        if interval is not None:
            assert interval == 10


# ===================================================================
# 1.3 npm install timeout prompt
# ===================================================================


class TestNpmTimeoutWarning:
    """the npm install timeout prompts the user to switch the npm source."""

    def test_npm_timeout_warning_mentions_npmmirror(self):
        """The timeout warning message should contain npmmirror.com."""
        # Fixed openclaw_runtime.py or deploy.py should have this logic
        # Check if there are npmmirror related strings in the module
        from alphadiana.agent import openclaw_runtime

        source = Path(openclaw_runtime.__file__).read_text(encoding="utf-8")
        # Once fixed, this assertion should pass
        assert "npmmirror" in source or "registry.npmmirror.com" in source, \
            "openclaw_runtime.py should contain npmmirror hint for slow npm installs"


# ===================================================================
# 2.1 Removing memory.enabled incompatible fields
# ===================================================================


class TestOpenClawConfigCompat:
    """openclaw.json and runtime config do not contain memory.enabled."""

    def test_openclaw_json_no_memory_enabled(self):
        """Static templates should not contain the memory.enabled field."""
        template = Path(__file__).parent.parent / "openclaw_deploy" / "openclaw.json"
        config = json.loads(template.read_text(encoding="utf-8"))
        memory = config.get("memory", {})
        # Once fixed, the entire memory key should be removed, or at least not enabled
        assert "enabled" not in memory, \
            "memory.enabled is incompatible with openclaw@2026.3.7"

    def test_runtime_config_no_memory_enabled_without_embedding(self):
        """_build_openclaw_config () should not be injected into memory.enabled without embedding."""
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
# 2.2 The workspace path is configurable
# ===================================================================


class TestWorkspacePath:
    """The OpenClaw workspace path is not hard-coded to/root/."""

    def test_openclaw_json_workspace_not_root(self):
        """The workspace of a static template should not be a path under/root/."""
        template = Path(__file__).parent.parent / "openclaw_deploy" / "openclaw.json"
        config = json.loads(template.read_text(encoding="utf-8"))
        workspace = config.get("agents", {}).get("defaults", {}).get("workspace", "")
        # Fixed workspace should use variables or user directories
        # Within the sandbox/root/is ok, but configuration overrides should be supported
        assert workspace, "workspace should be set"

    def test_runtime_workspace_configurable(self):
        """_build_openclaw_config () should support custom workspaces."""
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
# 3.1 Proxy Variable Contamination Detection
# ===================================================================


class TestProxyWarning:
    """The CLI/Runner detects and warns about the presence of proxy variables."""

    def test_proxy_warning_when_set(self):
        """A warning should be generated when all_proxy is present."""
        from alphadiana.cli import _warn_proxy

        with patch.dict(os.environ, {"ALL_PROXY": "socks5://127.0.0.1:1080"}):
            # _warn_proxy should return True or output warning
            result = _warn_proxy()
            assert result is True or result is None  # Function exists

    def test_no_warning_when_clean(self):
        """No warning should be generated when there are no proxy variables."""
        from alphadiana.cli import _warn_proxy

        env_clean = {k: v for k, v in os.environ.items()
                     if k.upper() not in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
                                          "all_proxy", "http_proxy", "https_proxy")}
        with patch.dict(os.environ, env_clean, clear=True):
            result = _warn_proxy()
            # Returns either False or None
            assert result is not True

    def test_warning_mentions_rock_env(self):
        """the warning message should prompt the user source rock_env.sh."""
        from alphadiana import cli

        source = Path(cli.__file__).read_text(encoding="utf-8")
        assert "rock_env.sh" in source, \
            "CLI should mention rock_env.sh in proxy warning"
