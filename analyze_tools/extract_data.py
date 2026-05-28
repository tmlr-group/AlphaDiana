#!/usr/bin/env python3
"""
extract_data.py — 从 results/ 目录提取分析所需数据，写入 data/ CSV。

运行方式：
    python3 analyze_tools/extract_data.py

输出文件（analyze_tools/data/）：
    entropy_calibration.csv     — entropy bin vs accuracy (OpenClaw / OpenCode)
    token_count_by_outcome.csv  — OpenClaw 每题 token 数 + 答对/错
    tool_boundary_profile.csv   — tool call 边界 ±15 token 的 entropy（OpenCode）
    baseline_vs_posttool.csv    — turn-level mean entropy (baseline vs post-tool)
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

RESULTS_DIR = Path(__file__).parent.parent / "results"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

OC_RUN = "full_gpqa_v2_openclaw_qwen35_27b_logprobs"
OCODE_RUN = "full_gpqa_v2_opencode_qwen35_27b_logprobs"


# ─── helpers ──────────────────────────────────────────────────────────────────

def load_valid_tasks(run_id: str) -> dict:
    tasks_dir = RESULTS_DIR / run_id / "tasks"
    results = {}
    for fname in os.listdir(tasks_dir):
        if not fname.endswith(".json"):
            continue
        with open(tasks_dir / fname) as f:
            d = json.load(f)
        if isinstance(d, list):
            d = d[-1]
        if d.get("score_status") == "valid_scored":
            results[d["task_id"]] = d
    return results


def load_int16_tokens(run_id: str, task_id: str) -> list | None:
    path = RESULTS_DIR / run_id / "logprobs_int16" / f"{task_id}.jsonl"
    if not path.exists():
        return None
    tokens = []
    with open(path) as f:
        for line in f:
            tokens.append(json.loads(line))
    return tokens


def get_ocode_turn_texts(task_id: str) -> list | None:
    path = RESULTS_DIR / OCODE_RUN / "artifacts" / task_id / "workspace" / "opencode_output.jsonl"
    if not path.exists():
        return None
    turns = []
    cur: dict = {"visible_text": "", "has_tool": False}
    with open(path) as f:
        for line in f:
            r = json.loads(line)
            t = r["type"]
            p = r.get("part", {})
            if t == "step_start":
                cur = {"visible_text": "", "has_tool": False}
            elif t == "text":
                visible = re.sub(r"<think>.*?</think>", "", p.get("text", ""), flags=re.DOTALL)
                cur["visible_text"] += visible
            elif t == "tool_use":
                cur["has_tool"] = True
            elif t == "step_finish":
                cur["reason"] = p.get("reason", "")
                turns.append(cur)
                cur = {"visible_text": "", "has_tool": False}
    return turns


def find_turn_boundaries(tokens: list, turns: list) -> list:
    token_text = "".join(t["token"] for t in tokens)
    boundaries = []
    prev_pos = 0
    for i, turn in enumerate(turns):
        if i == 0:
            s = 0
        else:
            search = turn["visible_text"].strip()[:40]
            if not search:
                continue
            pos = token_text.find(search, prev_pos)
            if pos < 0:
                continue
            prev_pos = pos
            char_pos = 0
            s = 0
            for j, tok in enumerate(tokens):
                if char_pos >= pos:
                    s = j
                    break
                char_pos += len(tok["token"])
        boundaries.append({"turn": i, "start_tok": s, "has_tool": turn["has_tool"], "reason": turn.get("reason", "")})
    for i in range(len(boundaries)):
        boundaries[i]["end_tok"] = boundaries[i + 1]["start_tok"] if i + 1 < len(boundaries) else len(tokens)
    return boundaries


# ─── 1. entropy_calibration.csv ───────────────────────────────────────────────

def extract_entropy_calibration() -> None:
    rows = []
    for run_id, model_name in [(OC_RUN, "OpenClaw"), (OCODE_RUN, "OpenCode")]:
        tasks = load_valid_tasks(run_id)
        bins: dict = defaultdict(lambda: [0, 0])  # bin -> [correct, total]
        for task in tasks.values():
            s = task.get("token_entropy_stats", {})
            if not s:
                continue
            me = s.get("mean", 0)
            b = round(min(int(me * 10) / 10.0, 1.5), 1)
            bins[b][0] += int(task["correct"])
            bins[b][1] += 1
        for b in sorted(bins):
            c, n = bins[b]
            rows.append({"model": model_name, "entropy_bin": b, "accuracy": c / n, "n": n})

    out = DATA_DIR / "entropy_calibration.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "entropy_bin", "accuracy", "n"])
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {out} ({len(rows)} rows)")


# ─── 2. token_count_by_outcome.csv ────────────────────────────────────────────

def extract_token_counts() -> None:
    tasks = load_valid_tasks(OC_RUN)
    rows = []
    for tid, task in tasks.items():
        s = task.get("token_entropy_stats", {})
        n_tok = s.get("n_tokens", 0)
        mean_ent = s.get("mean", 0)
        if n_tok == 0:
            continue
        rows.append({
            "task_id": tid,
            "correct": int(task["correct"]),
            "n_tokens": n_tok,
            "mean_entropy": mean_ent,
            "wall_time_min": task.get("wall_time_sec", 0) / 60,
        })

    out = DATA_DIR / "token_count_by_outcome.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["task_id", "correct", "n_tokens", "mean_entropy", "wall_time_min"])
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {out} ({len(rows)} rows)")


# ─── 3. tool_boundary_profile.csv ─────────────────────────────────────────────

def extract_tool_boundary_profile() -> None:
    tasks = load_valid_tasks(OCODE_RUN)
    WINDOW = 15
    dist: dict = defaultdict(list)   # offset -> [entropy]
    dist_c: dict = defaultdict(list)
    dist_w: dict = defaultdict(list)

    for tid, task in tasks.items():
        turns = get_ocode_turn_texts(tid)
        tokens = load_int16_tokens(OCODE_RUN, tid)
        if not turns or not tokens or len(turns) < 2:
            continue
        boundaries = find_turn_boundaries(tokens, turns)
        if len(boundaries) < 2:
            continue
        correct = task["correct"]
        for i, b in enumerate(boundaries[:-1]):
            if not b["has_tool"]:
                continue
            bnd = boundaries[i + 1]["start_tok"]
            for off in range(-WINDOW, WINDOW + 1):
                idx = bnd + off
                if 0 <= idx < len(tokens):
                    e = tokens[idx]["entropy_nats"]
                    dist[off].append(e)
                    (dist_c if correct else dist_w)[off].append(e)

    rows = []
    for off in range(-WINDOW, WINDOW + 1):
        rows.append({
            "offset": off,
            "mean_all": np.mean(dist[off]) if dist[off] else float("nan"),
            "mean_correct": np.mean(dist_c[off]) if dist_c[off] else float("nan"),
            "mean_wrong": np.mean(dist_w[off]) if dist_w[off] else float("nan"),
            "n": len(dist[off]),
        })

    out = DATA_DIR / "tool_boundary_profile.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["offset", "mean_all", "mean_correct", "mean_wrong", "n"])
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {out} ({len(rows)} rows)")


# ─── 4. baseline_vs_posttool.csv ──────────────────────────────────────────────

def extract_baseline_vs_posttool() -> None:
    tasks = load_valid_tasks(OCODE_RUN)
    WINDOW = 30
    # (turn_label, correct) -> [mean_entropy_per_turn_start]
    buckets: dict = defaultdict(list)

    for tid, task in tasks.items():
        turns = get_ocode_turn_texts(tid)
        tokens = load_int16_tokens(OCODE_RUN, tid)
        if not turns or not tokens or len(turns) < 2:
            continue
        boundaries = find_turn_boundaries(tokens, turns)
        if not boundaries:
            continue
        correct = task["correct"]
        tool_n = 0
        for b in boundaries:
            s, e = b["start_tok"], b["end_tok"]
            if e - s < 5:
                continue
            n = min(WINDOW, e - s)
            me = float(np.mean([tokens[s + k]["entropy_nats"] for k in range(n)]))
            label = "baseline" if b["turn"] == 0 else f"after_tool_{min(tool_n + 1, 4)}+"
            buckets[(label, int(correct))].append(me)
            if b["has_tool"]:
                tool_n += 1

    rows = []
    for (label, correct), vals in sorted(buckets.items()):
        rows.append({
            "turn_label": label,
            "correct": correct,
            "mean_entropy": float(np.mean(vals)),
            "std_entropy": float(np.std(vals)),
            "n": len(vals),
        })

    out = DATA_DIR / "baseline_vs_posttool.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["turn_label", "correct", "mean_entropy", "std_entropy", "n"])
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {out} ({len(rows)} rows)")


# ─── 5. openclaw_entropy_by_outcome.csv ───────────────────────────────────────

def extract_openclaw_entropy_by_outcome() -> None:
    """Per-task mean entropy and wall time for OpenClaw, grouped by correct/wrong."""
    tasks = load_valid_tasks(OC_RUN)
    rows = []
    for tid, task in tasks.items():
        s = task.get("token_entropy_stats", {})
        rows.append({
            "task_id": tid,
            "correct": int(task["correct"]),
            "mean_entropy": s.get("mean", float("nan")),
            "p90_entropy": s.get("p90", float("nan")),
            "max_entropy": s.get("max", float("nan")),
            "n_tokens": s.get("n_tokens", 0),
            "wall_time_min": task.get("wall_time_sec", 0) / 60,
            "traj_steps": len(task.get("trajectory", [])),
        })
    out = DATA_DIR / "openclaw_entropy_by_outcome.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {out} ({len(rows)} rows)")


# ─── main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Extracting data...")
    extract_entropy_calibration()
    extract_token_counts()
    extract_tool_boundary_profile()
    extract_baseline_vs_posttool()
    extract_openclaw_entropy_by_outcome()
    print("Done. All CSVs written to analyze_tools/data/")
