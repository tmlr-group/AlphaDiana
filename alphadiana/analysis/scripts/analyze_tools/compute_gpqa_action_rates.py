"""
Script 1: compute_gpqa_action_rates.py

Compute outcome-conditioned action rates for GPQA.
Rate = fraction of trajectories in (harness, outcome) bucket with >=1 event of that type.
"""

import os
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_CSV = os.path.join(BASE, "analyze_tools", "data", "degradation_task_features.csv")
OUT_CSV = os.path.join(BASE, "analyze_tools", "data", "gpqa_action_rates.csv")

RATE_FIELDS = [
    "tool_use_count",
    "verify_count",
    "plan_count",
    "recover_count",
    "reason_count",
    "answer_count",
]

# Map paired_outcome -> harness outcome (correct/wrong)
CORRECT_OUTCOMES = {"both_correct", "rescue"}
WRONG_OUTCOMES = {"both_wrong", "regression"}


def outcome_label(paired_outcome):
    if paired_outcome in CORRECT_OUTCOMES:
        return "correct"
    if paired_outcome in WRONG_OUTCOMES:
        return "wrong"
    return None


def main():
    df = pd.read_csv(FEATURES_CSV)
    print(f"Loaded features: {len(df)} rows, harnesses: {df['harness'].unique().tolist()}")

    records = []

    # --- DirectLLM: degenerate baseline (hardcoded) ---
    # All DirectLLM trajectories: reason=1, answer=1, all others=0
    # Correct: 159/198, Wrong: 39/198
    for outcome, n in [("correct", 159), ("wrong", 39)]:
        records.append(
            {
                "harness": "directllm",
                "outcome": outcome,
                "n": n,
                "answer_rate": 1.0,
                "reason_rate": 1.0,
                "tool_use_rate": 0.0,
                "verify_rate": 0.0,
                "plan_rate": 0.0,
                "recover_rate": 0.0,
            }
        )
    print("DirectLLM: hardcoded baseline (159 correct, 39 wrong)")

    # --- Agent harnesses ---
    # Filter to valid_scored only
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
            for field in RATE_FIELDS:
                col = field
                if col in grp.columns:
                    rate = (grp[col] > 0).mean()
                else:
                    rate = float("nan")
                # map field name to output column
                key = field.replace("_count", "_rate")
                rec[key] = float(rate)
            records.append(rec)
            print(
                f"  {harness} / {outcome}: n={n}, "
                + ", ".join(
                    f"{f.replace('_count','_rate')}={rec[f.replace('_count','_rate')]:.3f}"
                    for f in RATE_FIELDS
                )
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
