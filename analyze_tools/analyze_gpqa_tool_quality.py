"""
Script 3: analyze_gpqa_tool_quality.py

For OpenCode and OpenClaw trajectories, analyze whether tool calls produced
substantive results. Reports tool quality by (harness, outcome).
"""

import os
import json
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEATURES_CSV = os.path.join(BASE, "analyze_tools", "data", "degradation_task_features.csv")
OUT_CSV = os.path.join(BASE, "analyze_tools", "data", "gpqa_tool_quality.csv")

HARNESS_DIRS = {
    "openclaw": os.path.join(BASE, "results", "full_gpqa_v2_openclaw_qwen35_27b_logprobs"),
    # OpenCode traces use a different encoding (type='tool_use' marker steps) that
    # doesn't include tool result content; we fall back to feature matrix counts.
    # "opencode": os.path.join(BASE, "results", "full_gpqa_v2_opencode_qwen35_27b_logprobs"),
}

CORRECT_OUTCOMES = {"both_correct", "rescue"}
WRONG_OUTCOMES = {"both_wrong", "regression"}


def outcome_label(paired_outcome):
    if paired_outcome in CORRECT_OUTCOMES:
        return "correct"
    if paired_outcome in WRONG_OUTCOMES:
        return "wrong"
    return None


def analyze_trace(trace_path):
    """
    Parse normalized_trace.json and extract per-tool-call quality stats.
    Returns: list of dicts with tool_name, result_length, is_empty, is_error, is_timeout
    """
    try:
        with open(trace_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return []

    steps = []
    if isinstance(data, dict):
        steps = data.get("steps", [])
    elif isinstance(data, list):
        # older format: flat list of steps
        steps = data

    tool_calls_info = []

    # normalized_trace structure:
    # - assistant step with role='assistant', may have 'tool_calls': list of {id, tool, input}
    # - tool step with role='tool', has 'tool_results': list of {tool_use_id, content}
    # We match them by position (tool result follows tool call step)
    pending_calls = []  # list of tool names waiting for results

    for step in steps:
        role = step.get("role", "")

        if role == "assistant" and "tool_calls" in step:
            tc_list = step["tool_calls"]
            if isinstance(tc_list, list):
                for tc in tc_list:
                    tool_name = tc.get("tool", tc.get("name", "unknown"))
                    pending_calls.append(tool_name)

        elif role == "tool":
            results = step.get("tool_results", [])
            if not isinstance(results, list):
                results = [{"content": step.get("content", "")}]

            # If no structured tool_results, use content directly
            if not results and "content" in step:
                results = [{"content": step.get("content", "")}]

            for i, tr in enumerate(results):
                content = tr.get("content", "")
                if not isinstance(content, str):
                    content = str(content)
                result_length = len(content)
                is_empty = result_length < 20
                is_error = (
                    result_length == 0
                    or "error" in content.lower()[:200]
                    or "traceback" in content.lower()[:200]
                )
                is_timeout = "timeout" in content.lower() or "timed out" in content.lower()
                is_substantive = result_length > 100 and not is_error

                # match to tool name
                tool_name = pending_calls.pop(0) if pending_calls else "unknown"

                tool_calls_info.append({
                    "tool_name": tool_name,
                    "result_length": result_length,
                    "is_empty": is_empty,
                    "is_error": is_error,
                    "is_timeout": is_timeout,
                    "is_substantive": is_substantive,
                })

    return tool_calls_info


def process_harness(harness, harness_dir, features_sub):
    """Process all tasks for a harness; return per-task summary rows."""
    artifacts_dir = os.path.join(harness_dir, "artifacts")
    if not os.path.isdir(artifacts_dir):
        print(f"  WARNING: artifacts dir not found: {artifacts_dir}")
        return []

    # Build outcome lookup from features
    outcome_map = {}
    for _, row in features_sub.iterrows():
        if row["harness_score_status"] == "valid_scored":
            label = outcome_label(row["paired_outcome"])
            if label:
                outcome_map[row["task_id"]] = label

    task_rows = []
    n_missing = 0
    for task_id in sorted(os.listdir(artifacts_dir)):
        trace_path = os.path.join(artifacts_dir, task_id, "agent", "normalized_trace.json")
        if not os.path.exists(trace_path):
            n_missing += 1
            continue

        tool_calls = analyze_trace(trace_path)
        any_tool = len(tool_calls) > 0
        any_substantive = any(tc["is_substantive"] for tc in tool_calls)
        any_empty_or_error = any(tc["is_empty"] or tc["is_error"] for tc in tool_calls)
        n_total = len(tool_calls)
        n_substantive = sum(1 for tc in tool_calls if tc["is_substantive"])

        outcome = outcome_map.get(task_id, None)

        task_rows.append({
            "task_id": task_id,
            "harness": harness,
            "outcome": outcome,
            "any_tool_call": any_tool,
            "any_substantive_tool_call": any_substantive,
            "any_empty_or_error": any_empty_or_error,
            "n_tool_calls": n_total,
            "n_substantive": n_substantive,
        })

    print(f"  {harness}: processed {len(task_rows)} tasks, {n_missing} missing traces")
    return task_rows


CORRECT_OUTCOMES_SET = {"both_correct", "rescue"}
WRONG_OUTCOMES_SET   = {"both_wrong",  "regression"}


def process_harness_from_features(harness, features_sub):
    """
    Fallback: derive tool quality metrics from the pre-computed feature matrix.
    This is used for OpenCode because its normalized_trace uses an opaque encoding
    (type='tool_use' marker steps with no result content).
    We cannot compute is_substantive / is_error from trace; approximate:
      - any_tool_call = tool_use_count > 0
      - any_substantive = tool_use_count > 0 AND tool_error_or_fail_count < tool_use_count
      - any_empty_or_error = tool_error_or_fail_count > 0
    """
    valid = features_sub[features_sub["harness_score_status"] == "valid_scored"].copy()
    valid["outcome"] = valid["paired_outcome"].apply(outcome_label)
    valid = valid[valid["outcome"].notna()]

    rows = []
    for _, r in valid.iterrows():
        tu = int(r.get("tool_use_count", 0))
        te = int(r.get("tool_error_or_fail_count", 0))
        any_tool = tu > 0
        any_substantive = tu > 0 and te < tu
        any_error = te > 0
        rows.append({
            "task_id": r["task_id"],
            "harness": harness,
            "outcome": r["outcome"],
            "any_tool_call": any_tool,
            "any_substantive_tool_call": any_substantive,
            "any_empty_or_error": any_error,
            "n_tool_calls": tu,
            "n_substantive": max(0, tu - te),
        })
    print(f"  {harness} (from features): {len(rows)} tasks")
    return rows


def main():
    features_df = pd.read_csv(FEATURES_CSV)
    print(f"Loaded {len(features_df)} feature rows")

    all_task_rows = []

    # OpenClaw: parse normalized_trace for richer tool result analysis
    for harness, harness_dir in HARNESS_DIRS.items():
        print(f"\nProcessing {harness} (trace parsing) ...")
        h_features = features_df[features_df["harness"] == harness]
        rows = process_harness(harness, harness_dir, h_features)
        all_task_rows.extend(rows)

    # OpenCode: fallback to feature matrix (trace uses opaque tool_use markers)
    print("\nProcessing opencode (feature matrix fallback) ...")
    oc_features = features_df[features_df["harness"] == "opencode"]
    all_task_rows.extend(process_harness_from_features("opencode", oc_features))

    task_df = pd.DataFrame(all_task_rows)

    records = []
    for harness in ["openclaw", "opencode"]:
        h_df = task_df[task_df["harness"] == harness]
        for outcome in ["correct", "wrong"]:
            grp = h_df[h_df["outcome"] == outcome]
            n = len(grp)
            if n == 0:
                continue
            rec = {
                "harness": harness,
                "outcome": outcome,
                "n": n,
                "tool_use_rate_any": float(grp["any_tool_call"].mean()),
                "tool_use_rate_substantive": float(grp["any_substantive_tool_call"].mean()),
                "tool_use_rate_empty_or_error": float(grp["any_empty_or_error"].mean()),
                "mean_tool_calls_per_traj": float(grp["n_tool_calls"].mean()),
                "mean_substantive_per_traj": float(grp["n_substantive"].mean()),
            }
            records.append(rec)
            print(
                f"  {harness}/{outcome}: n={n}, any={rec['tool_use_rate_any']:.3f}, "
                f"substantive={rec['tool_use_rate_substantive']:.3f}, "
                f"err={rec['tool_use_rate_empty_or_error']:.3f}, "
                f"mean_calls={rec['mean_tool_calls_per_traj']:.2f}"
            )

    out_df = pd.DataFrame(records)[
        ["harness", "outcome", "n", "tool_use_rate_any", "tool_use_rate_substantive",
         "tool_use_rate_empty_or_error", "mean_tool_calls_per_traj", "mean_substantive_per_traj"]
    ]
    out_df.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV}")
    print(out_df.to_string(index=False))


if __name__ == "__main__":
    main()
