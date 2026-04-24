"""Phase 12 — ROCK checkout preflight script structural tests.

Validates that `scripts/phase12_rock_checkout_preflight.sh` contains the exact
command strings required by the plan's acceptance criteria, without actually
starting ROCK services (pure static analysis).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = REPO_ROOT / "scripts" / "phase12_rock_checkout_preflight.sh"


# ── fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def script_text() -> str:
    assert PREFLIGHT_SCRIPT.exists(), f"Preflight script not found: {PREFLIGHT_SCRIPT}"
    return PREFLIGHT_SCRIPT.read_text(encoding="utf-8")


# ── required command strings (plan acceptance criteria) ───────────────────────

REQUIRED_STRINGS = [
    "source scripts/activate.sh",
    "source scripts/rock_env.sh",
    "alphadiana env",
    "bash scripts/start_openclaw.sh",
    "bash scripts/start_zeroclaw.sh",
    "phase12 rock preflight failed",
]


@pytest.mark.parametrize("required_string", REQUIRED_STRINGS)
def test_preflight_contains_required_string(script_text: str, required_string: str) -> None:
    """Each required command string must appear verbatim in the script."""
    assert required_string in script_text, (
        f"Required string not found in {PREFLIGHT_SCRIPT.name!r}: {required_string!r}"
    )


# ── structural checks ─────────────────────────────────────────────────────────

def test_preflight_script_exists() -> None:
    assert PREFLIGHT_SCRIPT.exists(), f"Missing: {PREFLIGHT_SCRIPT}"


def test_preflight_script_is_executable() -> None:
    assert os.access(PREFLIGHT_SCRIPT, os.X_OK), (
        f"Script is not executable: {PREFLIGHT_SCRIPT}\n"
        f"Fix: chmod +x {PREFLIGHT_SCRIPT}"
    )


def test_preflight_script_has_shebang(script_text: str) -> None:
    first_line = script_text.splitlines()[0] if script_text else ""
    assert first_line.startswith("#!"), (
        f"Script must start with a shebang: {PREFLIGHT_SCRIPT}"
    )


def test_preflight_script_validates_openclaw_config(script_text: str) -> None:
    """Script must validate the OpenClaw Phase 11 smoke config."""
    assert "phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml" in script_text


def test_preflight_script_validates_zeroclaw_config(script_text: str) -> None:
    """Script must validate the ZeroClaw Phase 11 smoke config."""
    assert "phase11_zeroclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml" in script_text


def test_preflight_script_exits_nonzero_on_failure(script_text: str) -> None:
    """Script must contain exit 1 for the failure path."""
    assert "exit 1" in script_text, "Script must exit non-zero on preflight failure"


def test_preflight_remediation_runs_both_start_scripts(script_text: str) -> None:
    """Both start scripts must appear in the remediation block."""
    assert "start_openclaw.sh" in script_text
    assert "start_zeroclaw.sh" in script_text


def test_preflight_checks_all_three_services(script_text: str) -> None:
    """Preflight must check admin, proxy, and redis services."""
    assert "admin" in script_text
    assert "proxy" in script_text
    assert "redis" in script_text
