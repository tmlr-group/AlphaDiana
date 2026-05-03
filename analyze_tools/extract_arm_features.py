"""
extract_arm_features.py

Extract Agentic Reasoning Mode (ARM) features from GPQA-Diamond trajectories
across OpenClaw, OpenCode, and ZeroClaw harnesses.

ARM taxonomy:
  SE  = Systematic Elimination   — explicit option comparison, rule-out
  PD  = Principle-Based Deduction — cite laws/theorems, work through derivations
  CV  = Computational Verification — tool call with numerical result
  IC  = Intuitive Commitment     — short reasoning, "clearly"/"must be"
  UN  = Uncertainty Navigation   — "not sure", "could be", "likely"
  RR  = Recovery-Replanning      — "wait", "actually", reconsideration
  LS  = Loop Stall               — high ngram repeat + low entropy (from existing CSV)

Outputs:
  data/arm_segment_features.csv  — per-segment ARM labels (long form)
  data/arm_trajectory_features.csv — per-trajectory ARM mode rates + transition matrix
  data/arm_mode_rates.csv        — (harness, outcome) × ARM mode frequency
"""

import os
import re
import json
import pandas as pd
import numpy as np
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "analyze_tools", "data")

RESULT_DIRS = {
    "openclaw": os.path.join(BASE, "results", "full_gpqa_v2_openclaw_qwen35_27b_logprobs"),
    "opencode": os.path.join(BASE, "results", "full_gpqa_v2_opencode_qwen35_27b_logprobs"),
    "zeroclaw": os.path.join(BASE, "results", "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs"),
}

DEGRADATION_CSV = os.path.join(DATA, "degradation_task_features.csv")

# ── ARM keyword rules ──────────────────────────────────────────────

# Each ARM mode has a list of (regex, weight) tuples.
# Weight > 0 = evidence FOR the mode; weight < 0 = evidence AGAINST.

ARM_RULES = {
    "SE": [
        # Strong signals
        (r"\brule\s+out\b", 3),
        (r"\beliminate\b.{0,30}\b(option|choice|answer)\b", 3),
        (r"\b(option|choice)\s+[A-D]\s+is\s+(incorrect|wrong|not)", 3),
        # Moderate signals
        (r"\bnot\s+(correct|right|valid|consistent)\b.{0,40}(because|since|as)", 2),
        (r"\b(whereas|while|compared\s+to)\b.{0,40}\b(option|choice)\b", 2),
        (r"\b(option|choice)\s+[A-D]\b.{0,50}\b(option|choice)\s+[A-D]\b", 2),
        # Weak signals
        (r"\b(?:let'?s|let us)\s+(compare|check\s+each|examine\s+each|go through)", 1),
    ],
    "PD": [
        # Strong signals
        (r"\b(according\s+to|by\s+(the\s+)?(definition|law|principle|theorem|rule|equation))\b", 3),
        (r"\b(the\s+)?(formula|equation|principle|law|theorem)\s+(of|for|states|gives)\b", 3),
        (r"\b(deriv(?:e|ing|ation)|rearrang(?:e|ing|ement)|substitut(?:e|ing|ion))\b", 2),
        # Chemistry-specific
        (r"\b(react(?:ion|s|ivity)|mechanism|nucleophil|electrophil|oxidation|reduction|acid|base|catalyst)\b", 2),
        (r"\b(orbital|conjugat|aromatic|resonance|stereochem|chiral|enantiomer)\b", 2),
        # Physics-specific
        (r"\b(hamiltonian|wave\s*function|schr[oö]dinger|lagrangian|conservation\s+of)\b", 3),
        (r"\b(quantum|classical|relativistic|newtonian|maxwell)\b", 1),
        # Biology-specific
        (r"\b(pathway|enzyme|gene\s+express|transcription|translation|receptor|ligand)\b", 2),
        # Math/formal
        (r"\\frac\{|\\sum_|\\int_|\\prod_|\\lim_", 2),
        (r"\b(compute|calculate|solve).{0,30}\b(using|via|with|from)\b", 1),
    ],
    "IC": [
        (r"\b(clearly|obviously|undoubtedly|without\s+(a\s+)?doubt|definitely)\b", 3),
        (r"\b(must\s+be|has\s+to\s+be|it\s+is\s+(clearly|obviously))\b", 2),
        (r"\b(the\s+)?(answer\s+is|correct\s+(answer|option|choice)\s+is)\b", 2),
        (r"\b(straightforward|trivial|simple|easy)\b", 1),
    ],
    "UN": [
        (r"\b(not\s+sure|uncertain|unsure|not\s+certain)\b", 3),
        (r"\b(could\s+(be|have)|might\s+(be|have)|may\s+(be|have))\b", 2),
        (r"\b(perhaps|possibly|maybe|I\s+(would\s+)?guess)\b", 2),
        (r"\b(lean\s+(toward|towards)|tend\s+to|more\s+likely|probably)\b", 2),
        (r"\b(I\s+think|I\s+believe|in\s+my\s+opinion|it\s+seems)\b", 1),
        (r"\b(hesitat|tentative|ambivalent|on\s+the\s+fence)\b", 2),
    ],
    "RR": [
        (r"\b(wait[\s,!.]+(actually|no|that|let|I|this))\b", 3),
        (r"\b(actually[\s,!.]+(no|that|I|let|this|the))\b", 2),
        (r"\b(let\s+me\s+(reconsid|re-examin|rethink|go\s+back|start\s+over|try\s+again))\b", 3),
        (r"\b(that'?s\s+(wrong|not\s+right|incorrect|a\s+mistake))\b", 3),
        (r"\b(I\s+(made\s+)?(a\s+)?mistake|my\s+(previous|earlier|initial)\s+(answer|reasoning|analysis|approach))\b", 3),
        (r"\b(on\s+second\s+thought|upon\s+(further\s+)?reflection|I\s+need\s+to\s+correct)\b", 2),
        (r"\b(let\s+me\s+(correct|fix|revise|amend))\b", 2),
        (r"\b(scratch\s+that|never\s+mind|disregard\s+(that|the))\b", 2),
    ],
}

# Step types that indicate tool use (for CV mode)
TOOL_USE_TYPES = {"tool_use", "tool_call", "function_call"}


def extract_assistant_segments(trajectory):
    """Extract (step_index, role, text_content, has_tool_use) per step."""
    segments = []
    for i, step in enumerate(trajectory):
        role = step.get("role", "")
        stype = step.get("type", "")

        # Skip system messages
        if role == "system" or stype == "system":
            continue

        content = step.get("content", "")
        has_tool = False
        text = ""

        if isinstance(content, list):
            for chunk in content:
                if isinstance(chunk, dict):
                    ct = chunk.get("type", "")
                    if ct in TOOL_USE_TYPES:
                        has_tool = True
                        text += f" [TOOL:{chunk.get('name', '?')}] "
                    elif ct == "tool_result":
                        has_tool = True
                        tc = chunk.get("content", "")
                        if isinstance(tc, str):
                            text += f" [TOOL_RESULT:{tc[:100]}] "
                    elif ct == "text":
                        text += chunk.get("text", "") + " "
        elif isinstance(content, str):
            # Check if it's JSON metadata (step-start, etc.)
            if content.startswith('{"part"'):
                try:
                    meta = json.loads(content)
                    part = meta.get("part", {})
                    if part.get("type") == "step-start":
                        continue  # skip step-start metadata
                except (json.JSONDecodeError, KeyError):
                    pass
            text = content

        text = text.strip()
        if text:
            segments.append({
                "step": i,
                "role": role,
                "has_tool_use": has_tool,
                "text": text,
            })

    return segments


def classify_segment(text, has_tool_use):
    """Score text for each ARM mode, return mode with highest score.
    Returns (mode, score, all_scores_dict)."""
    scores = {}
    for mode, rules in ARM_RULES.items():
        score = 0
        for pattern, weight in rules:  # rules is a list of (pattern, weight) tuples
            matches = len(re.findall(pattern, text, re.IGNORECASE))
            score += matches * weight
        scores[mode] = score

    # CV is special: tool_use = automatic CV label
    if has_tool_use:
        scores["CV"] = max(scores.get("CV", 0), 5)

    # If no mode has a positive score, default to "reason" (undifferentiated)
    max_score = max(scores.values())
    if max_score <= 0:
        return "reason", 0, scores

    # Break ties: RR > CV > SE > PD > IC > UN
    tie_priority = ["RR", "CV", "SE", "PD", "IC", "UN"]
    best_mode = None
    for mode in tie_priority:
        if scores.get(mode, 0) == max_score:
            best_mode = mode
            break

    return best_mode, max_score, scores


def compute_transition_features(arm_sequence):
    """Compute ARM transition counts and entropy."""
    if len(arm_sequence) < 2:
        return {}, []

    transitions = Counter()
    for i in range(len(arm_sequence) - 1):
        pair = (arm_sequence[i], arm_sequence[i + 1])
        transitions[pair] += 1

    # Normalize to rates
    n_pairs = len(arm_sequence) - 1
    trans_rates = {f"{a}->{b}": c / n_pairs for (a, b), c in transitions.items()}

    return trans_rates, list(transitions.keys())


def process_trajectory(task_id, harness, trajectory, paired_outcome, harness_correct,
                        deg_row=None):
    """Process a single trajectory: extract segments, classify, aggregate."""
    segments = extract_assistant_segments(trajectory)

    if not segments:
        return None, []

    arm_sequence = []
    segment_records = []

    for seg in segments:
        mode, score, all_scores = classify_segment(seg["text"], seg["has_tool_use"])
        arm_sequence.append(mode)

        segment_records.append({
            "task_id": task_id,
            "harness": harness,
            "paired_outcome": paired_outcome,
            "harness_correct": harness_correct,
            "step": seg["step"],
            "role": seg["role"],
            "has_tool_use": seg["has_tool_use"],
            "arm_mode": mode,
            "arm_score": score,
            "text_len": len(seg["text"]),
            "text_head": seg["text"][:200],
            **{f"arm_score_{k}": v for k, v in all_scores.items()},
        })

    # Aggregate trajectory-level features
    n_segments = len(arm_sequence)
    arm_counts = Counter(arm_sequence)
    arm_rates = {f"rate_{mode}": arm_counts.get(mode, 0) / n_segments
                 for mode in ["SE", "PD", "CV", "IC", "UN", "RR", "reason"]}

    trans_rates, trans_pairs = compute_transition_features(arm_sequence)

    # Sequence diversity: entropy of ARM distribution
    arm_dist = [arm_counts.get(m, 0) / n_segments for m in ["SE", "PD", "CV", "IC", "UN", "RR", "reason"]]
    arm_entropy = -sum(p * np.log(p) if p > 0 else 0 for p in arm_dist)

    # Dominant mode and its rate
    dominant_mode = max(arm_counts, key=arm_counts.get)
    dominant_rate = arm_counts[dominant_mode] / n_segments

    traj_record = {
        "task_id": task_id,
        "harness": harness,
        "paired_outcome": paired_outcome,
        "harness_correct": harness_correct,
        "n_segments": n_segments,
        "arm_sequence": "|".join(arm_sequence),
        "dominant_mode": dominant_mode,
        "dominant_rate": dominant_rate,
        "arm_entropy": round(arm_entropy, 4),
        **{k: round(v, 4) for k, v in arm_rates.items()},
        **{k: round(v, 4) for k, v in trans_rates.items()},
    }

    # Append degradation features if available
    if deg_row is not None:
        for col in ["looping_marker_count", "self_correction_marker_count",
                     "uncertainty_marker_count", "harness_repeated_ngram_rate",
                     "harness_mean_entropy", "harness_n_tokens",
                     "harness_malformed_prediction", "harness_missing_boxed_answer"]:
            traj_record[col] = deg_row.get(col, None)

    return traj_record, segment_records


def main():
    # Load degradation features for labels and existing metrics
    deg = pd.read_csv(DEGRADATION_CSV)
    print(f"Loaded degradation features: {len(deg)} rows")

    all_traj = []
    all_segments = []

    for harness_key, result_dir in RESULT_DIRS.items():
        tasks_dir = os.path.join(result_dir, "tasks")
        if not os.path.isdir(tasks_dir):
            print(f"  SKIP {harness_key}: no tasks/ directory at {tasks_dir}")
            continue

        task_files = sorted(os.listdir(tasks_dir))
        hdeg = deg[deg["harness"] == harness_key]
        n_processed = 0

        for fname in task_files:
            if not fname.endswith(".json"):
                continue
            task_id = fname.replace(".json", "")
            fpath = os.path.join(tasks_dir, fname)

            try:
                with open(fpath) as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                print(f"  WARN: cannot read {fpath}: {e}")
                continue

            rec = raw[-1] if isinstance(raw, list) else raw
            trajectory = rec.get("trajectory", [])
            if not trajectory:
                continue

            harness_correct = rec.get("correct", None)
            deg_row = hdeg[hdeg["task_id"] == task_id]
            if len(deg_row) == 0:
                paired_outcome = "unknown"
            else:
                paired_outcome = deg_row.iloc[0]["paired_outcome"]

            deg_dict = deg_row.iloc[0].to_dict() if len(deg_row) > 0 else None

            traj_rec, seg_recs = process_trajectory(
                task_id, harness_key, trajectory,
                paired_outcome, harness_correct, deg_dict
            )

            if traj_rec:
                all_traj.append(traj_rec)
                all_segments.extend(seg_recs)
                n_processed += 1

        print(f"  {harness_key}: processed {n_processed} trajectories, {len(all_segments)} segments so far")

    # ── Save outputs ──
    df_traj = pd.DataFrame(all_traj)
    df_seg = pd.DataFrame(all_segments)

    # Merge with degradation outcome labels for DirectLLM reference
    direct_info = deg[["task_id", "harness", "direct_correct", "direct_n_tokens",
                        "direct_mean_entropy", "paired_outcome"]].drop_duplicates("task_id")
    # Keep only one harness's row per task for direct info
    direct_info = direct_info.drop_duplicates("task_id")

    traj_out = os.path.join(DATA, "arm_trajectory_features.csv")
    seg_out = os.path.join(DATA, "arm_segment_features.csv")
    df_traj.to_csv(traj_out, index=False)
    df_seg.to_csv(seg_out, index=False)
    print(f"\nWrote {traj_out} ({len(df_traj)} rows)")
    print(f"Wrote {seg_out} ({len(df_seg)} rows)")

    # ── Aggregated mode rates ──
    mode_cols = [c for c in df_traj.columns if c.startswith("rate_")]
    outcome_groups = df_traj.groupby(["harness", "paired_outcome"])

    mode_records = []
    for (harness, outcome), grp in outcome_groups:
        rec = {"harness": harness, "paired_outcome": outcome, "n": len(grp)}
        for col in mode_cols:
            mode_name = col.replace("rate_", "")
            rec[mode_name] = round(grp[col].mean(), 4)
        mode_records.append(rec)

    df_modes = pd.DataFrame(mode_records)
    modes_out = os.path.join(DATA, "arm_mode_rates.csv")
    df_modes.to_csv(modes_out, index=False)
    print(f"Wrote {modes_out} ({len(df_modes)} rows)")

    # ── Transition summary ──
    trans_cols = [c for c in df_traj.columns if "->" in c]
    if trans_cols:
        trans_summary = df_traj.groupby(["harness", "paired_outcome"])[trans_cols].mean().reset_index()
        trans_out = os.path.join(DATA, "arm_transition_rates.csv")
        trans_summary.to_csv(trans_out, index=False)
        print(f"Wrote {trans_out}")

    # ── Quick summary print ──
    print("\n=== ARM Mode Rates by (harness, paired_outcome) ===")
    display_cols = ["harness", "paired_outcome", "n"] + [c.replace("rate_", "") for c in mode_cols]
    print(df_modes[display_cols].to_string(index=False))

    # ── Top ARM discriminators: correct vs wrong within each harness ──
    print("\n=== ARM mode deltas (correct - wrong) per harness ===")
    for harness in df_modes["harness"].unique():
        hdf = df_modes[df_modes["harness"] == harness]
        correct_row = hdf[hdf["paired_outcome"].isin(["both_correct", "rescue"])]
        wrong_row = hdf[hdf["paired_outcome"].isin(["both_wrong", "regression"])]

        if len(correct_row) == 0 or len(wrong_row) == 0:
            continue

        # Weighted average
        corr_n = correct_row["n"].sum()
        wron_n = wrong_row["n"].sum()
        if corr_n == 0 or wron_n == 0:
            continue

        corr_rates = {}
        wron_rates = {}
        for col in [c.replace("rate_", "") for c in mode_cols]:
            corr_rates[col] = (correct_row[col] * correct_row["n"]).sum() / corr_n
            wron_rates[col] = (wrong_row[col] * wrong_row["n"]).sum() / wron_n

        print(f"\n{harness} (correct n={corr_n}, wrong n={wron_n}):")
        for mode in ["SE", "PD", "CV", "IC", "UN", "RR", "reason"]:
            delta = corr_rates.get(mode, 0) - wron_rates.get(mode, 0)
            marker = " ***" if abs(delta) > 0.03 else ""
            print(f"  {mode}: C={corr_rates.get(mode,0):.3f}, W={wron_rates.get(mode,0):.3f}, Δ={delta:+.3f}{marker}")


if __name__ == "__main__":
    main()
