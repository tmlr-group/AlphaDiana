"""
Script 4: compute_gpqa_oracle.py

Compute oracle harness router ceiling: what accuracy would a perfect per-task
harness selector achieve?
"""

import os
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_CSV = os.path.join(BASE, "analyze_tools", "data", "degradation_task_features.csv")
OUT_CSV = os.path.join(BASE, "analyze_tools", "data", "gpqa_oracle_ceiling.csv")

TOTAL_TASKS = 198
DIRECT_CORRECT = 159
DIRECT_WRONG = 39


def main():
    df = pd.read_csv(FEATURES_CSV)
    print(f"Loaded {len(df)} rows from feature matrix")

    # Keep only valid_scored rows
    valid = df[df["harness_score_status"] == "valid_scored"].copy()
    harnesses = sorted(valid["harness"].unique().tolist())
    print(f"Harnesses: {harnesses}")

    # Build per-task correctness dict
    # task_id -> { harness -> correct }
    task_correct = {}
    for _, row in valid.iterrows():
        tid = row["task_id"]
        h = row["harness"]
        correct = bool(row["harness_correct"])
        direct = bool(row["direct_correct"])
        if tid not in task_correct:
            task_correct[tid] = {"direct": direct}
        task_correct[tid][h] = correct

    all_task_ids = list(task_correct.keys())
    print(f"Total unique task_ids in feature matrix: {len(all_task_ids)}")

    # Per-harness accuracy
    print("\nPer-harness accuracy:")
    for h in harnesses:
        n_valid = sum(1 for t in task_correct.values() if h in t)
        n_correct = sum(1 for t in task_correct.values() if t.get(h, False))
        print(f"  {h}: {n_correct}/{n_valid} = {n_correct/n_valid:.4f}")

    direct_correct_tasks = {t for t, v in task_correct.items() if v.get("direct", False)}
    direct_wrong_tasks = {t for t, v in task_correct.items() if not v.get("direct", True)}
    print(f"\nDirectLLM: {len(direct_correct_tasks)} correct, {len(direct_wrong_tasks)} wrong")
    print(f"(Total tasks in feature matrix: {len(all_task_ids)}; canonical total: {TOTAL_TASKS})")

    # Oracle: correct if DirectLLM OR any harness correct
    oracle_correct = 0
    oracle_task_ids = []
    for tid, v in task_correct.items():
        any_correct = v.get("direct", False) or any(v.get(h, False) for h in harnesses)
        if any_correct:
            oracle_correct += 1
            oracle_task_ids.append(tid)
    oracle_rate = oracle_correct / len(all_task_ids)
    print(f"\nOracle ceiling: {oracle_correct}/{len(all_task_ids)} = {oracle_rate:.4f}")

    # How many tasks correct under ALL harnesses
    for k in range(0, len(harnesses) + 1):
        n_k = sum(
            1
            for v in task_correct.values()
            if sum(1 for h in harnesses if v.get(h, False)) == k
        )
        print(f"  Tasks correct under exactly {k} harnesses: {n_k}")

    # Rescue breakdown: tasks DirectLLM WRONG that each harness recovers
    print(f"\nRescue breakdown (from {len(direct_wrong_tasks)} DirectLLM-wrong tasks):")
    rescue_by_harness = {}
    for h in harnesses:
        rescued = sum(
            1 for tid in direct_wrong_tasks
            if task_correct.get(tid, {}).get(h, False)
        )
        rescue_by_harness[h] = rescued
        print(f"  {h}: rescues {rescued}/{len(direct_wrong_tasks)} = {rescued/len(direct_wrong_tasks):.4f}")

    # Tasks rescued by any harness
    any_rescued = sum(
        1 for tid in direct_wrong_tasks
        if any(task_correct.get(tid, {}).get(h, False) for h in harnesses)
    )
    print(f"  any_harness: rescues {any_rescued}/{len(direct_wrong_tasks)} = {any_rescued/len(direct_wrong_tasks):.4f}")

    # Regression: tasks DirectLLM RIGHT but harness gets WRONG
    print(f"\nRegression (DirectLLM correct but harness wrong):")
    for h in harnesses:
        regressed = sum(
            1 for tid in direct_correct_tasks
            if not task_correct.get(tid, {}).get(h, True)
        )
        print(f"  {h}: {regressed}/{len(direct_correct_tasks)} regress = {regressed/len(direct_correct_tasks):.4f}")

    # Summary records
    records = []
    # Per-harness
    for h in harnesses:
        n_valid = sum(1 for t in task_correct.values() if h in t)
        n_correct = sum(1 for t in task_correct.values() if t.get(h, False))
        records.append({
            "metric": f"{h}_accuracy",
            "value": n_correct / n_valid if n_valid else float("nan"),
            "numerator": n_correct,
            "denominator": n_valid,
        })
        records.append({
            "metric": f"{h}_rescue_from_direct_wrong",
            "value": rescue_by_harness[h] / len(direct_wrong_tasks) if direct_wrong_tasks else float("nan"),
            "numerator": rescue_by_harness[h],
            "denominator": len(direct_wrong_tasks),
        })
    records.append({
        "metric": "directllm_accuracy",
        "value": len(direct_correct_tasks) / len(all_task_ids),
        "numerator": len(direct_correct_tasks),
        "denominator": len(all_task_ids),
    })
    records.append({
        "metric": "oracle_ceiling",
        "value": oracle_rate,
        "numerator": oracle_correct,
        "denominator": len(all_task_ids),
    })
    records.append({
        "metric": "any_harness_rescue_from_direct_wrong",
        "value": any_rescued / len(direct_wrong_tasks) if direct_wrong_tasks else float("nan"),
        "numerator": any_rescued,
        "denominator": len(direct_wrong_tasks),
    })

    # n-harnesses-correct distribution
    for k in range(0, len(harnesses) + 1):
        n_k = sum(
            1 for v in task_correct.values()
            if sum(1 for h in harnesses if v.get(h, False)) == k
        )
        records.append({
            "metric": f"tasks_correct_under_exactly_{k}_harnesses",
            "value": n_k / len(all_task_ids),
            "numerator": n_k,
            "denominator": len(all_task_ids),
        })

    out_df = pd.DataFrame(records)
    out_df.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV}")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    main()
