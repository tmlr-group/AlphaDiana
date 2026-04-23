"""CLI + renderer coverage for the TB2 and SWE-bench-Verified official backends.

Asserts both that `benchmark_rollout_cli commands` can emit runnable shell for
the official Terminal-Bench v2 backend (existing) and the new
``official_swebench_verified`` backend (added in this change).
"""
from __future__ import annotations

import io
import textwrap
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from alphadiana.benchmark_rollout_cli import main as rollout_cli_main
from alphadiana.utils import rollout_campaign as rc


_MANIFEST = """
campaign_id: "test_official_backends"
defaults:
  run_id_prefix: "t"
models:
  - id: "qwen35_27b"
    model_name: "Qwen/Qwen3.5-27B"
    api_base_env: "QWEN_VLLM_API_BASE"
    api_key_env: "QWEN_VLLM_API_KEY"
    official_model_name: "openai/qwen3.5-27b"
    supports_multimodal: false
path_templates:
  - id: "tb2_direct"
    benchmark: "terminal_bench2"
    harness: "direct_llm"
    backend: "official_terminal_bench_2"
    max_concurrent: 3
    base_wave: "wave_b_official"
    base_risk: "official_checkout"
    config_gaps: []
  - id: "swe_verified_mini_sweagent"
    benchmark: "swe_bench_verified"
    harness: "sweagent"
    backend: "official_swebench_verified"
    max_concurrent: 10
    base_wave: "wave_b_official"
    base_risk: "official_checkout"
    config_gaps: []
    overrides:
      dataset: "MariusHobbhahn/swe-bench-verified-mini"
      subset: "verified"
      split: "test"
      temperature: 0.0
      top_p: 0.95
      max_output_tokens: 131072
      per_instance_call_limit: 80
"""


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    p = tmp_path / "campaign.yaml"
    p.write_text(textwrap.dedent(_MANIFEST).lstrip(), encoding="utf-8")
    return p


def test_expand_covers_both_official_backends(manifest_path: Path):
    runs = rc.expand_campaign(manifest_path)
    backends = sorted(r.path.backend for r in runs)
    assert backends == ["official_swebench_verified", "official_terminal_bench_2"]


def test_tb2_render_uses_harbor_cli(manifest_path: Path):
    runs = rc.filter_runs(rc.expand_campaign(manifest_path), benchmark="terminal_bench2")
    assert len(runs) == 1
    cmd = rc.render_run_command(runs[0])
    assert "harbor run --dataset terminal-bench@2.0" in cmd
    assert "--agent terminus-2" in cmd
    assert "QWEN_VLLM_API_BASE" in cmd
    assert "temperature=0.0" in cmd


def test_swe_verified_render_uses_sweagent_run_batch(manifest_path: Path):
    runs = rc.filter_runs(rc.expand_campaign(manifest_path), benchmark="swe_bench_verified")
    assert len(runs) == 1
    cmd = rc.render_run_command(runs[0])
    # Core sweagent invocation with the Verified-Mini dataset
    assert "sweagent run-batch" in cmd
    assert "--config config/default.yaml" in cmd  # upstream sweagent 1.1.0 default
    assert "--instances.type swe_bench" in cmd
    assert "--instances.path_override MariusHobbhahn/swe-bench-verified-mini" in cmd
    assert "--instances.subset verified" in cmd
    assert "--instances.split test" in cmd
    assert "--agent.model.per_instance_call_limit 80" in cmd
    # Official SWE-bench eval harness (not swe_bench_pro_eval.py)
    assert "swebench.harness.run_evaluation" in cmd
    assert "swe_bench_pro_eval.py" not in cmd
    # Points at the vllm endpoint from the manifest model spec
    assert "QWEN_VLLM_API_BASE" in cmd


def test_swe_verified_render_uses_override_sampling(manifest_path: Path):
    """Sampling params must NOT be hardcoded in the renderer; they come from
    manifest overrides. The default manifest above pins 0.0 / 0.95 / 32768."""
    runs = rc.filter_runs(rc.expand_campaign(manifest_path), benchmark="swe_bench_verified")
    cmd = rc.render_run_command(runs[0])
    assert "--agent.model.temperature 0.0" in cmd
    assert "--agent.model.top_p 0.95" in cmd
    assert "--agent.model.max_output_tokens 131072" in cmd


def test_swe_verified_render_respects_overrides(tmp_path: Path):
    """Override-driven design: changing manifest values propagates into the command."""
    custom = (_MANIFEST
              .replace("per_instance_call_limit: 80", "per_instance_call_limit: 40")
              .replace('split: "test"', 'split: "dev"')
              .replace("temperature: 0.0", "temperature: 0.6")
              .replace("max_output_tokens: 131072", "max_output_tokens: 65536"))
    p = tmp_path / "custom.yaml"
    p.write_text(textwrap.dedent(custom).lstrip(), encoding="utf-8")
    runs = rc.filter_runs(rc.expand_campaign(p), benchmark="swe_bench_verified")
    cmd = rc.render_run_command(runs[0])
    assert "--agent.model.per_instance_call_limit 40" in cmd
    assert "--instances.split dev" in cmd
    assert "--agent.model.temperature 0.6" in cmd
    assert "--agent.model.max_output_tokens 65536" in cmd


def test_cli_commands_prints_both_backends(manifest_path: Path, capsys):
    rc_code = rollout_cli_main(
        ["commands", "--manifest", str(manifest_path), "--kind", "run"],
    )
    assert rc_code == 0
    out = capsys.readouterr().out
    assert "harbor run --dataset terminal-bench@2.0" in out
    assert "sweagent run-batch" in out


def test_cli_summary_lists_both_backends(manifest_path: Path, capsys):
    rc_code = rollout_cli_main(["summary", "--manifest", str(manifest_path)])
    assert rc_code == 0
    out = capsys.readouterr().out
    assert "official_terminal_bench_2" in out
    assert "official_swebench_verified" in out
