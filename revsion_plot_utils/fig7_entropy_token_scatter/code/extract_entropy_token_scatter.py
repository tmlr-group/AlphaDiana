#!/usr/bin/env python3
"""
extract_entropy_token_scatter.py — Extract trajectory-level entropy vs token length
for all model x harness x benchmark combinations.

Output:
    data/entropy_token_scatter.csv
    Columns: model,harness,benchmark,task_id,sample,correct,mean_entropy,n_tokens

Run:
    python3 analyze_tools/extract_entropy_token_scatter.py
    (from repo root)
"""

from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ─── Run directory definitions ───────────────────────────────────────────────

RUNS: dict[tuple[str, str, str], Path] = {}

def _reg(model: str, harness: str, benchmark: str, path: str) -> None:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / path
    if p.exists():
        RUNS[(model, harness, benchmark)] = p

# --- GPQA ---
_reg("Qwen3.5-27B",  "directllm", "GPQA", "/path/to/xxx/alphadiana_results/phase9_directllm_gpqa_diamond_qwen35_27b_logprobs")
_reg("Qwen3.5-27B",  "openclaw",  "GPQA", "results/full_gpqa_v2_openclaw_qwen35_27b_logprobs")
_reg("Qwen3.5-27B",  "opencode",  "GPQA", "results/full_gpqa_v2_opencode_qwen35_27b_logprobs")
_reg("Qwen3.5-27B",  "zeroclaw",  "GPQA", "results/full_gpqa_v2_zeroclaw_qwen35_27b_logprobs")
_reg("Gemma4-31B",   "directllm", "GPQA", "results/422_full/results/full_gpqa_directllm_gemma4_31b_logprobs")
_reg("Gemma4-31B",   "openclaw",  "GPQA", "results/422_full/results/full_gpqa_openclaw_gemma4_31b_logprobs")
_reg("Gemma4-31B",   "opencode",  "GPQA", "results/422_full/results/full_gpqa_opencode_gemma4_31b_logprobs")
_reg("Gemma4-31B",   "zeroclaw",  "GPQA", "results/422_full/results/full_gpqa_zeroclaw_gemma4_31b_logprobs")

# --- HLE ---
_reg("Qwen3.5-27B",  "directllm", "HLE", "/path/to/xxx/alphadiana_results/phase9_directllm_qwen35_27b_hle_logprobs")
_reg("Qwen3.5-27B",  "openclaw",  "HLE", "results/quick_260430_hle_openclaw_qwen35_27b_merged")
_reg("Qwen3.5-27B",  "opencode",  "HLE", "/path/to/xxx/alphadiana-results/20260426-hle-opencode-qwen35_27b-v01")
_reg("Qwen3.5-27B",  "zeroclaw",  "HLE", "/path/to/xxx/alphadiana-results/20260426-hle-zeroclaw-qwen35_27b-v01")
_reg("Gemma4-31B",   "directllm", "HLE", "results/422_full/results/full_hle_directllm_gemma4_31b_logprobs")
_reg("Gemma4-31B",   "openclaw",  "HLE", "results/422_full/results/full_hle_openclaw_gemma4_31b_logprobs")
_reg("Gemma4-31B",   "opencode",  "HLE", "results/422_full/results/full_hle_opencode_gemma4_31b_logprobs")
_reg("Gemma4-31B",   "zeroclaw",  "HLE", "results/422_full/results/full_hle_zeroclaw_gemma4_31b_logprobs")

# --- AIME ---
_reg("Qwen3.5-27B",  "directllm", "AIME", "/path/to/xxx/alphadiana_results/full_20260423_qwen35_27b_aime2026_directllm_r1_pass4")
_reg("Qwen3.5-27B",  "openclaw",  "AIME", "/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_openclaw_qwen35_27b_pass4_t9300_from_20260428")
_reg("Qwen3.5-27B",  "opencode",  "AIME", "/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_opencode_qwen35_27b_pass4_t9300_from_20260425")
_reg("Qwen3.5-27B",  "zeroclaw",  "AIME", "/path/to/xxx/alphadiana_offload/422_full/results/repair_20260502_aime2026_zeroclaw_qwen35_27b_pass4_t9300_from_20260428")
_reg("Gemma4-31B",   "directllm", "AIME", "/path/to/xxx/results/full_aime2026_directllm_gemma4_31b_k4_logprobs")
_reg("Gemma4-31B",   "openclaw",  "AIME", "/path/to/xxx/results/quick_260503_aime2026_openclaw_gemma4_31b_8012_pass4_c1")
_reg("Gemma4-31B",   "opencode",  "AIME", "/path/to/xxx/results/full_20260503_aime2026_opencode_gemma4_31b_8012_pass4_c4")
_reg("Gemma4-31B",   "zeroclaw",  "AIME", "/path/to/xxx/results/full_20260503_aime2026_zeroclaw_gemma4_31b_8011_pass4_c4")


def is_valid_record(record: dict) -> bool:
    correct = record.get("correct")
    if correct is None or not isinstance(correct, bool):
        score = record.get("score")
        if isinstance(score, (int, float)) and not math.isnan(score):
            correct = score > 0
        else:
            return False
    tes = record.get("token_entropy_stats")
    if not isinstance(tes, dict):
        return False
    n_tokens = tes.get("n_tokens", 0)
    if not n_tokens or n_tokens <= 0:
        return False
    mean_entropy = tes.get("mean")
    if mean_entropy is None:
        return False
    try:
        if math.isnan(float(mean_entropy)):
            return False
    except (TypeError, ValueError):
        return False
    return True


def extract_from_dir(run_dir: Path) -> list[dict]:
    tasks_dir = run_dir / "tasks"
    if not tasks_dir.exists():
        return []
    rows = []
    for path in sorted(tasks_dir.glob("*.json")):
        data = json.loads(path.read_text())
        task_id = path.stem
        if isinstance(data, list):
            if not data:
                continue
            for idx, record in enumerate(data):
                if not is_valid_record(record):
                    continue
                tes = record["token_entropy_stats"]
                rows.append({
                    "task_id": record.get("task_id", task_id),
                    "sample": idx,
                    "correct": 1 if record["correct"] else 0,
                    "mean_entropy": float(tes["mean"]),
                    "n_tokens": int(tes["n_tokens"]),
                })
        else:
            if not is_valid_record(data):
                continue
            tes = data["token_entropy_stats"]
            rows.append({
                "task_id": data.get("task_id", task_id),
                "sample": 0,
                "correct": 1 if data["correct"] else 0,
                "mean_entropy": float(tes["mean"]),
                "n_tokens": int(tes["n_tokens"]),
            })
    return rows


def main() -> None:
    all_rows: list[dict] = []
    fieldnames = ["model", "harness", "benchmark", "task_id", "sample",
                  "correct", "mean_entropy", "n_tokens"]

    for (model, harness, benchmark), run_dir in sorted(RUNS.items()):
        records = extract_from_dir(run_dir)
        for r in records:
            r["model"] = model
            r["harness"] = harness
            r["benchmark"] = benchmark
        all_rows.extend(records)
        print(f"  {model:14s} {harness:10s} {benchmark:5s} -> {len(records):4d} samples")

    out_path = DATA_DIR / "entropy_token_scatter.csv"
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nWrote {len(all_rows)} total rows to {out_path}")

    for benchmark in ["GPQA", "HLE", "AIME"]:
        bench_rows = [r for r in all_rows if r["benchmark"] == benchmark]
        if not bench_rows:
            print(f"  {benchmark}: NO DATA")
            continue
        models = sorted(set(r["model"] for r in bench_rows))
        harnesses = sorted(set(r["harness"] for r in bench_rows))
        print(f"  {benchmark}: {len(bench_rows)} samples, {len(models)} models, {len(harnesses)} harnesses")


if __name__ == "__main__":
    main()
