#!/usr/bin/env python3
"""
Aggregate token-position entropy dynamics for post-tool trajectories.

For each tool-capable (benchmark, model, harness) setting this script aggregates
only post-tool-call assistant tokens. Each assistant segment immediately after a
tool call/result is re-indexed from token position 1 before aggregation.

Outputs:
    analyze_tools/figures/post_tool_entropy/<benchmark>_<model>_<harness>.pdf
    analyze_tools/figures/post_tool_entropy/<benchmark>_<model>_<harness>.png
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "analyze_tools" / "figures" / "post_tool_entropy"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HARNESSES = ("DirectLLM", "OpenClaw", "OpenCode", "ZeroClaw")
TOOL_HARNESSES = {"OpenClaw", "OpenCode"}
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
MIN_BIN_COUNT = 3
MAX_Y_LIM = 1.2
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
    Setting("HLE", "Qwen", "DirectLLM", Path("/path/to/xxx/alphadiana_results/phase9_directllm_qwen35_27b_hle_logprobs")),
    Setting("HLE", "Qwen", "OpenClaw", ROOT / "results/quick_260430_hle_openclaw_qwen35_27b_merged"),
    Setting("HLE", "Qwen", "OpenCode", Path("/path/to/xxx/alphadiana-results/20260426-hle-opencode-qwen35_27b-v01")),
    Setting("HLE", "Qwen", "ZeroClaw", Path("/path/to/xxx/alphadiana-results/20260426-hle-zeroclaw-qwen35_27b-v01")),
    Setting("HLE", "Gemma", "DirectLLM", ROOT / "results/422_full/results/full_hle_directllm_gemma4_31b_logprobs"),
    Setting("HLE", "Gemma", "OpenClaw", ROOT / "results/422_full/results/full_hle_openclaw_gemma4_31b_logprobs"),
    Setting("HLE", "Gemma", "OpenCode", ROOT / "results/422_full/results/full_hle_opencode_gemma4_31b_logprobs"),
    Setting("HLE", "Gemma", "ZeroClaw", ROOT / "results/422_full/results/full_hle_zeroclaw_gemma4_31b_logprobs"),
    # GPQA
    Setting(
        "GPQA",
        "Qwen",
        "DirectLLM",
        ROOT / "results/hf-alphadiana-benchmark-results/full_run/20260423-gpqa-diamond-directllm-qwen35-27b-v1/results/20260423-gpqa_diamond-directllm-qwen35_27b-v01",
    ),
    Setting("GPQA", "Qwen", "OpenClaw", ROOT / "results/full_gpqa_v2_openclaw_qwen35_27b_logprobs"),
    Setting("GPQA", "Qwen", "OpenCode", ROOT / "results/full_gpqa_v2_opencode_qwen35_27b_logprobs"),
    Setting("GPQA", "Qwen", "ZeroClaw", ROOT / "results/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs"),
    Setting("GPQA", "Gemma", "DirectLLM", ROOT / "results/422_full/results/full_gpqa_directllm_gemma4_31b_logprobs"),
    Setting("GPQA", "Gemma", "OpenClaw", ROOT / "results/422_full/results/full_gpqa_openclaw_gemma4_31b_logprobs"),
    Setting("GPQA", "Gemma", "OpenCode", ROOT / "results/422_full/results/full_gpqa_opencode_gemma4_31b_logprobs"),
    Setting("GPQA", "Gemma", "ZeroClaw", ROOT / "results/422_full/results/full_gpqa_zeroclaw_gemma4_31b_logprobs"),
    # AIME pass@4
    Setting("AIMEPass4", "Qwen", "DirectLLM", Path("/path/to/xxx/alphadiana_results/full_20260423_qwen35_27b_aime2026_directllm_r1_pass4")),
    Setting("AIMEPass4", "Qwen", "OpenClaw", Path("/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_openclaw_qwen35_27b_pass4_t9300_from_20260428")),
    Setting("AIMEPass4", "Qwen", "OpenCode", Path("/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_opencode_qwen35_27b_pass4_t9300_from_20260425")),
    Setting("AIMEPass4", "Qwen", "ZeroClaw", Path("/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_zeroclaw_qwen35_27b_pass4_t9300_from_20260428")),
    Setting("AIMEPass4", "Gemma", "DirectLLM", Path("/path/to/xxx/results/full_aime2026_directllm_gemma4_31b_k4_logprobs")),
    Setting("AIMEPass4", "Gemma", "OpenClaw", Path("/path/to/xxx/results/quick_260503_aime2026_openclaw_gemma4_31b_8012_pass4_c1")),
    Setting("AIMEPass4", "Gemma", "OpenCode", Path("/path/to/xxx/results/full_20260503_aime2026_opencode_gemma4_31b_8012_pass4_c4")),
    Setting("AIMEPass4", "Gemma", "ZeroClaw", Path("/path/to/xxx/results/full_20260503_aime2026_zeroclaw_gemma4_31b_8011_pass4_c4")),
]


def main() -> None:
    global TOOL_CALL_LOOKUP
    TOOL_CALL_LOOKUP = load_tool_call_lookup()
    summaries = []
    for setting in SETTINGS:
        if setting.harness not in TOOL_HARNESSES:
            continue
        if not setting.run_dir.exists():
            print(f"SKIP missing {setting.benchmark}/{setting.model}/{setting.harness}: {setting.run_dir}", flush=True)
            continue
        print(f"Processing {setting.benchmark}/{setting.model}/{setting.harness}", flush=True)
        summary = plot_setting(setting)
        summaries.append(summary)
        print(
            "  files={files} post_tool_segments={segments} post_tool_tokens={tokens} -> {out}".format(
                files=summary["files"],
                segments=summary["post_tool_segments"],
                tokens=summary["tokens"],
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
    file_count = 0
    correct_files = 0
    wrong_files = 0
    skipped_files = 0
    post_tool_segments = 0
    token_count = 0

    for lp_path in files:
        task_id, sample_index = task_and_sample_from_logprob_path(lp_path)
        tool_key = (setting.model, setting.benchmark, setting.harness, task_id, sample_index)
        if TOOL_CALL_LOOKUP.get(tool_key) is False:
            continue
        correct = load_correct_label(setting.run_dir, task_id, sample_index)
        if correct is None:
            skipped_files += 1
            continue
        tokens = load_token_records(lp_path)
        if not tokens:
            continue
        spans = post_tool_spans(setting, lp_path, tokens)
        if not spans:
            continue
        file_count += 1
        if correct:
            correct_files += 1
        else:
            wrong_files += 1
        post_tool_segments += len(spans)
        for start, end in spans:
            token_count += max(0, end - start)
            for rel_idx, item in enumerate(tokens[start:end], start=1):
                entropy = token_entropy(item)
                if entropy is None or not math.isfinite(entropy):
                    continue
                bin_idx = int(np.searchsorted(edges, rel_idx, side="right") - 1)
                if not (0 <= bin_idx < n_bins):
                    continue
                if correct:
                    correct_sum[bin_idx] += entropy
                    correct_count[bin_idx] += 1
                else:
                    wrong_sum[bin_idx] += entropy
                    wrong_count[bin_idx] += 1

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
        correct_files=correct_files,
        wrong_files=wrong_files,
        pdf=pdf,
        png=png,
        no_data=post_tool_segments == 0,
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
        "post_tool_segments": post_tool_segments,
        "tokens": token_count,
        "pdf": str(pdf.relative_to(ROOT)),
        "png": str(png.relative_to(ROOT)),
    }


def render_entropy_plot(
    *,
    setting: Setting,
    edges: np.ndarray,
    correct_sum: np.ndarray,
    correct_count: np.ndarray,
    wrong_sum: np.ndarray,
    wrong_count: np.ndarray,
    correct_files: int,
    wrong_files: int,
    pdf: Path,
    png: Path,
    no_data: bool,
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
    x = np.sqrt(edges[:-1] * edges[1:])

    fig, ax = plt.subplots(figsize=(8.2, 4.8))
    widths = np.diff(edges)
    if np.any(correct_valid):
        ax.bar(
            edges[:-1][correct_valid],
            correct_mean[correct_valid],
            width=widths[correct_valid] * 0.48,
            align="edge",
            color=CORRECT_COLOR,
            alpha=0.82,
            edgecolor="white",
            linewidth=0.35,
        )
    if np.any(wrong_valid):
        ax.bar(
            edges[:-1][wrong_valid] + widths[wrong_valid] * 0.52,
            wrong_mean[wrong_valid],
            width=widths[wrong_valid] * 0.48,
            align="edge",
            color=WRONG_COLOR,
            alpha=0.82,
            edgecolor="white",
            linewidth=0.35,
        )
    if no_data:
        ax.text(
            0.5,
            0.52,
            "No post-tool-call tokens",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=18,
            color="#555555",
        )

    ax.set_xscale("log")
    ax.set_xlim(1, max(10, min(edges[-1], max_position_with_data(total_count, edges))))
    y_values = np.concatenate([correct_mean[correct_valid], wrong_mean[wrong_valid]])
    if y_values.size:
        y_top = min(MAX_Y_LIM, max(0.08, float(np.nanmax(y_values)) * 1.18))
    else:
        y_top = MAX_Y_LIM
    ax.set_ylim(0, y_top)
    ax.set_xlabel("Post-tool token position (log scale)", fontsize=18)
    ax.set_ylabel("Mean token entropy (nats)", fontsize=18)
    ax.tick_params(axis="both", labelsize=15)
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    ax.grid(True, axis="x", alpha=0.16, linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

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


def post_tool_spans(setting: Setting, lp_path: Path, tokens: list[dict]) -> list[tuple[int, int]]:
    if setting.harness not in {"OpenClaw", "OpenCode"}:
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
    turn_spans = align_turn_spans(tokens, turns)
    spans = []
    previous_turn_used_tool = False
    for turn in turn_spans:
        has_token_span = turn["start_tok"] is not None and turn["end_tok"] is not None
        if previous_turn_used_tool and has_token_span:
            start = int(turn["start_tok"])
            end = int(turn["end_tok"])
            if end > start:
                spans.append((start, end))
        previous_turn_used_tool = bool(turn["has_tool"])
    return spans


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


def align_turn_spans(tokens: list[dict], turns: list[dict[str, object]]) -> list[dict[str, object]]:
    token_text = "".join(str(t.get("token") or "") for t in tokens)
    spans: list[dict[str, object]] = []
    cursor = 0
    token_offsets = []
    char_pos = 0
    for token in tokens:
        token_offsets.append(char_pos)
        char_pos += len(str(token.get("token") or ""))

    for turn in turns:
        text = str(turn.get("text") or "")
        if not text.strip():
            spans.append(
                {
                    "start_tok": None,
                    "end_tok": None,
                    "has_tool": bool(turn.get("has_tool")),
                }
            )
            continue
        pos = -1
        needle = ""
        for probe in compact_probes(text):
            if not probe:
                continue
            needle = probe
            pos = token_text.find(probe, cursor)
            if pos < 0:
                pos = token_text.find(probe)
            if pos >= 0:
                break
        if pos < 0:
            continue
        end_char = min(len(token_text), pos + len(text))
        start_tok = char_to_token_index(token_offsets, pos)
        end_tok = char_to_token_index(token_offsets, end_char)
        end_tok = max(start_tok + 1, min(end_tok, len(tokens)))
        cursor = max(cursor, end_char)
        spans.append(
            {
                "start_tok": start_tok,
                "end_tok": end_tok,
                "has_tool": bool(turn.get("has_tool")),
            }
        )
    return spans


def compact_probes(text: str) -> list[str]:
    stripped = text.strip()
    collapsed = re.sub(r"\s+", " ", stripped)
    return [
        stripped[:96],
        stripped[:64],
        stripped[:40],
        collapsed[:96],
        collapsed[:64],
        collapsed[:40],
    ]


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
