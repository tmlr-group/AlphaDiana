from pathlib import Path


def test_phase12_preflight_script_contains_required_commands() -> None:
    script_path = Path("scripts/phase12_rock_checkout_preflight.sh")
    assert script_path.exists(), "preflight script missing"

    content = script_path.read_text(encoding="utf-8")

    required_snippets = [
        "source \"${SCRIPT_DIR}/activate.sh\"",
        "source \"${SCRIPT_DIR}/rock_env.sh\"",
        "python -m alphadiana.cli env",
        "configs/full_runs/phase11_openclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml",
        "configs/full_runs/phase11_zeroclaw_gpqa_diamond_qwen35_27b_logprobs_smoke.yaml",
        "bash scripts/start_openclaw.sh",
        "bash scripts/start_zeroclaw.sh",
        "phase12 rock preflight failed",
    ]

    for snippet in required_snippets:
        assert snippet in content, f"missing required snippet: {snippet}"
