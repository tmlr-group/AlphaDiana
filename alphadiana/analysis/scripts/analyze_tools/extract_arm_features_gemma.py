"""
extract_arm_features_gemma.py — ARM classification for Gemma4-31B on GPQA-Diamond.
Reuses ARM_RULES from extract_arm_features.py, applied to Gemma artifact directories.
Handles DirectLLM (response.json), OpenClaw/OpenCode/ZeroClaw (normalized_trace.json),
and Gemma's reasoning_content (thinking text).
"""

import os, re, json
import pandas as pd
import numpy as np
from collections import Counter, defaultdict
from pathlib import Path
import math

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "analyze_tools" / "data"
REPO_ROOT = Path(__file__).resolve().parents[4]
RESULTS_DIR = Path(os.environ.get("ALPHADIANA_RESULTS_DIR", REPO_ROOT / "results")).expanduser()

RESULT_DIRS = {
    "directllm": RESULTS_DIR / "full_gpqa_directllm_gemma4_31b_logprobs",
    "openclaw": RESULTS_DIR / "full_gpqa_openclaw_gemma4_31b_logprobs",
    "opencode": RESULTS_DIR / "full_gpqa_opencode_gemma4_31b_logprobs",
    "zeroclaw": RESULTS_DIR / "full_gpqa_zeroclaw_gemma4_31b_logprobs",
}

# ── ARM keyword rules (same as extract_arm_features.py) ──────────────
ARM_RULES = {
    "SE": [
        (r"\brule\s+out\b", 3),
        (r"\beliminate\b.{0,30}\b(option|choice|answer)\b", 3),
        (r"\b(option|choice)\s+[A-D]\s+is\s+(incorrect|wrong|not)", 3),
        (r"\bnot\s+(correct|right|valid|consistent)\b.{0,40}(because|since|as)", 2),
        (r"\b(whereas|while|compared\s+to)\b.{0,40}\b(option|choice)\b", 2),
        (r"\b(option|choice)\s+[A-D]\b.{0,50}\b(option|choice)\s+[A-D]\b", 2),
        (r"\b(?:let'?s|let us)\s+(compare|check\s+each|examine\s+each|go through)", 1),
        (r"\b(each|every)\s+(option|choice)\b", 1),
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
        (r"\b(?:let'?s|let us)\s+analy[sz]e\b", 1),
        (r"\b(step\s+\d+|step\s+by\s+step)\b", 1),
    ],
    "IC": [
        (r"\b(clearly|obviously|evidently|undoubtedly|certainly|surely)\b", 3),
        (r"\b(must\s+be|has\s+to\s+be|can\s+only\s+be|is\s+definitely)\b", 3),
        (r"\b(the\s+)?(answer|correct\s+(option|choice)|right\s+one)\s+is\b", 2),
        (r"\bI\s+(would|will|am)\s+(say|choose|select|pick|go\s+with)\b", 2),
        (r"\b(?:so|thus|therefore|hence)\s*[,:]\s*(?:the\s+)?(?:answer|correct)\b.{0,30}\bis\b", 1),
        (r"\bI('m| am)\s+(confident|convinced|sure|certain)\b", 1),
        (r"\bthis\s+(is|means|indicates)\s+(clearly|definitely|undoubtedly)\b", 1),
    ],
    "UN": [
        (r"\b(I\s+am\s+)?not\s+(sure|certain|confident|entirely)\b", 3),
        (r"\b(might|may|could)\s+(be|have|also|possibly)\b", 3),
        (r"\b(possibly|perhaps|maybe|potentially)\b", 2),
        (r"\b(uncertain|unclear|ambiguous|confusing)\b", 2),
        (r"\b(roughly|approximately|around|about|somewhere)\b.{0,30}\b\d", 1),
        (r"\b(I\s+(?:would|could|might)\s+(guess|estimate|speculate))\b", 1),
    ],
    "RR": [
        (r"\b(wait|hold\s+on|actually|hmm|oops|let\s+me\s+rethink)\b", 3),
        (r"\b(I\s+)?(made\s+a\s+mistake|that('s|\s+is)\s+wrong|let\s+me\s+correct)\b", 3),
        (r"\b(reconsider|re-evaluat|recalculat|rethink|back\s+up|let\s+me\s+try|start\s+over|try\s+again)\b", 2),
        (r"\b(upon\s+reflection|on\s+second\s+thought|I\s+realiz|scratch\s+that)\b", 2),
    ],
}

REASON_KW = ["think", "reason", "consider", "analyze", "examine", "evaluate",
    "assess", "understand", "determine", "approach", "strategy",
    "step", "first", "second", "third", "note that", "recall"]


def classify_segment(text: str) -> dict:
    """Classify a text segment into ARM modes."""
    if len(text) < 50:
        return {"dominant": "reason", "scores": {m: 0 for m in ARM_RULES}}

    text_lower = text.lower()
    scores = {mode: 0 for mode in ARM_RULES}
    for mode, rules in ARM_RULES.items():
        for pattern, weight in rules:
            matches = len(re.findall(pattern, text_lower))
            scores[mode] += matches * weight

    max_score = max(scores.values())
    if max_score <= 0:
        reason_score = sum(1 for kw in REASON_KW if kw in text_lower)
        return {"dominant": "reason", "scores": scores}

    dominant = max(scores, key=scores.get)
    return {"dominant": dominant, "scores": scores}


def extract_directllm(artifact_dir, task_id):
    """Extract ARM segments from DirectLLM response.json."""
    resp_path = os.path.join(artifact_dir, "agent", "response.json")
    if not os.path.exists(resp_path):
        return []

    with open(resp_path) as f:
        resp = json.load(f)

    segments = []
    choices = resp.get('choices', [])
    if choices:
        msg = choices[0].get('message', {})
        # Reasoning content (thinking) as separate segment
        reasoning = msg.get('reasoning_content', '')
        if reasoning and len(reasoning) > 100:
            segments.append(reasoning)
        # Main content split by double newlines
        content = msg.get('content', '')
        if content:
            paras = [p.strip() for p in content.split('\n\n') if len(p.strip()) > 80]
            segments.extend(paras)
    return segments


def extract_agent(artifact_dir, task_id, harness):
    """Extract ARM segments from normalized_trace.json (OpenClaw/OpenCode/ZeroClaw)."""
    trace_path = os.path.join(artifact_dir, "agent", "normalized_trace.json")
    if not os.path.exists(trace_path):
        return []

    with open(trace_path) as f:
        trace = json.load(f)

    segments = []
    steps = trace.get('steps', [])

    for step in steps:
        if not isinstance(step, dict):
            continue

        role = step.get('role', '')
        content = step.get('content', '')
        if not isinstance(content, str) or len(content) < 50:
            continue

        # Skip JSON metadata (step-start, step-finish)
        if content.strip().startswith('{'):
            continue

        # For OpenClaw: skip tool_result content (tool outputs, not model reasoning)
        if harness == "openclaw" and role == "tool":
            continue

        # For OpenCode: skip "tool" placeholder content
        if harness == "opencode" and content.strip() == "tool":
            continue

        # For ZeroClaw: system/user messages are not model reasoning
        if harness == "zeroclaw" and role in ("system", "user"):
            continue

        # For OpenClaw: also skip user messages
        if harness == "openclaw" and role == "user":
            continue

        segments.append(content)

    return segments


def process_all():
    """Run ARM classification on all Gemma GPQA trajectories."""
    all_traj = []
    all_seg = []

    for harness, result_dir in RESULT_DIRS.items():
        artifacts_dir = os.path.join(result_dir, "artifacts")
        tasks_dir = os.path.join(result_dir, "tasks")

        if not os.path.exists(artifacts_dir):
            print(f"  {harness}: artifacts MISSING")
            continue

        task_dirs = sorted([d for d in os.listdir(artifacts_dir)
                          if os.path.isdir(os.path.join(artifacts_dir, d))])
        print(f"  {harness}: {len(task_dirs)} artifact dirs")

        for task_id in task_dirs:
            art_dir = os.path.join(artifacts_dir, task_id)

            # Extract segments
            if harness == "directllm":
                segments = extract_directllm(art_dir, task_id)
            else:
                segments = extract_agent(art_dir, task_id, harness)

            if not segments:
                continue

            # Classify each segment
            seg_modes = []
            for seg in segments:
                result = classify_segment(seg)
                seg_modes.append(result["dominant"])
                all_seg.append({
                    "task_id": task_id, "harness": harness,
                    "arm_mode": result["dominant"],
                    "arm_score": max(result["scores"].values()),
                    "text_len": len(seg),
                    "text_head": seg[:200],
                    **{f"arm_score_{m}": result["scores"][m] for m in ARM_RULES},
                })

            # Per-trajectory stats
            mode_counts = Counter(seg_modes)
            n_seg = len(seg_modes)
            mode_rates = {f"rate_{m}": mode_counts.get(m, 0) / n_seg for m in ARM_RULES}
            mode_rates["rate_reason"] = mode_counts.get("reason", 0) / n_seg

            # Arm entropy
            arm_ent = 0.0
            for mode, rate in mode_rates.items():
                if rate > 0:
                    arm_ent -= rate * math.log(rate)

            # Transitions
            transitions = defaultdict(int)
            for i in range(len(seg_modes) - 1):
                transitions[f"{seg_modes[i]}->{seg_modes[i+1]}"] += 1
            # Normalize
            total_trans = sum(transitions.values()) or 1

            traj_row = {
                "task_id": task_id, "harness": harness,
                "n_segments": n_seg,
                "arm_sequence": "|".join(seg_modes),
                "dominant_mode": mode_counts.most_common(1)[0][0] if mode_counts else "reason",
                "dominant_rate": round(mode_counts.most_common(1)[0][1] / n_seg, 4) if mode_counts else 0,
                "arm_entropy": round(arm_ent, 4),
                # Key transitions
                "PD->PD": transitions.get("PD->PD", 0) / total_trans,
                "reason->PD": transitions.get("reason->PD", 0) / total_trans,
                "reason->IC": transitions.get("reason->IC", 0) / total_trans,
                "PD->reason": transitions.get("PD->reason", 0) / total_trans,
                "reason->UN": transitions.get("reason->UN", 0) / total_trans,
                "UN->reason": transitions.get("UN->reason", 0) / total_trans,
                "RR->reason": transitions.get("RR->reason", 0) / total_trans,
                "PD->IC": transitions.get("PD->IC", 0) / total_trans,
            }
            traj_row.update(mode_rates)
            all_traj.append(traj_row)

    # ═══ Merge with outcomes ═══
    df_traj = pd.DataFrame(all_traj)

    # Load outcomes from task JSONs
    for harness, result_dir in RESULT_DIRS.items():
        tasks_dir = os.path.join(result_dir, "tasks")
        if not os.path.exists(tasks_dir):
            continue
        for tf in sorted(os.listdir(tasks_dir)):
            if not tf.endswith('.json'): continue
            task_id = tf.replace('.json', '')
            try:
                with open(os.path.join(tasks_dir, tf)) as f:
                    data = json.load(f)
                records = data if isinstance(data, list) else [data]
                for rec in records:
                    if isinstance(rec, dict) and 'correct' in rec and rec['correct'] is not None:
                        mask = (df_traj['task_id'] == task_id) & (df_traj['harness'] == harness)
                        df_traj.loc[mask, 'correct'] = rec['correct']
                        if 'score' in rec and isinstance(rec['score'], (int, float)):
                            df_traj.loc[mask, 'score'] = rec['score']
            except: pass

    df_traj['outcome'] = df_traj['correct'].apply(
        lambda x: 'correct' if x == 1 or x is True else ('wrong' if x == 0 or x is False else None)
    )

    # ═══ Save ═══
    traj_path = DATA / "arm_trajectory_features_gemma.csv"
    seg_path = DATA / "arm_segment_features_gemma.csv"
    df_traj.to_csv(traj_path, index=False)
    pd.DataFrame(all_seg).to_csv(seg_path, index=False)

    # ═══ Summary ═══
    print(f"\n  -> {traj_path} ({len(df_traj)} trajectories)")
    print(f"  -> {seg_path} ({len(all_seg)} segments)")

    print(f"\n{'='*60}")
    print("GEMMA GPQA ARM SUMMARY")
    print(f"{'='*60}")

    for harness in ['directllm', 'openclaw', 'opencode', 'zeroclaw']:
        sub = df_traj[df_traj['harness'] == harness]
        c = sub[sub['outcome'] == 'correct']
        w = sub[sub['outcome'] == 'wrong']
        print(f"\n{harness}: {len(sub)} traj (C={len(c)}, W={len(w)})")
        if len(c) > 0 and len(w) > 0:
            for mode in ['PD', 'reason', 'IC', 'UN', 'RR', 'SE']:
                rc = c[f'rate_{mode}'].mean()
                rw = w[f'rate_{mode}'].mean()
                print(f"  {mode}: C={rc:.3f} W={rw:.3f} Δ={rw-rc:+.3f}")
            print(f"  arm_ent: C={c['arm_entropy'].mean():.3f} W={w['arm_entropy'].mean():.3f}")
            print(f"  n_seg: C={c['n_segments'].mean():.1f} W={w['n_segments'].mean():.1f}")
            print(f"  PD->PD: C={c['PD->PD'].mean():.3f} W={w['PD->PD'].mean():.3f}")
            print(f"  reason->IC: C={c['reason->IC'].mean():.3f} W={w['reason->IC'].mean():.3f}")

    return df_traj


if __name__ == "__main__":
    process_all()
