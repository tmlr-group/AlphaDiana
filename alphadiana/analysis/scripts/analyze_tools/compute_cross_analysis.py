"""
compute_cross_analysis.py

Cross-benchmark integrated analysis combining ARM semantic modes with
quantitative entropy/token signals. Addresses four research questions:

1. Timeout composition: reasoning vs tool-calling token breakdown
2. ARM failure mode analysis on HLE (extending GPQA ARM to HLE)
3. Quantitative failure signatures across benchmarks
4. Context rotting: entropy degradation across benchmarks

Outputs (analyze_tools/data/):
  cross_timeout_composition.csv   — token budget split by reasoning/tool
  cross_hle_arm_modes.csv          — ARM mode rates for HLE harnesses
  cross_context_rotting.csv        — head/tail entropy across all harnesses
  cross_arm_entropy_profile.csv    — per-ARM-mode entropy characteristics
"""

import json
import os
import re
import math
import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "analyze_tools" / "data"
REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS_DIR = Path(os.environ.get("ALPHADIANA_RESULTS_DIR", REPO_ROOT / "results")).expanduser()

# ── Paths ────────────────────────────────────────────────────────────
# GPQA (already have ARM, but need per-mode entropy)
GPQA_RESULT_DIRS = {
    "openclaw": RESULTS_DIR / "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
    "opencode": RESULTS_DIR / "full_gpqa_v2_opencode_qwen35_27b_logprobs",
    "zeroclaw": RESULTS_DIR / "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
}

# HLE
HLE_RESULT_DIRS = {
    "directllm": RESULTS_DIR / "phase9_directllm_qwen35_27b_hle_logprobs",
    "opencode": RESULTS_DIR / "20260426-hle-opencode-qwen35_27b-v01",
    "zeroclaw": RESULTS_DIR / "20260426-hle-zeroclaw-qwen35_27b-v01",
}

# Existing data
ARM_TRAJECTORY_CSV = DATA_DIR / "arm_trajectory_features.csv"
ARM_SEGMENT_CSV = DATA_DIR / "arm_segment_features.csv"
DEGRADATION_CSV = DATA_DIR / "degradation_task_features.csv"
GPQA_ENTROPY_CSV = DATA_DIR / "gpqa_entropy_by_harness.csv"

# ── ARM keyword rules (same as extract_arm_features.py) ──────────────
ARM_RULES = {
    "SE": [
        (r"\brule\s+out\b", 3),
        (r"\beliminate\b.{0,30}\b(option|choice|answer)\b", 3),
        (r"\b(option|choice)\s+[A-G]\s+is\s+(incorrect|wrong|not)", 3),
        (r"\bnot\s+(correct|right|valid|consistent)\b.{0,40}(because|since|as)", 2),
        (r"\b(whereas|while|compared\s+to)\b.{0,40}\b(option|choice)\b", 2),
        (r"\b(option|choice)\s+[A-G]\b.{0,50}\b(option|choice)\s+[A-G]\b", 2),
        (r"\b(?:let'?s|let us)\s+(compare|check\s+each|examine\s+each|go through)", 1),
    ],
    "PD": [
        (r"\b(according\s+to|by\s+(the\s+)?(definition|law|principle|theorem|rule|equation))\b", 3),
        (r"\b(the\s+)?(formula|equation|law|theorem|principle)\b.{0,30}\b(gives|yields|states|implies|tells)", 3),
        (r"\b(derive|derivation|calculat(e|ion)|comput(e|ation)|solv(e|ing))\b", 2),
        (r"\b(?:we|let'?s|let us)\s+(compute|calculate|derive|solve|find|determine)\b", 2),
        (r"\b(plug|substitut)\w*\s+(in|into|back)\b", 2),
        (r"\b(therefore|thus|hence|consequently|it\s+follows)\b", 1),
        (r"\b\w+\s*=\s*\d+[.\d]*\b.{0,40}\b\w+\s*=\s*\d+[.\d]*\b", 1),
        (r"\b(?:first|second|third|finally|next)\s*[,:]\s*\b", 1),
    ],
    "IC": [
        (r"\b(clearly|obviously|evidently|undoubtedly|certainly|surely)\b", 3),
        (r"\b(must\s+be|has\s+to\s+be|can\s+only\s+be|is\s+definitely)\b", 3),
        (r"\b(the\s+)?(answer|correct\s+(option|choice)|right\s+one)\s+is\b", 2),
        (r"\bI\s+(would|will|am)\s+(say|choose|select|pick|go\s+with)\b", 2),
        (r"\b(?:so|thus|therefore|hence)\s*[,:]\s*(?:the\s+)?(?:answer|correct)\b.{0,30}\bis\b", 1),
        (r"\bI('m| am)\s+(confident|convinced|sure|certain)\b", 1),
    ],
    "UN": [
        (r"\b(I\s+am\s+)?not\s+(sure|certain|confident|entirely)\b", 3),
        (r"\b(might|may|could)\s+(be|have|also|possibly)\b", 3),
        (r"\b(possibly|perhaps|maybe|potentially)\b", 2),
        (r"\b(uncertain|unclear|ambiguous|confusing)\b", 2),
        (r"\b(roughly|approximately|around|about|somewhere)\b.{0,30}\b\d", 1),
    ],
    "RR": [
        (r"\b(wait|hold\s+on|actually|hmm|oops|let\s+me\s+rethink)\b", 3),
        (r"\b(I\s+)?(made\s+a\s+mistake|that('s|\s+is)\s+wrong|let\s+me\s+correct)\b", 3),
        (r"\b(reconsider|re-evaluat|recalculat|rethink|back\s+up|start\s+over|try\s+again)\b", 2),
        (r"\b(upon\s+reflection|on\s+second\s+thought|I\s+realiz|scratch\s+that)\b", 2),
    ],
}

REASON_KEYWORDS = [
    "think", "reason", "consider", "analyze", "examine", "evaluate",
    "assess", "understand", "determine", "approach", "strategy",
    "step", "first", "second", "third", "note that", "recall",
]

def classify_arm_segment(text: str) -> dict:
    """Classify a single text segment into ARM modes. Returns mode scores dict."""
    text_lower = text.lower()
    scores = {mode: 0 for mode in ARM_RULES}
    for mode, rules in ARM_RULES.items():
        for pattern, weight in rules:
            matches = len(re.findall(pattern, text_lower))
            scores[mode] += matches * weight
    # Determine dominant mode
    max_score = max(scores.values())
    if max_score <= 0:
        # Check if it's generic reasoning
        reason_score = sum(1 for kw in REASON_KEYWORDS if kw in text_lower)
        if reason_score >= 2 or len(text) > 200:
            return {"dominant": "reason", "scores": scores}
        return {"dominant": "reason", "scores": scores}
    dominant = max(scores, key=scores.get)
    return {"dominant": dominant, "scores": scores}


# ═══════════════════════════════════════════════════════════════════════
# 1. HLE ARM Classification
# ═══════════════════════════════════════════════════════════════════════

def compute_hle_arm():
    """Run ARM classification on HLE trajectories."""
    rows = []

    # ── HLE DirectLLM ──
    hle_dir = HLE_RESULT_DIRS["directllm"]
    artifacts_dir = hle_dir / "artifacts"
    print(f"Processing HLE DirectLLM from {artifacts_dir}...")

    for task_dir in sorted(artifacts_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        resp_path = task_dir / "agent" / "response.json"
        if not resp_path.exists():
            continue
        try:
            with open(resp_path) as f:
                resp = json.load(f)
            content = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
            if not content:
                continue
        except Exception:
            continue

        # Split content into reasoning segments (paragraph-level)
        # For DirectLLM, the whole response is one continuous text
        # Split by double newline or major section breaks
        segments = re.split(r'\n{2,}', content)
        segments = [s.strip() for s in segments if len(s.strip()) > 50]

        seg_modes = []
        for seg in segments:
            result = classify_arm_segment(seg)
            seg_modes.append(result["dominant"])

        if not seg_modes:
            continue

        mode_counts = Counter(seg_modes)
        n_seg = len(seg_modes)
        mode_rates = {mode: mode_counts.get(mode, 0) / n_seg for mode in
                      ["SE", "PD", "IC", "UN", "RR", "reason"]}

        # Arm entropy
        arm_ent = 0.0
        for mode, rate in mode_rates.items():
            if rate > 0:
                arm_ent -= rate * math.log(rate)
        arm_ent = round(arm_ent, 4)

        rows.append({
            "task_id": task_id,
            "harness": "directllm",
            "benchmark": "HLE",
            "n_segments": n_seg,
            "arm_sequence": "|".join(seg_modes),
            "dominant_mode": mode_counts.most_common(1)[0][0] if mode_counts else "reason",
            "dominant_rate": round(mode_counts.most_common(1)[0][1] / n_seg, 4) if mode_counts else 0,
            "arm_entropy": arm_ent,
            **{f"rate_{m}": round(mode_rates[m], 4) for m in ["SE", "PD", "IC", "UN", "RR", "reason"]},
        })

    df_direct = pd.DataFrame(rows)
    print(f"  DirectLLM: {len(df_direct)} trajectories classified")

    # ── HLE OpenCode ──
    rows2 = []
    hle_dir2 = HLE_RESULT_DIRS["opencode"]
    artifacts_dir2 = hle_dir2 / "artifacts"
    print(f"Processing HLE OpenCode from {artifacts_dir2}...")

    for task_dir in sorted(artifacts_dir2.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        trace_path = task_dir / "agent" / "normalized_trace.json"
        if not trace_path.exists():
            continue
        try:
            with open(trace_path) as f:
                trace = json.load(f)
            steps = trace.get("steps", [])
            if not steps:
                continue
        except Exception:
            continue

        seg_modes = []
        for step in steps:
            if step.get("role") != "assistant":
                continue
            content = step.get("content", "")
            if not isinstance(content, str) or len(content) < 50:
                continue
            # Skip tool call content (it's structured JSON)
            if step.get("type") == "tool_use" or content.strip().startswith("{"):
                continue
            result = classify_arm_segment(content)
            seg_modes.append(result["dominant"])

        if not seg_modes:
            continue

        mode_counts = Counter(seg_modes)
        n_seg = len(seg_modes)
        mode_rates = {mode: mode_counts.get(mode, 0) / n_seg for mode in
                      ["SE", "PD", "IC", "UN", "RR", "reason"]}
        arm_ent = 0.0
        for mode, rate in mode_rates.items():
            if rate > 0:
                arm_ent -= rate * math.log(rate)
        arm_ent = round(arm_ent, 4)

        rows2.append({
            "task_id": task_id,
            "harness": "opencode",
            "benchmark": "HLE",
            "n_segments": n_seg,
            "arm_sequence": "|".join(seg_modes),
            "dominant_mode": mode_counts.most_common(1)[0][0] if mode_counts else "reason",
            "dominant_rate": round(mode_counts.most_common(1)[0][1] / n_seg, 4) if mode_counts else 0,
            "arm_entropy": arm_ent,
            **{f"rate_{m}": round(mode_rates[m], 4) for m in ["SE", "PD", "IC", "UN", "RR", "reason"]},
        })

    df_oc = pd.DataFrame(rows2)
    print(f"  OpenCode: {len(df_oc)} trajectories classified")

    df_all = pd.concat([df_direct, df_oc], ignore_index=True)
    outpath = DATA_DIR / "cross_hle_arm_modes.csv"
    df_all.to_csv(outpath, index=False)
    print(f"  -> {outpath} ({len(df_all)} rows)")
    return df_all


# ═══════════════════════════════════════════════════════════════════════
# 2. Context Rotting Across Benchmarks
# ═══════════════════════════════════════════════════════════════════════

def compute_token_entropy(logprob_entry: dict) -> float:
    """Compute per-token entropy from top_logprobs."""
    top_logprobs = logprob_entry.get("top_logprobs", [])
    if not top_logprobs:
        logp = logprob_entry.get("logprob", 0)
        return float(-logp) if logp < 0 else 0.0
    # H = -sum(p_i * log(p_i)) where p_i = exp(logprob_i)
    # Normalize
    logps = [t["logprob"] for t in top_logprobs]
    # Softmax normalization for top-k
    max_logp = max(logps)
    probs = [math.exp(lp - max_logp) for lp in logps]
    total = sum(probs)
    if total <= 0:
        return 0.0
    probs = [p / total for p in probs]
    entropy = -sum(p * math.log(p) for p in probs if p > 0)
    return round(entropy, 6)


def compute_context_rotting():
    """Compute head/tail entropy for all harnesses across GPQA and HLE."""
    rows = []

    # ── HLE: DirectLLM ──
    print("Computing context rotting for HLE DirectLLM...")
    logprobs_dir = HLE_RESULT_DIRS["directllm"] / "logprobs"
    for lp_file in sorted(logprobs_dir.glob("*.jsonl")):
        task_id = lp_file.stem
        try:
            with open(lp_file) as f:
                tokens = [json.loads(line) for line in f if line.strip()]
            if len(tokens) < 100:
                continue
            entropies = [compute_token_entropy(t) for t in tokens]
            n = len(entropies)
            q1_end = max(n // 4, 1)
            q4_start = max(3 * n // 4, 1)
            head_ent = np.mean(entropies[:q1_end])
            tail_ent = np.mean(entropies[q4_start:])
            # Low-ent share: fraction of tokens in bottom quartile
            cutoff = np.percentile(entropies, 25)
            low_share = sum(1 for e in entropies if e < cutoff) / n
            top1_probs = [math.exp(t.get("logprob", t.get("top_logprobs", [{}])[0].get("logprob", 0)))
                          for t in tokens]
            top1_mean = np.mean([p for p in top1_probs if p > 0])

            rows.append({
                "task_id": task_id, "harness": "directllm", "benchmark": "HLE",
                "n_tokens": n, "head_entropy": round(head_ent, 6),
                "tail_entropy": round(tail_ent, 6),
                "delta_entropy": round(tail_ent - head_ent, 6),
                "low_ent_share": round(low_share, 4),
                "top1_prob_mean": round(top1_mean, 4),
            })
        except Exception as e:
            continue
    print(f"  HLE DirectLLM: {len(rows)} trajectories")

    # ── HLE: OpenCode ──
    print("Computing context rotting for HLE OpenCode...")
    logprobs_dir2 = HLE_RESULT_DIRS["opencode"] / "logprobs"
    count_before = len(rows)
    for lp_file in sorted(logprobs_dir2.glob("*.jsonl")):
        task_id = lp_file.stem
        try:
            with open(lp_file) as f:
                tokens = [json.loads(line) for line in f if line.strip()]
            if len(tokens) < 100:
                continue
            entropies = [compute_token_entropy(t) for t in tokens]
            n = len(entropies)
            q1_end = max(n // 4, 1)
            q4_start = max(3 * n // 4, 1)
            head_ent = np.mean(entropies[:q1_end])
            tail_ent = np.mean(entropies[q4_start:])
            cutoff = np.percentile(entropies, 25)
            low_share = sum(1 for e in entropies if e < cutoff) / n
            top1_probs = [math.exp(t.get("logprob", t.get("top_logprobs", [{}])[0].get("logprob", 0)))
                          for t in tokens]
            top1_mean = np.mean([p for p in top1_probs if p > 0])

            rows.append({
                "task_id": task_id, "harness": "opencode", "benchmark": "HLE",
                "n_tokens": n, "head_entropy": round(head_ent, 6),
                "tail_entropy": round(tail_ent, 6),
                "delta_entropy": round(tail_ent - head_ent, 6),
                "low_ent_share": round(low_share, 4),
                "top1_prob_mean": round(top1_mean, 4),
            })
        except Exception:
            continue
    print(f"  HLE OpenCode: {len(rows) - count_before} trajectories")

    # ── HLE: ZeroClaw ──
    print("Computing context rotting for HLE ZeroClaw...")
    logprobs_dir3 = HLE_RESULT_DIRS["zeroclaw"] / "logprobs"
    count_before = len(rows)
    for lp_file in sorted(logprobs_dir3.glob("*.jsonl")):
        task_id = lp_file.stem
        try:
            with open(lp_file) as f:
                tokens = [json.loads(line) for line in f if line.strip()]
            if len(tokens) < 100:
                continue
            entropies = [compute_token_entropy(t) for t in tokens]
            n = len(entropies)
            q1_end = max(n // 4, 1)
            q4_start = max(3 * n // 4, 1)
            head_ent = np.mean(entropies[:q1_end])
            tail_ent = np.mean(entropies[q4_start:])
            cutoff = np.percentile(entropies, 25)
            low_share = sum(1 for e in entropies if e < cutoff) / n
            top1_probs = [math.exp(t.get("logprob", t.get("top_logprobs", [{}])[0].get("logprob", 0)))
                          for t in tokens]
            top1_mean = np.mean([p for p in top1_probs if p > 0])

            rows.append({
                "task_id": task_id, "harness": "zeroclaw", "benchmark": "HLE",
                "n_tokens": n, "head_entropy": round(head_ent, 6),
                "tail_entropy": round(tail_ent, 6),
                "delta_entropy": round(tail_ent - head_ent, 6),
                "low_ent_share": round(low_share, 4),
                "top1_prob_mean": round(top1_mean, 4),
            })
        except Exception:
            continue
    print(f"  HLE ZeroClaw: {len(rows) - count_before} trajectories")

    df = pd.DataFrame(rows)
    outpath = DATA_DIR / "cross_context_rotting.csv"
    df.to_csv(outpath, index=False)
    print(f"  -> {outpath} ({len(df)} rows)")
    return df


# ═══════════════════════════════════════════════════════════════════════
# 3. Timeout Composition: Reasoning vs Tool Tokens
# ═══════════════════════════════════════════════════════════════════════

def compute_timeout_composition():
    """Break down token usage into reasoning vs tool-call output for OpenCode."""
    rows = []

    # ── GPQA OpenCode ──
    print("Computing timeout composition for GPQA OpenCode...")
    gpqa_dir = GPQA_RESULT_DIRS["opencode"]
    artifacts_dir = gpqa_dir / "artifacts"

    for task_dir in sorted(artifacts_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        trace_path = task_dir / "agent" / "normalized_trace.json"
        if not trace_path.exists():
            continue
        try:
            with open(trace_path) as f:
                trace = json.load(f)
            steps = trace.get("steps", [])
            if not steps:
                continue
        except Exception:
            continue

        reasoning_tokens = 0
        tool_tokens = 0
        tool_calls = 0
        for step in steps:
            content = step.get("content", "")
            if not isinstance(content, str):
                continue
            n_tokens = len(content) // 3  # rough estimate: ~3 chars per token
            if step.get("role") == "assistant":
                reasoning_tokens += n_tokens
            elif step.get("role") == "tool" or step.get("type") in ("tool_use", "tool_result"):
                tool_tokens += n_tokens
                tool_calls += 1

        total = reasoning_tokens + tool_tokens
        if total == 0:
            continue

        rows.append({
            "task_id": task_id, "harness": "opencode", "benchmark": "GPQA",
            "reasoning_tokens": reasoning_tokens,
            "tool_tokens": tool_tokens,
            "total_tokens": total,
            "reasoning_pct": round(100 * reasoning_tokens / total, 1),
            "tool_pct": round(100 * tool_tokens / total, 1),
            "tool_calls": tool_calls,
        })

    # ── HLE OpenCode ──
    print("Computing timeout composition for HLE OpenCode...")
    hle_dir = HLE_RESULT_DIRS["opencode"]
    artifacts_dir2 = hle_dir / "artifacts"

    for task_dir in sorted(artifacts_dir2.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        trace_path = task_dir / "agent" / "normalized_trace.json"
        if not trace_path.exists():
            continue
        try:
            with open(trace_path) as f:
                trace = json.load(f)
            steps = trace.get("steps", [])
            if not steps:
                continue
        except Exception:
            continue

        reasoning_tokens = 0
        tool_tokens = 0
        tool_calls = 0
        for step in steps:
            content = step.get("content", "")
            if not isinstance(content, str):
                continue
            n_tokens = len(content) // 3
            if step.get("role") == "assistant":
                reasoning_tokens += n_tokens
            elif step.get("role") == "tool" or step.get("type") in ("tool_use", "tool_result"):
                tool_tokens += n_tokens
                tool_calls += 1

        total = reasoning_tokens + tool_tokens
        if total == 0:
            continue

        rows.append({
            "task_id": task_id, "harness": "opencode", "benchmark": "HLE",
            "reasoning_tokens": reasoning_tokens,
            "tool_tokens": tool_tokens,
            "total_tokens": total,
            "reasoning_pct": round(100 * reasoning_tokens / total, 1),
            "tool_pct": round(100 * tool_tokens / total, 1),
            "tool_calls": tool_calls,
        })

    df = pd.DataFrame(rows)
    outpath = DATA_DIR / "cross_timeout_composition.csv"
    df.to_csv(outpath, index=False)
    print(f"  -> {outpath} ({len(df)} rows)")

    # Print summary
    for bench in ["GPQA", "HLE"]:
        subset = df[df["benchmark"] == bench]
        if len(subset) == 0:
            continue
        print(f"\n  {bench} OpenCode timeout composition (mean):")
        print(f"    Reasoning: {subset['reasoning_pct'].mean():.1f}%")
        print(f"    Tool output: {subset['tool_pct'].mean():.1f}%")
        print(f"    Tool calls/traj: {subset['tool_calls'].mean():.1f}")
        print(f"    Mean total tokens: {subset['total_tokens'].mean():.0f}")

    return df


# ═══════════════════════════════════════════════════════════════════════
# 4. ARM-Entropy Integrated Analysis (GPQA)
# ═══════════════════════════════════════════════════════════════════════

def compute_arm_entropy_profile():
    """Cross-reference ARM modes with per-token entropy from GPQA logprobs."""
    # Load arm segment features
    arm_seg = pd.read_csv(ARM_SEGMENT_CSV)

    # Load degradation features for entropy data
    deg = pd.read_csv(DEGRADATION_CSV)

    rows = []
    # Group by task_id, harness, arm_mode and compute entropy stats
    for (task_id, harness, mode), group in arm_seg.groupby(["task_id", "harness", "arm_mode"]):
        # Get token-level entropy for this trajectory from degradation data
        traj_data = deg[(deg["task_id"] == task_id) & (deg["harness"] == harness)]
        if len(traj_data) == 0:
            continue

        rows.append({
            "task_id": task_id,
            "harness": harness,
            "arm_mode": mode,
            "n_segments": len(group),
            "mean_arm_score": group["arm_score"].mean(),
            "mean_text_len": group["text_len"].mean(),
            "harness_entropy": traj_data["harness_mean_entropy"].iloc[0] if len(traj_data) > 0 else np.nan,
            "repeated_ngram_rate": traj_data["harness_repeated_ngram_rate"].iloc[0] if len(traj_data) > 0 else np.nan,
        })

    df = pd.DataFrame(rows)
    outpath = DATA_DIR / "cross_arm_entropy_profile.csv"
    df.to_csv(outpath, index=False)
    print(f"  -> {outpath} ({len(df)} rows)")

    # Summary by ARM mode
    print("\n  ARM mode entropy profile (GPQA, pooled):")
    for mode in ["PD", "reason", "IC", "UN", "RR", "SE"]:
        subset = df[df["arm_mode"] == mode]
        if len(subset) == 0:
            continue
        print(f"    {mode}: n={len(subset)}, mean entropy={subset['harness_entropy'].mean():.4f}, "
              f"mean ngram={subset['repeated_ngram_rate'].mean():.4f}")

    return df


# ═══════════════════════════════════════════════════════════════════════
# 5. Outcome-conditioned summary tables
# ═══════════════════════════════════════════════════════════════════════

def load_gpqa_outcomes():
    """Load GPQA paired outcomes from degradation data."""
    deg = pd.read_csv(DEGRADATION_CSV)
    return dict(zip(
        deg["task_id"].astype(str) + "_" + deg["harness"],
        deg["paired_outcome"]
    ))


def load_hle_outcomes():
    """Load HLE outcomes from the pre-computed data."""
    hle_ent = pd.read_csv(DATA_DIR / "hle_entropy_by_outcome.csv")
    # This file should have task_id, harness, outcome info
    # Try to extract from the data we have
    outcomes = {}
    try:
        # Use paired net gain data
        paired = pd.read_csv(DATA_DIR / "hle_paired_net_gain.csv")
        # This might not have per-task outcomes, let's check
    except Exception:
        pass

    # Load from logprob data directly - check if answer is correct
    return outcomes


def merge_outcomes(df, benchmark="GPQA"):
    """Merge outcome labels into a dataframe."""
    if benchmark == "GPQA":
        outcomes = load_gpqa_outcomes()
        df["key"] = df["task_id"].astype(str) + "_" + df["harness"]
        df["paired_outcome"] = df["key"].map(outcomes)
        df = df.drop(columns=["key"])
    return df


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 60)
    print("Cross-Analysis: ARM + Quantitative Integration")
    print("=" * 60)

    # 1. HLE ARM Classification
    print("\n[1/4] HLE ARM Classification")
    hle_arm = compute_hle_arm()

    # 2. Context Rotting Across Benchmarks
    print("\n[2/4] Context Rotting Across Benchmarks")
    rotting = compute_context_rotting()

    # 3. Timeout Composition
    print("\n[3/4] Timeout Composition: Reasoning vs Tool Tokens")
    timeout = compute_timeout_composition()

    # 4. ARM-Entropy Integrated Profile
    print("\n[4/4] ARM-Entropy Integrated Profile (GPQA)")
    arm_ent = compute_arm_entropy_profile()

    print("\n" + "=" * 60)
    print("All outputs written to analyze_tools/data/cross_*.csv")
    print("=" * 60)
