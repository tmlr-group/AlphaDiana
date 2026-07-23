"""
Script 1: compute_gpqa_action_rates.py

Compute outcome-conditioned action rates for GPQA.
Rate = fraction of trajectories in (harness, outcome) bucket with >=1 event of that type.

Answer rate for agent harnesses is computed from normalized_trace.json by checking each
step's content for answer-indicating text (mirrors alphadiana action_events._looks_like_answer).
All other action rates use the pre-computed count columns in degradation_task_features.csv.
"""

import json
import os
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_CSV = os.path.join(BASE, "analyze_tools", "data", "degradation_task_features.csv")
OUT_CSV = os.path.join(BASE, "analyze_tools", "data", "gpqa_action_rates.csv")

OPENCLAW_DIR = os.path.join(BASE, "results", "full_gpqa_v2_openclaw_qwen35_27b_logprobs")
OPENCODE_DIR = os.path.join(BASE, "results", "full_gpqa_v2_opencode_qwen35_27b_logprobs")
ZEROCLAW_DIR = os.path.join(BASE, "results", "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs")

HARNESS_DIRS = {
    "openclaw": OPENCLAW_DIR,
    "opencode": OPENCODE_DIR,
    "zeroclaw": ZEROCLAW_DIR,
}

RATE_FIELDS = [
    "tool_use_count",
    "verify_count",
    "plan_count",
    "recover_count",
    "reason_count",
]

ANSWER_STEP_TYPES = {"final", "answer", "final_answer"}
ANSWER_TOKENS = ("final answer", "answer:", "the answer is", "therefore the answer")

CORRECT_OUTCOMES = {"both_correct", "rescue"}
WRONG_OUTCOMES = {"both_wrong", "regression"}


def outcome_label(paired_outcome):
    if paired_outcome in CORRECT_OUTCOMES:
        return "correct"
    if paired_outcome in WRONG_OUTCOMES:
        return "wrong"
    return None


def _step_text(step):
    content = step.get("content", "")
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict):
                parts.append(str(c.get("text", c.get("content", ""))))
            else:
                parts.append(str(c))
        return " ".join(parts)
    return str(content) if content else ""


def _looks_like_answer(step_type, text_lower):
    if step_type in ANSWER_STEP_TYPES:
        return True
    return any(tok in text_lower for tok in ANSWER_TOKENS)


def has_answer_in_trace(task_id, harness_dir):
    """Return True if any step in the normalized trace looks like an answer step."""
    path = os.path.join(harness_dir, "artifacts", task_id, "agent", "normalized_trace.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            trace = json.load(f)
        for step in trace.get("steps", []):
            if not isinstance(step, dict):
                continue
            stype = str(step.get("type", "")).strip().lower()
            text_lower = _step_text(step).lower()
            if _looks_like_answer(stype, text_lower):
                return True
    except Exception:
        pass
    return False


def build_answer_rate_map():
    """Build {(harness, task_id): has_answer} for all agent harnesses."""
    result = {}
    for harness, harness_dir in HARNESS_DIRS.items():
        if not os.path.isdir(harness_dir):
            continue
        artifacts_dir = os.path.join(harness_dir, "artifacts")
        if not os.path.isdir(artifacts_dir):
            continue
        for task_id in os.listdir(artifacts_dir):
            result[(harness, task_id)] = has_answer_in_trace(task_id, harness_dir)
    print(f"Built answer rate map: {len(result)} (harness, task) entries")
    return result


def main():
    df = pd.read_csv(FEATURES_CSV)
    print(f"Loaded features: {len(df)} rows, harnesses: {df['harness'].unique().tolist()}")

    answer_map = build_answer_rate_map()

    records = []

    # DirectLLM: degenerate baseline (hardcoded — answer=1 since predicted is always set)
    for outcome, n in [("correct", 159), ("wrong", 39)]:
        records.append({
            "harness": "directllm", "outcome": outcome, "n": n,
            "answer_rate": 1.0, "reason_rate": 1.0,
            "tool_use_rate": 0.0, "verify_rate": 0.0,
            "plan_rate": 0.0, "recover_rate": 0.0,
        })
    print("DirectLLM: hardcoded baseline (159 correct, 39 wrong)")

    # Agent harnesses
    valid = df[df["harness_score_status"] == "valid_scored"].copy()
    valid["outcome"] = valid["paired_outcome"].apply(outcome_label)
    valid = valid[valid["outcome"].notna()]

    for harness in sorted(valid["harness"].unique()):
        h_df = valid[valid["harness"] == harness]
        for outcome in ["correct", "wrong"]:
            grp = h_df[h_df["outcome"] == outcome]
            n = len(grp)
            if n == 0:
                continue
            rec = {"harness": harness, "outcome": outcome, "n": n}

            # Answer rate from normalized trace (correct definition)
            ans_hits = sum(
                1 for tid in grp["task_id"]
                if answer_map.get((harness, tid), False)
            )
            rec["answer_rate"] = ans_hits / n if n else float("nan")

            # All other rates from feature matrix count columns
            for field in RATE_FIELDS:
                if field in grp.columns:
                    rec[field.replace("_count", "_rate")] = float((grp[field] > 0).mean())
                else:
                    rec[field.replace("_count", "_rate")] = float("nan")

            records.append(rec)
            print(
                f"  {harness} / {outcome}: n={n}, answer={rec['answer_rate']:.3f}, "
                f"reason={rec['reason_rate']:.3f}, tool_use={rec['tool_use_rate']:.3f}, "
                f"verify={rec['verify_rate']:.3f}, plan={rec['plan_rate']:.3f}, "
                f"recover={rec['recover_rate']:.3f}"
            )

    out_df = pd.DataFrame(records)[
        ["harness", "outcome", "n", "answer_rate", "reason_rate",
         "tool_use_rate", "verify_rate", "plan_rate", "recover_rate"]
    ]
    out_df.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV}")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    main()
