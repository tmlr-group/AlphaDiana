"""
compute_gpqa_goal_loss.py

Compute goal-loss / self-correction spiral signals for GPQA-Diamond,
across all harnesses (OpenClaw, OpenCode, ZeroClaw) and DirectLLM.

For agent harnesses: uses degradation_task_features.csv columns.
For DirectLLM: uses the direct_* columns (one row per task, de-duplicated).

Output: analyze_tools/data/gpqa_goal_loss.csv
Columns:
  system, outcome, n,
  looping_marker_mean, self_correction_mean, uncertainty_mean,
  repeated_ngram_mean, mean_tokens, cap_frac
"""

import os
import pandas as pd
import numpy as np

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_CSV = os.path.join(BASE, "analyze_tools", "data", "degradation_task_features.csv")
OUT_CSV = os.path.join(BASE, "analyze_tools", "data", "gpqa_goal_loss.csv")

TOKEN_CAP = 32000  # threshold to count as "hitting cap"

CORRECT_OUTCOMES = {"both_correct", "rescue"}
WRONG_OUTCOMES   = {"both_wrong", "regression"}


def outcome_label(paired_outcome):
    if paired_outcome in CORRECT_OUTCOMES:
        return "correct"
    if paired_outcome in WRONG_OUTCOMES:
        return "wrong"
    return None


def agent_rows(df, harness, paired_outcomes):
    mask = (df["harness"] == harness) & (df["paired_outcome"].isin(paired_outcomes))
    return df[mask]


def summarise(grp, token_col, looping_col, selfcorr_col, uncertainty_col, ngram_col):
    n = len(grp)
    toks = grp[grp[token_col] > 0][token_col]
    return {
        "n": n,
        "looping_marker_mean":    round(grp[looping_col].mean(), 2),
        "self_correction_mean":   round(grp[selfcorr_col].mean(), 2),
        "uncertainty_mean":       round(grp[uncertainty_col].mean(), 2),
        "repeated_ngram_mean":    round(grp[ngram_col].mean(), 3),
        "mean_tokens":            int(toks.mean()) if len(toks) else 0,
        "cap_frac":               round((toks >= TOKEN_CAP).sum() / n, 3),
    }


def main():
    df = pd.read_csv(FEATURES_CSV)
    print(f"Loaded {len(df)} rows, harnesses: {df['harness'].unique().tolist()}")

    records = []

    # --- DirectLLM (from direct_* columns; de-duplicate by task_id using openclaw rows) ---
    tasks = (
        df[df["harness"] == "openclaw"]
        [["task_id", "direct_correct", "direct_n_tokens", "direct_mean_entropy"]]
        .drop_duplicates("task_id")
    )
    for correct, label in [(True, "correct"), (False, "wrong")]:
        grp = tasks[tasks["direct_correct"] == correct]
        n = len(grp)
        toks = grp[grp["direct_n_tokens"] > 0]["direct_n_tokens"]
        rec = {
            "system": "DirectLLM",
            "outcome": label,
            "n": n,
            "looping_marker_mean":  "—",
            "self_correction_mean": "—",
            "uncertainty_mean":     "—",
            "repeated_ngram_mean":  "—",
            "mean_tokens": int(toks.mean()) if len(toks) else 0,
            "cap_frac": round((toks >= 32768).sum() / n, 3),
        }
        records.append(rec)
        print(f"  DirectLLM / {label}: n={n}, mean_tokens={rec['mean_tokens']}, cap_frac={rec['cap_frac']}")

    # --- Agent harnesses ---
    for harness, display in [("openclaw", "OpenClaw"), ("opencode", "OpenCode"), ("zeroclaw", "ZeroClaw")]:
        hdf = df[df["harness"] == harness].copy()
        hdf["_outcome"] = hdf["paired_outcome"].apply(outcome_label)
        hdf = hdf[hdf["_outcome"].notna()]

        for outcome in ["correct", "wrong"]:
            # For wrong, split into regression vs both_wrong
            if outcome == "wrong":
                subsets = [
                    ("regression", hdf[hdf["paired_outcome"] == "regression"]),
                    ("both_wrong", hdf[hdf["paired_outcome"] == "both_wrong"]),
                    ("wrong (all)", hdf[hdf["_outcome"] == "wrong"]),
                ]
            else:
                subsets = [("correct (all)", hdf[hdf["_outcome"] == "correct"])]

            for sublabel, grp in subsets:
                if len(grp) == 0:
                    continue
                s = summarise(
                    grp,
                    token_col="harness_n_tokens",
                    looping_col="looping_marker_count",
                    selfcorr_col="self_correction_marker_count",
                    uncertainty_col="uncertainty_marker_count",
                    ngram_col="harness_repeated_ngram_rate",
                )
                rec = {"system": display, "outcome": sublabel, **s}
                records.append(rec)
                print(
                    f"  {display} / {sublabel}: n={s['n']}, tokens={s['mean_tokens']}, "
                    f"cap={s['cap_frac']}, looping={s['looping_marker_mean']}, "
                    f"selfcorr={s['self_correction_mean']}, ngram={s['repeated_ngram_mean']}"
                )

    out = pd.DataFrame(records)
    out.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV}")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
