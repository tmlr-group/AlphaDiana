from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path

from scripts import podman_vlm_image_preflight as preflight_mod


RUNNER = Path("scripts/run_podman_mmmu_pro_readiness.sh")
PREFLIGHT = Path("scripts/podman_vlm_image_preflight.py")

EXCLUDED_NAMES = {
    "swe_bench",
    "swebench",
    "terminal_bench2",
    "moltbench",
    "aime",
    "gpqa",
    "imo_answerbench",
}


def test_runner_shell_syntax_is_valid() -> None:
    result = subprocess.run(["bash", "-n", str(RUNNER)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_preflight_helper_compiles() -> None:
    result = subprocess.run(["python", "-m", "py_compile", str(PREFLIGHT)], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_runner_has_required_modes_and_agent_matrix() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    for mode in ("validate", "preflight", "pilot", "audit", "all|auto", "full"):
        assert f"{mode})" in script
    assert "openclaw zeroclaw opencode" in script
    assert "_mmmu_pro_pilot.yaml" in script
    assert "${RUN_PREFIX}_${agent}_mmmu_pro" in script
    assert "docker.io/python:3.12-slim" in script
    assert "--network \"$PROVIDER_PREFLIGHT_NETWORK\"" in script


def test_runner_blocks_pilot_on_podman_image_preflight() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    assert "podman run --rm --network" in script
    assert "scripts/podman_vlm_image_preflight.py" in script
    assert "preflight_provider_from_podman || return" in script
    pilot_body = script.split("run_pilot() {", 1)[1].split("\n}", 1)[0]
    assert pilot_body.index("preflight_provider_from_podman || return") < pilot_body.index("run_pilot_cells")


def test_runner_main_preserves_mode_exit_status() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    main_body = script.split("main() {", 1)[1].split("\n}", 1)[0]
    assert "local rc=0" in main_body
    assert "preflight) preflight_provider_from_podman; rc=$? ;;" in main_body
    assert "return \"$rc\"" in main_body


def test_runner_all_auto_sequence_is_fail_fast() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    all_body = script.split("run_all() {", 1)[1].split("\n}", 1)[0]
    assert all_body.index("validate_matrix || return") < all_body.index("preflight_provider_from_podman || return")
    assert all_body.index("preflight_provider_from_podman || return") < all_body.index("run_pilot_cells || return")
    assert all_body.index("run_pilot_cells || return") < all_body.index("run_audit")
    main_body = script.split("main() {", 1)[1].split("\n}", 1)[0]
    assert "all|auto) run_all; rc=$? ;;" in main_body


def test_runner_writes_expected_logs_and_status_paths() -> None:
    script = RUNNER.read_text(encoding="utf-8")
    assert "context/podman-mmmu-pro-readiness" in script
    assert "logs/${run_id}.log" in script
    assert "results/${run_id}" in script
    assert "run-status-${RUN_PREFIX}.tsv" in script
    assert "preflight-${RUN_PREFIX}.json" in script


def test_runner_avoids_excluded_benchmark_names() -> None:
    script = RUNNER.read_text(encoding="utf-8").lower()
    for excluded in EXCLUDED_NAMES:
        assert excluded not in script


def test_preflight_helper_uses_image_chat_without_secret_output() -> None:
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert "/models" in source
    assert "/chat/completions" in source
    assert "image_url" in source
    assert "response_non_empty" in source
    assert "OPENAI_BASE_URL" in source
    assert "OPENAI_API_KEY" in source
    assert "OPENAI_MODEL_NAME" in source
    assert "remote_image_url" in source
    assert "data_url" in source
    assert "PODMAN_MMMU_PRO_VLM_IMAGE_URL" in source
    assert "chat_template_kwargs" in source
    assert "top_k" in source
    assert "extra_body" not in source
    assert "Authorization.*print" not in source
    assert "OPENAI_API_KEY.*print" not in source
    assert "data:image/png;base64.*print" not in source


def test_preflight_payload_defaults_to_thinking_mode_and_large_token_budget() -> None:
    payload = preflight_mod._chat_payload(
        model="Qwen/Qwen3.5-4B",
        image_url="https://example.test/image.png",
        max_tokens=preflight_mod.DEFAULT_MAX_TOKENS,
        top_k=20,
    )

    assert payload["max_tokens"] >= 8192
    assert payload["top_k"] == 20
    assert payload["chat_template_kwargs"] == {"enable_thinking": True}
    assert "extra_body" not in payload
    encoded = json.dumps(payload)
    assert "enable_thinking" in encoded
    assert ("enable" + "_thinking=" + "false") not in encoded


def test_preflight_http_error_body_is_captured_and_sanitized() -> None:
    secret = "sk-secret"
    err = urllib.error.HTTPError(
        url="http://127.0.0.1:8011/v1/chat/completions",
        code=400,
        msg="Bad Request",
        hdrs={},
        fp=io.BytesIO(b'{"error":"provider rejected sk-secret image input"}'),
    )

    status = preflight_mod._error_probe_status(
        preflight_mod.RequestError(
            "HTTPError",
            str(err),
            http_status=400,
            error_body=preflight_mod._read_error_body(err, secrets=(secret,)),
        )
    )

    assert status["http_status"] == 400
    assert status["error_type"] == "HTTPError"
    assert "provider rejected" in status["error_body"]
    assert secret not in status["error_body"]
