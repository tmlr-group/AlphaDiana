"""
Script 2: build_gpqa_subdomain_map.py

Map GPQA task_ids to subdomains by matching question text against HF dataset.
Also produces gpqa_subdomain_passrate.csv by joining with degradation_task_features.csv.
"""

import os
import json
import csv
import difflib
import pandas as pd
from datasets import load_dataset

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS_DIR = os.path.join(
    BASE,
    "results",
    "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs",
    "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs",
    "tasks",
)
FEATURES_CSV = os.path.join(BASE, "analyze_tools", "data", "degradation_task_features.csv")
OUT_MAP = os.path.join(BASE, "analyze_tools", "data", "gpqa_subdomain_map.csv")
OUT_PASSRATE = os.path.join(BASE, "analyze_tools", "data", "gpqa_subdomain_passrate.csv")

PREFIX_LEN = 80


def load_hf():
    print("Loading HF gpqa_diamond dataset ...")
    ds = load_dataset("Idavidrein/gpqa", "gpqa_diamond")
    rows = list(ds["train"])
    print(f"  HF dataset: {len(rows)} rows")
    return rows


def load_local_tasks():
    """Return dict: task_id -> problem text (from DirectLLM task JSONs)."""
    result = {}
    for fname in os.listdir(TASKS_DIR):
        if not fname.endswith(".json"):
            continue
        task_id = fname[:-5]
        with open(os.path.join(TASKS_DIR, fname)) as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            problem = data[0].get("problem", "")
        elif isinstance(data, dict):
            problem = data.get("problem", "")
        else:
            problem = ""
        result[task_id] = problem
    print(f"  Loaded {len(result)} local task files")
    return result


def build_map(local_tasks, hf_rows):
    """Match local problem text to HF rows; return list of dicts."""
    # Build prefix index for HF rows
    hf_prefix = {}
    for row in hf_rows:
        q = row["Question"]
        prefix = q[:PREFIX_LEN].strip()
        hf_prefix[prefix] = row

    records = []
    exact = 0
    fuzzy = 0
    failed = 0

    for task_id, problem in sorted(local_tasks.items()):
        prefix = problem[:PREFIX_LEN].strip()
        if prefix in hf_prefix:
            row = hf_prefix[prefix]
            records.append(
                {
                    "task_id": task_id,
                    "subdomain": row["Subdomain"],
                    "high_level_domain": row["High-level domain"],
                    "match_method": "exact_prefix",
                }
            )
            exact += 1
        else:
            # Fuzzy fallback
            best_ratio = 0.0
            best_row = None
            for row in hf_rows:
                ratio = difflib.SequenceMatcher(
                    None, problem[:200], row["Question"][:200]
                ).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_row = row
            if best_ratio >= 0.9 and best_row is not None:
                records.append(
                    {
                        "task_id": task_id,
                        "subdomain": best_row["Subdomain"],
                        "high_level_domain": best_row["High-level domain"],
                        "match_method": f"fuzzy_{best_ratio:.3f}",
                    }
                )
                fuzzy += 1
            else:
                print(
                    f"  WARNING: no match for {task_id} (best ratio={best_ratio:.3f}): {repr(problem[:60])}"
                )
                records.append(
                    {
                        "task_id": task_id,
                        "subdomain": "unknown",
                        "high_level_domain": "unknown",
                        "match_method": f"failed_{best_ratio:.3f}",
                    }
                )
                failed += 1

    print(f"  Match results: exact={exact}, fuzzy={fuzzy}, failed={failed}")
    return records


def compute_passrate(subdomain_map, features_df):
    """Join subdomain map with feature matrix to compute per-subdomain pass rates."""
    map_df = pd.DataFrame(subdomain_map).set_index("task_id")

    harnesses = ["openclaw", "opencode", "zeroclaw"]
    # pivot features to wide: task_id -> harness_correct per harness
    rows_by_task = {}
    for _, row in features_df.iterrows():
        if row["harness_score_status"] != "valid_scored":
            continue
        tid = row["task_id"]
        h = row["harness"]
        if tid not in rows_by_task:
            rows_by_task[tid] = {"direct_correct": row["direct_correct"]}
        rows_by_task[tid][f"{h}_correct"] = row["harness_correct"]

    # build wide df
    wide = pd.DataFrame.from_dict(rows_by_task, orient="index")
    wide.index.name = "task_id"

    # join subdomain
    joined = wide.join(map_df[["subdomain"]], how="left")
    joined["subdomain"] = joined["subdomain"].fillna("unknown")

    records = []
    for subdomain, grp in joined.groupby("subdomain"):
        n = len(grp)
        rec = {"subdomain": subdomain, "n": n}
        dc = grp["direct_correct"]
        rec["directllm_rate"] = float(dc.mean()) if len(dc) > 0 else float("nan")
        for h in harnesses:
            col = f"{h}_correct"
            if col in grp.columns:
                rec[f"{h}_rate"] = float(grp[col].mean())
            else:
                rec[f"{h}_rate"] = float("nan")
        records.append(rec)

    passrate_df = pd.DataFrame(records).sort_values("directllm_rate", ascending=False)
    return passrate_df


def main():
    hf_rows = load_hf()
    print("Loading local task files ...")
    local_tasks = load_local_tasks()

    print("Building subdomain map ...")
    subdomain_map = build_map(local_tasks, hf_rows)

    # Write map CSV
    with open(OUT_MAP, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["task_id", "subdomain", "high_level_domain", "match_method"]
        )
        writer.writeheader()
        writer.writerows(subdomain_map)
    print(f"Wrote {OUT_MAP} ({len(subdomain_map)} rows)")

    print("Computing subdomain pass rates ...")
    features_df = pd.read_csv(FEATURES_CSV)
    passrate_df = compute_passrate(subdomain_map, features_df)
    passrate_df.to_csv(OUT_PASSRATE, index=False)
    print(f"Wrote {OUT_PASSRATE} ({len(passrate_df)} subdomains)")
    print("\nSubdomain pass rates:")
    print(passrate_df.to_string(index=False))


if __name__ == "__main__":
    main()
