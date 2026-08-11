#!/usr/bin/env python3
"""
Aggregate token-position entropy dynamics for post-tool trajectories.

For each (benchmark, model, harness) setting this script aggregates per-token
entropy from logprob sidecars into log-spaced token-position bins. Tool-call
regions are highlighted in gray when trace alignment can identify assistant
turns that ended in tool calls, or when explicit structured tool tokens appear
in the logprob stream.

Outputs:
    analyze_tools/figures/post_tool_entropy/<benchmark>_<model>_<harness>.pdf
    analyze_tools/figures/post_tool_entropy/<benchmark>_<model>_<harness>.png
    analyze_tools/figures/post_tool_entropy/<benchmark>_<model>_<harness>_toolcall_only.pdf
    analyze_tools/figures/post_tool_entropy/<benchmark>_<model>_<harness>_toolcall_only.png
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS_DIR = Path(os.environ.get("ALPHADIANA_RESULTS_DIR", REPO_ROOT / "results")).expanduser()
OUT_DIR = ROOT / "analyze_tools" / "figures" / "post_tool_entropy"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HARNESSES = ("DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw")
BENCHMARKS = ("HLE", "GPQA", "AIMEPass4")
MODELS = ("Qwen", "Gemma")

HARNESS_COLORS = {
    "DirectLLM": "#CC3311",
    "OpenClaw": "#103778",
    "OpenCode": "#8e375f",
    "ZeroClaw": "#33BBEE",
}

STRUCTURED_COLOR = "#8a8a8a"
CORRECT_COLOR = "#2ca02c"
WRONG_COLOR = "#d62728"
TOOL_TAIL_TOKENS = 32
MIN_BIN_COUNT = 3
MAX_TRACE_ALIGNED_FILES = 220
COMMON_Y_LIM = (0.0, 1.2)
TOOL_METRIC_FILES = {
    "Qwen": ROOT / "analyze_tools" / "data" / "six_action_statistics" / "trajectory_metrics.csv",
    "Gemma": ROOT / "analyze_tools" / "data" / "six_action_statistics_gemma" / "trajectory_metrics.csv",
}
TOOL_CALL_LOOKUP = {}


@dataclass(frozen=True)
class Setting:
    benchmark: str
    model: str
    harness: str
    run_dir: Path


SETTINGS = [
    # HLE
    Setting("HLE", "Qwen", "DirectLLM", RESULTS_DIR / "phase9_directllm_qwen35_27b_hle_logprobs"),
    Setting("HLE", "Qwen", "OpenClaw", RESULTS_DIR / "quick_260430_hle_openclaw_qwen35_27b_merged"),
    Setting("HLE", "Qwen", "OpenCode", RESULTS_DIR / "20260426-hle-opencode-qwen35_27b-v01"),
    Setting("HLE", "Qwen", "ZeroClaw", RESULTS_DIR / "20260426-hle-zeroclaw-qwen35_27b-v01"),
    Setting("HLE", "Gemma", "DirectLLM", RESULTS_DIR / "full_hle_directllm_gemma4_31b_logprobs"),
    Setting("HLE", "Gemma", "OpenClaw", RESULTS_DIR / "full_hle_openclaw_gemma4_31b_logprobs"),
    Setting("HLE", "Gemma", "OpenCode", RESULTS_DIR / "full_hle_opencode_gemma4_31b_logprobs"),
    Setting("HLE", "Gemma", "ZeroClaw", RESULTS_DIR / "full_hle_zeroclaw_gemma4_31b_logprobs"),
    # GPQA
    Setting(
        "GPQA",
        "Qwen",
        "DirectLLM",
        RESULTS_DIR / "hf-alphadiana-benchmark-results/full_run/20260423-gpqa-diamond-directllm-qwen35-27b-v1/results/20260423-gpqa_diamond-directllm-qwen35_27b-v01",
    ),
    Setting("GPQA", "Qwen", "OpenClaw", RESULTS_DIR / "full_gpqa_v2_openclaw_qwen35_27b_logprobs"),
    Setting("GPQA", "Qwen", "OpenCode", RESULTS_DIR / "full_gpqa_v2_opencode_qwen35_27b_logprobs"),
    Setting("GPQA", "Qwen", "ZeroClaw", RESULTS_DIR / "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs"),
    Setting("GPQA", "Gemma", "DirectLLM", RESULTS_DIR / "full_gpqa_directllm_gemma4_31b_logprobs"),
    Setting("GPQA", "Gemma", "OpenClaw", RESULTS_DIR / "full_gpqa_openclaw_gemma4_31b_logprobs"),
    Setting("GPQA", "Gemma", "OpenCode", RESULTS_DIR / "full_gpqa_opencode_gemma4_31b_logprobs"),
    Setting("GPQA", "Gemma", "ZeroClaw", RESULTS_DIR / "full_gpqa_zeroclaw_gemma4_31b_logprobs"),
    # AIME pass@4
    Setting("AIMEPass4", "Qwen", "DirectLLM", RESULTS_DIR / "full_20260423_qwen35_27b_aime2026_directllm_r1_pass4"),
    Setting("AIMEPass4", "Qwen", "OpenClaw", RESULTS_DIR / "repair_20260502_aime2026_openclaw_qwen35_27b_pass4_t9300_from_20260428"),
    Setting("AIMEPass4", "Qwen", "OpenCode", RESULTS_DIR / "repair_20260502_aime2026_opencode_qwen35_27b_pass4_t9300_from_20260425"),
    Setting("AIMEPass4", "Qwen", "ZeroClaw", RESULTS_DIR / "repair_20260502_aime2026_zeroclaw_qwen35_27b_pass4_t9300_from_20260428"),
    Setting("AIMEPass4", "Gemma", "DirectLLM", RESULTS_DIR / "full_aime2026_directllm_gemma4_31b_k4_logprobs"),
    Setting("AIMEPass4", "Gemma", "OpenClaw", RESULTS_DIR / "quick_260503_aime2026_openclaw_gemma4_31b_8012_pass4_c1"),
    Setting("AIMEPass4", "Gemma", "OpenCode", RESULTS_DIR / "full_20260503_aime2026_opencode_gemma4_31b_8012_pass4_c4"),
    Setting("AIMEPass4", "Gemma", "ZeroClaw", RESULTS_DIR / "full_20260503_aime2026_zeroclaw_gemma4_31b_8011_pass4_c4"),
]


def main() -> None:
    global TOOL_CALL_LOOKUP
    TOOL_CALL_LOOKUP = load_tool_call_lookup()
    summaries = []
    for setting in SETTINGS:
        if not setting.run_dir.exists():
            print(f"SKIP missing {setting.benchmark}/{setting.model}/{setting.harness}: {setting.run_dir}", flush=True)
            continue
        print(f"Processing {setting.benchmark}/{setting.model}/{setting.harness}", flush=True)
        summary = plot_setting(setting)
        summaries.append(summary)
        print(
            "  files={files} tokens={tokens} structured={structured} tool_files={tool_files} -> {out}".format(
                files=summary["files"],
                tokens=summary["tokens"],
                structured=summary["structured_tokens"],
                tool_files=summary["toolcall_files"],
                out=summary["pdf"],
            ),
            flush=True,
        )

    write_summary(summaries)
    print(f"\nDone. Wrote {len(summaries)} settings under {OUT_DIR}", flush=True)


def plot_setting(setting: Setting) -> dict[str, object]:
    files = collect_logprob_files(setting.run_dir)
    edges = token_position_edges()
    n_bins = len(edges) - 1
    correct_sum = np.zeros(n_bins, dtype=float)
    correct_count = np.zeros(n_bins, dtype=int)
    wrong_sum = np.zeros(n_bins, dtype=float)
    wrong_count = np.zeros(n_bins, dtype=int)
    struct_count = np.zeros(n_bins, dtype=int)
    tool_correct_sum = np.zeros(n_bins, dtype=float)
    tool_correct_count = np.zeros(n_bins, dtype=int)
    tool_wrong_sum = np.zeros(n_bins, dtype=float)
    tool_wrong_count = np.zeros(n_bins, dtype=int)
    tool_struct_count = np.zeros(n_bins, dtype=int)
    file_count = 0
    correct_files = 0
    wrong_files = 0
    skipped_files = 0
    toolcall_files = 0
    toolcall_correct_files = 0
    toolcall_wrong_files = 0
    token_count = 0
    toolcall_token_count = 0
    structured_token_count = 0
    toolcall_structured_token_count = 0

    for lp_path in files:
        tokens = load_token_records(lp_path)
        if not tokens:
            continue
        task_id, sample_index = task_and_sample_from_logprob_path(lp_path)
        correct = load_correct_label(setting.run_dir, task_id, sample_index)
        if correct is None:
            skipped_files += 1
            continue
        spans = structured_spans(setting, lp_path, tokens, allow_trace_alignment=len(files) <= MAX_TRACE_ALIGNED_FILES)
        has_tool_call = trajectory_has_tool_call(setting, task_id, sample_index, spans)
        file_count += 1
        if correct:
            correct_files += 1
        else:
            wrong_files += 1
        if has_tool_call:
            toolcall_files += 1
            toolcall_token_count += len(tokens)
            if correct:
                toolcall_correct_files += 1
            else:
                toolcall_wrong_files += 1
        token_count += len(tokens)
        for idx, item in enumerate(tokens, start=1):
            entropy = token_entropy(item)
            if entropy is None or not math.isfinite(entropy):
                continue
            bin_idx = int(np.searchsorted(edges, idx, side="right") - 1)
            if not (0 <= bin_idx < n_bins):
                continue
            if correct:
                correct_sum[bin_idx] += entropy
                correct_count[bin_idx] += 1
                if has_tool_call:
                    tool_correct_sum[bin_idx] += entropy
                    tool_correct_count[bin_idx] += 1
            else:
                wrong_sum[bin_idx] += entropy
                wrong_count[bin_idx] += 1
                if has_tool_call:
                    tool_wrong_sum[bin_idx] += entropy
                    tool_wrong_count[bin_idx] += 1
            is_structured = token_is_structured(setting.harness, idx - 1, item, spans)
            if is_structured:
                struct_count[bin_idx] += 1
                structured_token_count += 1
                if has_tool_call:
                    tool_struct_count[bin_idx] += 1
                    toolcall_structured_token_count += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{file_benchmark_name(setting.benchmark)}_{setting.model}_{setting.harness}"
    pdf = OUT_DIR / f"{stem}.pdf"
    png = OUT_DIR / f"{stem}.png"
    render_entropy_plot(
        setting=setting,
        edges=edges,
        correct_sum=correct_sum,
        correct_count=correct_count,
        wrong_sum=wrong_sum,
        wrong_count=wrong_count,
        struct_count=struct_count,
        correct_files=correct_files,
        wrong_files=wrong_files,
        pdf=pdf,
        png=png,
        title_suffix="",
    )

    tool_pdf = None
    tool_png = None
    if toolcall_files:
        tool_pdf = OUT_DIR / f"{stem}_toolcall_only.pdf"
        tool_png = OUT_DIR / f"{stem}_toolcall_only.png"
        render_entropy_plot(
            setting=setting,
            edges=edges,
            correct_sum=tool_correct_sum,
            correct_count=tool_correct_count,
            wrong_sum=tool_wrong_sum,
            wrong_count=tool_wrong_count,
            struct_count=tool_struct_count,
            correct_files=toolcall_correct_files,
            wrong_files=toolcall_wrong_files,
            pdf=tool_pdf,
            png=tool_png,
            title_suffix=" / tool-call trajectories only",
        )

    return {
        "benchmark": setting.benchmark,
        "model": setting.model,
        "harness": setting.harness,
        "run_dir": str(setting.run_dir),
        "files": file_count,
        "correct_files": correct_files,
        "wrong_files": wrong_files,
        "skipped_files": skipped_files,
        "toolcall_files": toolcall_files,
        "toolcall_correct_files": toolcall_correct_files,
        "toolcall_wrong_files": toolcall_wrong_files,
        "tokens": token_count,
        "toolcall_tokens": toolcall_token_count,
        "structured_tokens": structured_token_count,
        "toolcall_structured_tokens": toolcall_structured_token_count,
        "pdf": str(pdf.relative_to(ROOT)),
        "png": str(png.relative_to(ROOT)),
        "toolcall_pdf": str(tool_pdf.relative_to(ROOT)) if tool_pdf else "",
        "toolcall_png": str(tool_png.relative_to(ROOT)) if tool_png else "",
    }


def render_entropy_plot(
    *,
    setting: Setting,
    edges: np.ndarray,
    correct_sum: np.ndarray,
    correct_count: np.ndarray,
    wrong_sum: np.ndarray,
    wrong_count: np.ndarray,
    struct_count: np.ndarray,
    correct_files: int,
    wrong_files: int,
    pdf: Path,
    png: Path,
    title_suffix: str,
) -> None:
    correct_mean = np.divide(
        correct_sum,
        correct_count,
        out=np.full(len(correct_sum), np.nan),
        where=correct_count > 0,
    )
    wrong_mean = np.divide(
        wrong_sum,
        wrong_count,
        out=np.full(len(wrong_sum), np.nan),
        where=wrong_count > 0,
    )
    total_count = correct_count + wrong_count
    correct_valid = correct_count >= MIN_BIN_COUNT
    wrong_valid = wrong_count >= MIN_BIN_COUNT
    valid = correct_valid | wrong_valid
    x = np.sqrt(edges[:-1] * edges[1:])

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    struct_valid = (struct_count > 0) & valid & (total_count > 0)
    if np.any(struct_valid):
        shade_bins = struct_valid & (struct_count / np.maximum(total_count, 1) >= 0.03)
        for left, right in zip(edges[:-1][shade_bins], edges[1:][shade_bins]):
            ax.axvspan(left, right, color=STRUCTURED_COLOR, alpha=0.18, linewidth=0)
        ax.plot(
            [],
            [],
            color=STRUCTURED_COLOR,
            linewidth=6,
            alpha=0.35,
            label="Tool-call / structured bins",
        )

    if np.any(correct_valid):
        ax.plot(
            x[correct_valid],
            correct_mean[correct_valid],
            color=CORRECT_COLOR,
            linewidth=2.1,
            marker="o",
            markersize=3.0,
            label=f"Correct trajectories (n={correct_files})",
        )
    if np.any(wrong_valid):
        ax.plot(
            x[wrong_valid],
            wrong_mean[wrong_valid],
            color=WRONG_COLOR,
            linewidth=2.1,
            marker="o",
            markersize=3.0,
            label=f"Wrong trajectories (n={wrong_files})",
        )

    ax.set_xscale("log")
    ax.set_xlim(1, max(10, min(edges[-1], max_position_with_data(total_count, edges))))
    ax.set_ylim(*COMMON_Y_LIM)
    ax.set_xlabel("Output token position within trajectory (log scale)")
    ax.set_ylabel("Mean token entropy (nats)")
    ax.set_title(f"{setting.benchmark} / {setting.model} / {setting.harness}{title_suffix}")
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    ax.grid(True, axis="x", alpha=0.16, linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper right", framealpha=0.9, fontsize=8)

    fig.tight_layout()
    fig.savefig(pdf, dpi=220, bbox_inches="tight")
    fig.savefig(png, dpi=220, bbox_inches="tight")
    plt.close(fig)


def collect_logprob_files(run_dir: Path) -> list[Path]:
    preferred = run_dir / "logprobs_int16"
    raw = run_dir / "logprobs"
    files: list[Path] = []
    if preferred.exists():
        files = sorted(preferred.rglob("*.jsonl"))
        if files and int16_entropy_looks_empty(files[: min(8, len(files))]) and raw.exists():
            files = sorted(raw.rglob("*.jsonl"))
    if not files and raw.exists():
        files = sorted(raw.rglob("*.jsonl"))
    return files


def int16_entropy_looks_empty(files: Iterable[Path]) -> bool:
    checked = 0
    nonzero = 0
    for path in files:
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    checked += 1
                    entropy = json.loads(line).get("entropy_nats")
                    if isinstance(entropy, (int, float)) and float(entropy) > 0:
                        nonzero += 1
                    if checked >= 80:
                        return nonzero == 0
        except (OSError, json.JSONDecodeError):
            continue
    return checked > 0 and nonzero == 0


def load_token_records(path: Path) -> list[dict]:
    records = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    records.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    return records


def load_correct_label(run_dir: Path, task_id: str, sample_index: int) -> bool | None:
    path = run_dir / "tasks" / f"{task_id}.json"
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if isinstance(data, list):
        if not data:
            return None
        if 0 <= sample_index < len(data):
            record = data[sample_index]
        else:
            record = data[-1]
    elif isinstance(data, dict):
        record = data
    else:
        return None

    correct = record.get("correct")
    if isinstance(correct, bool):
        return correct
    if correct in (0, 1):
        return bool(correct)

    score = record.get("score")
    if isinstance(score, (int, float)):
        return float(score) > 0
    return None


def load_tool_call_lookup() -> dict[tuple[str, str, str, str, int], bool]:
    lookup: dict[tuple[str, str, str, str, int], bool] = {}
    for model, path in TOOL_METRIC_FILES.items():
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    sample_index = int(float(row.get("sample_index") or 0))
                except ValueError:
                    sample_index = 0
                try:
                    tool_count = int(float(row.get("tool_call_count") or 0))
                except ValueError:
                    tool_count = 0
                key = (
                    model,
                    row.get("benchmark") or "",
                    row.get("harness") or "",
                    row.get("task_id") or "",
                    sample_index,
                )
                lookup[key] = tool_count > 0
    return lookup


def trajectory_has_tool_call(
    setting: Setting,
    task_id: str,
    sample_index: int,
    spans: list[tuple[int, int]],
) -> bool:
    if setting.harness not in {"OpenClaw", "OpenCode"}:
        return False
    key = (setting.model, setting.benchmark, setting.harness, task_id, sample_index)
    if key in TOOL_CALL_LOOKUP:
        return TOOL_CALL_LOOKUP[key]
    return bool(spans)


def token_entropy(item: dict) -> float | None:
    entropy = item.get("entropy_nats")
    if isinstance(entropy, (int, float)):
        value = float(entropy)
        if value > 0:
            return value

    top20 = item.get("top20")
    if isinstance(top20, list) and top20:
        probs = [max(0.0, float(x.get("prob_i16", 0)) / 32767.0) for x in top20]
        return shannon_entropy(probs)

    top_logprobs = item.get("top_logprobs")
    if isinstance(top_logprobs, list) and top_logprobs:
        probs = [math.exp(float(x["logprob"])) for x in top_logprobs if isinstance(x.get("logprob"), (int, float))]
        return shannon_entropy(probs)

    logprob = item.get("logprob")
    if isinstance(logprob, (int, float)):
        p = math.exp(float(logprob))
        return -p * math.log(max(p, 1e-12))
    return None


def shannon_entropy(probs: list[float]) -> float:
    total = sum(probs)
    if total <= 0:
        return 0.0
    norm = [p / total for p in probs if p > 0]
    return float(-sum(p * math.log(p) for p in norm))


def structured_spans(
    setting: Setting,
    lp_path: Path,
    tokens: list[dict],
    *,
    allow_trace_alignment: bool,
) -> list[tuple[int, int]]:
    if setting.harness not in {"OpenClaw", "OpenCode"}:
        return []
    if not allow_trace_alignment:
        return []
    task_id, sample_index = task_and_sample_from_logprob_path(lp_path)
    artifact_dir = find_artifact_dir(setting.run_dir, task_id, sample_index)
    if artifact_dir is None:
        return []
    if setting.harness == "OpenCode":
        turns = opencode_turns(artifact_dir)
    else:
        turns = openclaw_turns(artifact_dir)
    if not turns:
        return []
    return align_tool_turn_tails(tokens, turns)


def task_and_sample_from_logprob_path(path: Path) -> tuple[str, int]:
    if path.parent.name.startswith("sample_"):
        sample_text = path.parent.name.split("_", 1)[1]
        sample_index = int(sample_text) if sample_text.isdigit() else 0
        return path.parent.parent.name, sample_index
    if path.stem.startswith("sample_"):
        sample_text = path.stem.split("_", 1)[1]
        sample_index = int(sample_text) if sample_text.isdigit() else 0
        return path.parent.name, sample_index
    return path.stem, 0


def find_artifact_dir(run_dir: Path, task_id: str, sample_index: int) -> Path | None:
    root = run_dir / "artifacts"
    candidates = [
        root / task_id / f"sample_{sample_index}",
        root / task_id / str(sample_index),
        root / f"{task_id}_sample_{sample_index}",
        root / task_id,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def opencode_turns(artifact_dir: Path) -> list[dict[str, object]]:
    path = artifact_dir / "workspace" / "opencode_output.jsonl"
    if not path.exists():
        return []
    turns = []
    cur = {"text": "", "has_tool": False}
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                event_type = row.get("type")
                part = row.get("part") or {}
                if event_type == "step_start":
                    cur = {"text": "", "has_tool": False}
                elif event_type == "text":
                    cur["text"] += str(part.get("text") or "")
                elif event_type == "tool_use":
                    cur["has_tool"] = True
                elif event_type == "step_finish":
                    turns.append(dict(cur))
                    cur = {"text": "", "has_tool": False}
    except (OSError, json.JSONDecodeError):
        return []
    return turns


def openclaw_turns(artifact_dir: Path) -> list[dict[str, object]]:
    path = artifact_dir / "workspace" / "openclaw_session.jsonl"
    if not path.exists():
        return []
    turns = []
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("type") != "message":
                    continue
                msg = row.get("message") or {}
                if msg.get("role") != "assistant":
                    continue
                text_parts = []
                has_tool = False
                for block in msg.get("content") or []:
                    block_type = block.get("type")
                    if block_type == "text":
                        text_parts.append(str(block.get("text") or ""))
                    elif block_type in {"toolCall", "tool_call", "tool_use"}:
                        has_tool = True
                turns.append({"text": "".join(text_parts), "has_tool": has_tool})
    except (OSError, json.JSONDecodeError):
        return []
    return turns


def align_tool_turn_tails(tokens: list[dict], turns: list[dict[str, object]]) -> list[tuple[int, int]]:
    token_text = "".join(str(t.get("token") or "") for t in tokens)
    spans = []
    cursor = 0
    token_offsets = []
    char_pos = 0
    for token in tokens:
        token_offsets.append(char_pos)
        char_pos += len(str(token.get("token") or ""))

    for turn in turns:
        text = str(turn.get("text") or "")
        if not text.strip():
            continue
        needle = compact_probe(text)
        if not needle:
            continue
        pos = token_text.find(needle, cursor)
        if pos < 0:
            pos = token_text.find(needle)
        if pos < 0:
            continue
        end_char = min(len(token_text), pos + len(text))
        start_tok = char_to_token_index(token_offsets, pos)
        end_tok = char_to_token_index(token_offsets, end_char)
        end_tok = max(start_tok + 1, min(end_tok, len(tokens)))
        cursor = max(cursor, end_char)
        if bool(turn.get("has_tool")):
            span_start = max(start_tok, end_tok - TOOL_TAIL_TOKENS)
            spans.append((span_start, end_tok))
    return spans


def compact_probe(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= 48:
        return text
    return text[:48]


def char_to_token_index(offsets: list[int], char_pos: int) -> int:
    return int(np.searchsorted(offsets, char_pos, side="right") - 1)


def token_is_structured(harness: str, token_index: int, item: dict, spans: list[tuple[int, int]]) -> bool:
    if harness not in {"OpenClaw", "OpenCode"}:
        return False
    token = str(item.get("token") or "")
    if any(start <= token_index < end for start, end in spans):
        return True
    lowered = token.lower()
    return any(
        marker in lowered
        for marker in (
            "<tool_call",
            "</tool_call",
            "<tool_response",
            "</tool_response",
            "toolcall",
            "function_call",
        )
    )


def token_position_edges() -> np.ndarray:
    small = np.arange(1, 11, dtype=int)
    large = np.unique(np.round(np.logspace(1, math.log10(131072), 70)).astype(int))
    edges = np.unique(np.concatenate([small, large]))
    if edges[0] != 1:
        edges = np.insert(edges, 0, 1)
    return edges


def file_benchmark_name(benchmark: str) -> str:
    return "AIME" if benchmark == "AIMEPass4" else benchmark


def max_position_with_data(counts: np.ndarray, edges: np.ndarray) -> float:
    idx = np.where(counts > 0)[0]
    if len(idx) == 0:
        return 10.0
    return float(edges[min(idx[-1] + 1, len(edges) - 1)])


def write_summary(rows: list[dict[str, object]]) -> None:
    path = OUT_DIR / "summary.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


if __name__ == "__main__":
    main()
