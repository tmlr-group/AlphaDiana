"""Helpers for tuning ROCK runtime environment installation."""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Default sandbox image. Override via OPENCLAW_SANDBOX_IMAGE or OPENCLAW_IMAGE.
# ---------------------------------------------------------------------------
DEFAULT_SANDBOX_IMAGE: str = os.environ.get(
    "OPENCLAW_SANDBOX_IMAGE",
    os.environ.get("OPENCLAW_IMAGE", "ghcr.io/tsrigo/openclaw-reasoning:20260320"),
)

# Canonical pre-built image that ships with Node.js + openclaw already installed.
# Override via OPENCLAW_PREBUILT_IMAGE env var if needed.
PREBUILT_SANDBOX_IMAGE: str = os.environ.get(
    "OPENCLAW_PREBUILT_IMAGE", "ghcr.io/tsrigo/openclaw-reasoning:20260320"
)

# Full npm install command (used when running on a bare python:3.11 image).
OPENCLAW_NPM_INSTALL_CMD = "npm install -g openclaw@2026.3.7 --omit=optional"

# ---------------------------------------------------------------------------
# Node.js download commands
# ---------------------------------------------------------------------------
_NODE_MIRROR = os.environ.get("NPM_REGISTRY", "https://registry.npmmirror.com")
# Derive the Node.js binary mirror from the registry host (npmmirror.com -> npmmirror.com/mirrors/node).
# For custom registries, set NODE_MIRROR env var directly.
_NODE_MIRROR_BASE = os.environ.get(
    "NODE_MIRROR", "https://npmmirror.com/mirrors/node"
)
NODE_RUNTIME_MIRROR_INSTALL_CMD = (
    "[ -f node.tar.xz ] && rm node.tar.xz; "
    "[ -d runtime-env ] && rm -rf runtime-env; "
    "[ -d node-v22.18.0-linux-x64 ] && rm -rf node-v22.18.0-linux-x64; "
    f"wget -q -O node.tar.xz --tries=10 --waitretry=2 "
    f"{_NODE_MIRROR_BASE}/v22.18.0/node-v22.18.0-linux-x64.tar.xz "
    "&& tar -xf node.tar.xz && mv node-v22.18.0-linux-x64 runtime-env"
)

# For pre-built images: node is already at /opt/node, just create the symlink
# that ROCK expects so ${bin_dir} resolves correctly.
NODE_PREBUILT_INSTALL_CMD = "ln -sfn /opt/node runtime-env"


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

_PREBUILT_IMAGE_PREFIXES = (
    "ghcr.io/tsrigo/openclaw-reasoning:",
    "openclaw-reasoning:",
)


def is_prebuilt_image(image: str) -> bool:
    """Return True if *image* is a known pre-built sandbox image."""
    return any(image.startswith(prefix) for prefix in _PREBUILT_IMAGE_PREFIXES)


def get_custom_install_cmd(image: str) -> str:
    """Return the ``custom_install_cmd`` appropriate for *image*."""
    if is_prebuilt_image(image):
        return "openclaw --version"
    return OPENCLAW_NPM_INSTALL_CMD


def configure_rock_runtime_for_image(image: str) -> None:
    """Set ROCK env-vars appropriate for *image*.

    For pre-built images the Node.js download is replaced by a symlink;
    for bare images we use the fast npmmirror download.
    """
    if is_prebuilt_image(image):
        os.environ["ROCK_RTENV_NODE_V22180_INSTALL_CMD"] = NODE_PREBUILT_INSTALL_CMD
    else:
        ensure_fast_rock_runtime_mirrors()


def ensure_fast_rock_runtime_mirrors() -> None:
    """Prefer faster public mirrors for ROCK runtime bootstrap downloads."""
    os.environ.setdefault(
        "ROCK_RTENV_NODE_V22180_INSTALL_CMD",
        NODE_RUNTIME_MIRROR_INSTALL_CMD,
    )
