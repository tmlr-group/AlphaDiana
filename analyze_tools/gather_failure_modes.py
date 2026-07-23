#!/usr/bin/env python3
"""Gather portable failure-mode summaries from AlphaDiana result stores.

This is intentionally dataset-agnostic: point it at a directory containing result
stores and it discovers every descendant directory with a tasks/ folder.  It
classifies failed/unknown records into the macro taxonomy used by
compute_failure_taxonomy.py:

Reasoning, Tool Selection, Execution, Observation, Recovery, Memory/State,
Budget, Format/Verifier.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

FAILURE_MODES = [
    "Reasoning",
    "Tool Selection",
    "Execution",
    "Observation",
    "Recovery",
    "Memory/State",
    "Budget",
    "Format/Verifier",
]

ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error", "error"}
BUDGET_STATUSES = {"timeout", "timed_out", "max_tokens", "length", "context_length", "token_limit"}
NONE_STRINGS = {"", "none", "null", "nan", "n/a"}
TOOL_HARNESSES = {"openclaw", "opencode"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path, help="Directory containing result stores")
    p.add_argument("--output-dir", type=Path, default=Path("analyze_tools/data/failure_modes_alphadiana_results_copy"))
    p.add_argument("--include-checkpoint-backups", action="store_true")
    return p.parse_args()


def discover_run_dirs(root: Path, include_checkpoint_backups: bool = False) -> list[Path]:
    out: list[Path] = []
    for tasks in root.rglob("tasks"):
        if not any(tasks.glob("*.json")):
            continue
        parent = tasks.parent
        if not include_checkpoint_backups and "checkpoint_backups" in parent.parts:
            continue
        out.append(parent)
    # Avoid nested duplicate if both parent/tasks and child/tasks exist; keep all non-checkpoints.
    return sorted(set(out))


def load_records(tasks_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(tasks_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except Exception as exc:  # noqa: BLE001
            rows.append({"_task_file": path.name, "_load_error": repr(exc)})
            continue
        if isinstance(data, list):
            for i, rec in enumerate(data):
                if isinstance(rec, dict):
                    rec = dict(rec)
                    rec.setdefault("_sample_pos", i)
                    rec.setdefault("_task_file", path.name)
                    rows.append(rec)
        elif isinstance(data, dict):
            data = dict(data)
            data.setdefault("_sample_pos", data.get("sample_index", 0))
            data.setdefault("_task_file", path.name)
            rows.append(data)
    return rows


def infer_harness(run_dir: Path, rec: dict[str, Any]) -> str:
    val = str(rec.get("agent_name") or rec.get("harness") or "").lower()
    name = run_dir.name.lower()
    text = f"{val} {name}"
    for h in ("directllm", "openclaw", "opencode", "zeroclaw"):
        if h in text:
            return {"directllm": "DirectLLM", "openclaw": "OpenClaw", "opencode": "OpenCode", "zeroclaw": "ZeroClaw"}[h]
    return val or "unknown"


def infer_benchmark(run_dir: Path, rec: dict[str, Any]) -> str:
    val = str(rec.get("benchmark_name") or "").lower()
    name = run_dir.name.lower()
    text = f"{val} {name}"
    if "gpqa" in text:
        return "GPQA"
    if "aime" in text:
        return "AIME2026"
    if "hle" in text:
        return "HLE"
    if "imo" in text:
        return "IMO"
    if "mmmu" in text:
        return "MMMU-Pro"
    return val or "unknown"


def outcome(rec: dict[str, Any]) -> str:
    c = rec.get("correct")
    if c is True:
        return "success"
    if c is False:
        return "failure"
    return "unknown"


def as_text(obj: Any, max_len: int = 200000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    except Exception:
        s = str(obj)
    return s[:max_len]


def joined_model_text(rec: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("raw_output", "predicted", "rationale"):
        if rec.get(key) is not None:
            parts.append(str(rec.get(key)))
    traj = rec.get("trajectory")
    if isinstance(traj, list):
        for step in traj:
            if isinstance(step, dict) and step.get("role") in ("assistant", "tool"):
                for key in ("thinking", "content"):
                    if step.get(key):
                        parts.append(as_text(step.get(key), 5000))
    return "\n".join(parts)


def token_metrics(rec: dict[str, Any]) -> tuple[float | None, float | None]:
    stats = rec.get("token_entropy_stats") if isinstance(rec.get("token_entropy_stats"), dict) else {}
    usage = rec.get("token_usage") if isinstance(rec.get("token_usage"), dict) else {}
    resp = rec.get("response_json") if isinstance(rec.get("response_json"), dict) else {}
    resp_usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    n = stats.get("n_tokens") or usage.get("completion_tokens") or resp_usage.get("completion_tokens")
    ent = stats.get("mean")
    def num(x: Any) -> float | None:
        try:
            y = float(x)
        except Exception:
            return None
        return None if math.isnan(y) or math.isinf(y) else y
    return num(n), num(ent)


def count_tools(rec: dict[str, Any]) -> tuple[int, int, int]:
    calls = results = errors = 0
    traj = rec.get("trajectory")
    if isinstance(traj, list):
        for step in traj:
            if not isinstance(step, dict):
                continue
            role = str(step.get("role") or "").lower()
            typ = str(step.get("type") or "").lower()
            content = as_text(step.get("content") or "", 20000).lower()
            if typ in {"tool_use", "tool_call", "function_call"}:
                calls += max(1, len(step.get("tool_calls") or []) if isinstance(step.get("tool_calls"), list) else 1)
            if role == "tool" or typ == "tool_result":
                results += 1
                if any(x in content for x in ("traceback", "error", "exception", "failed", "timed out", "timeout", "not found", "permission denied")):
                    errors += 1
    # Fallback for OpenCode artifacts is not needed for classification if trajectory has no tool_use,
    # but call count remains a useful signal only from task JSONs here.
    return calls, results, errors


def has_final_answer(rec: dict[str, Any]) -> bool:
    text = str(rec.get("raw_output") or rec.get("predicted") or "")
    if re.search(r"\\boxed\s*\{[^}]+\}", text):
        return True
    if re.search(r"(?i)(final answer|the answer is|answer:)\s*\S+", text):
        return True
    pred = rec.get("predicted")
    return pred is not None and str(pred).strip().lower() not in NONE_STRINGS


def classify_failure(rec: dict[str, Any], harness: str) -> tuple[str, str]:
    if rec.get("_load_error"):
        return "Execution", "task_json_load_error"

    score_status = str(rec.get("score_status") or "").lower()
    finish_reason = str(rec.get("finish_reason") or "").lower()
    rationale = str(rec.get("rationale") or "").lower()
    pred = rec.get("predicted")
    pred_s = "" if pred is None else str(pred).strip()
    n_tokens, mean_entropy = token_metrics(rec)
    tool_calls, tool_results, tool_errors = count_tools(rec)
    text = joined_model_text(rec).lower()
    traj_len = len(rec.get("trajectory") or []) if isinstance(rec.get("trajectory"), list) else 0

    if score_status in ERROR_STATUSES or "error" in finish_reason:
        return "Execution", f"score_status_or_finish_reason={score_status or finish_reason}"
    if score_status in BUDGET_STATUSES or finish_reason in BUDGET_STATUSES or any(x in text[:20000] for x in ("timed out", "timeout", "maximum token", "context length")):
        return "Budget", f"budget_signal score_status={score_status} finish_reason={finish_reason}"
    if pred is None or pred_s.lower() in NONE_STRINGS or not has_final_answer(rec):
        return "Format/Verifier", "missing_or_nonvalid_final_answer"

    # Exact-match MC letter tasks often mark option text wrong even when semantically right.
    gt = str(rec.get("ground_truth") or "").strip()
    scorer = str(rec.get("scorer_name") or "").lower()
    if scorer == "exact_match" and re.fullmatch(r"[A-Z]", gt) and not re.fullmatch(r"[A-D]", pred_s):
        return "Format/Verifier", "multiple_choice_letter_format_mismatch"
    if "no exact match" in rationale and ("boxed" not in text[-500:] or re.fullmatch(r"[A-Z]", gt) and not re.fullmatch(r"[A-D]", pred_s)):
        return "Format/Verifier", "exact_match_or_boxed_format_failure"

    if tool_errors >= 2:
        return "Execution", f"tool_errors={tool_errors}"
    if harness.lower() in TOOL_HARNESSES and tool_calls == 0:
        return "Tool Selection", "tool_harness_no_tool_calls"

    verify_hits = len(re.findall(r"\b(check|verify|verification|recheck|validate|confirm|mistake|wrong|actually|wait)\b", text))
    recovery_hits = len(re.findall(r"\b(retry|try again|backtrack|alternative|another approach|fix|correct)\b", text))
    if (n_tokens is not None and n_tokens >= 20000) or traj_len >= 40:
        if verify_hits + recovery_hits >= 6:
            return "Recovery", f"long_recovery_churn tokens={n_tokens} trajectory_steps={traj_len}"
        return "Memory/State", f"long_repeated_or_state_churn tokens={n_tokens} trajectory_steps={traj_len}"
    if recovery_hits >= 3 or verify_hits >= 8:
        return "Recovery", f"verify/recovery_churn verify={verify_hits} recovery={recovery_hits}"
    if tool_calls > 0 and tool_results > 0:
        if verify_hits >= 2:
            return "Observation", f"tool_observation_not_integrated tool_calls={tool_calls} verify={verify_hits}"
        return "Observation", f"tool_observation_wrong tool_calls={tool_calls}"
    if mean_entropy is not None and n_tokens is not None and mean_entropy < 0.1 and n_tokens > 5000:
        return "Reasoning", f"low_entropy_long_wrong mean_entropy={mean_entropy:.4g} tokens={n_tokens:.0f}"
    return "Reasoning", "valid_answer_wrong_default"


# Collapsed 5-bucket names used by the Section 4 figure (Error-phrased).
FIVE_BUCKETS = ["Reasoning Error", "Tool Misuse", "Format Error",
                "Budget Exhaustion", "Execution/State Error"]


def classify_failure_multi(rec: dict[str, Any], harness: str) -> list[str]:
    """Multi-label variant: return EVERY failure bucket a trajectory exhibits.

    Independent predicates over the collapsed 5-bucket taxonomy, so one trajectory
    may carry several labels (e.g. it ran out of budget AND left no valid answer).
    Each failed trajectory carries exactly one "output" failure (Format Error if it
    left no valid extractable answer, else Reasoning Error for a committed wrong
    answer) plus zero or more "process" failures (Budget, Tool, Execution/State).
    """
    if rec.get("_load_error"):
        return ["Execution/State Error"]

    labels: set[str] = set()
    score_status = str(rec.get("score_status") or "").lower()
    finish_reason = str(rec.get("finish_reason") or "").lower()
    rationale = str(rec.get("rationale") or "").lower()
    pred = rec.get("predicted")
    pred_s = "" if pred is None else str(pred).strip()
    tool_calls, _tool_results, tool_errors = count_tools(rec)
    text = joined_model_text(rec).lower()
    gt = str(rec.get("ground_truth") or "").strip()
    scorer = str(rec.get("scorer_name") or "").lower()

    # --- process failures (independent, crisp signals only; can co-occur) ---
    if score_status in ERROR_STATUSES or "error" in finish_reason or tool_errors >= 2:
        labels.add("Execution/State Error")  # crash / repeated tool errors
    if (score_status in BUDGET_STATUSES or finish_reason in BUDGET_STATUSES
            or any(x in text[:20000] for x in ("timed out", "timeout", "maximum token", "context length"))):
        labels.add("Budget Exhaustion")
    if harness.lower() in TOOL_HARNESSES and tool_calls == 0:
        labels.add("Tool Misuse")  # tools available but never invoked

    # --- output failure (exactly one) ---
    no_answer = pred is None or pred_s.lower() in NONE_STRINGS or not has_final_answer(rec)
    mc_mismatch = scorer == "exact_match" and re.fullmatch(r"[A-Z]", gt) and not re.fullmatch(r"[A-D]", pred_s)
    boxed_fail = "no exact match" in rationale and ("boxed" not in text[-500:] or mc_mismatch)
    if no_answer or mc_mismatch or boxed_fail:
        labels.add("Format Error")
    else:
        labels.add("Reasoning Error")

    return [b for b in FIVE_BUCKETS if b in labels]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    args = parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    run_dirs = discover_run_dirs(args.root, args.include_checkpoint_backups)

    per_record: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    summary_counts: Counter[tuple[str, str, str, str, str]] = Counter()
    totals: Counter[tuple[str, str, str, str]] = Counter()
    multi_counts: Counter[tuple[str, str, str, str]] = Counter()
    multi_totals: Counter[tuple[str, str, str]] = Counter()

    for run_dir in run_dirs:
        records = load_records(run_dir / "tasks")
        run_id = run_dir.name
        manifest.append({"run_id": run_id, "run_path": str(run_dir), "records": len(records)})
        for rec in records:
            bench = infer_benchmark(run_dir, rec)
            harness = infer_harness(run_dir, rec)
            outc = outcome(rec)
            n_tokens, mean_entropy = token_metrics(rec)
            tool_calls, tool_results, tool_errors = count_tools(rec)
            task_id = str(rec.get("task_id") or str(rec.get("_task_file", "")).removesuffix(".json"))
            sample_index = rec.get("sample_index", rec.get("_sample_pos", 0))
            mode = reason = ""
            modes: list[str] = []
            if outc != "success":
                mode, reason = classify_failure(rec, harness)
                modes = classify_failure_multi(rec, harness)
                summary_counts[(bench, harness, run_id, outc, mode)] += 1
                totals[(bench, harness, run_id, outc)] += 1
                multi_totals[(bench, harness, run_id)] += 1
                for m in modes:
                    multi_counts[(bench, harness, run_id, m)] += 1
            per_record.append({
                "benchmark": bench,
                "harness": harness,
                "run_id": run_id,
                "task_id": task_id,
                "sample_index": sample_index,
                "outcome": outc,
                "correct": rec.get("correct"),
                "score_status": rec.get("score_status") or "",
                "predicted": rec.get("predicted"),
                "ground_truth": rec.get("ground_truth"),
                "failure_mode": mode,
                "failure_modes": ";".join(modes),
                "failure_reason": reason,
                "n_tokens": "" if n_tokens is None else f"{n_tokens:.0f}",
                "mean_entropy": "" if mean_entropy is None else f"{mean_entropy:.6f}",
                "trajectory_steps": len(rec.get("trajectory") or []) if isinstance(rec.get("trajectory"), list) else "",
                "tool_calls": tool_calls,
                "tool_results": tool_results,
                "tool_errors": tool_errors,
            })

    summary: list[dict[str, Any]] = []
    for (bench, harness, run_id, outc, mode), n in sorted(summary_counts.items()):
        total = totals[(bench, harness, run_id, outc)]
        summary.append({
            "benchmark": bench,
            "harness": harness,
            "run_id": run_id,
            "outcome": outc,
            "failure_mode": mode,
            "n": n,
            "total_failed_or_unknown": total,
            "share": f"{n / total:.6f}" if total else "",
        })

    # Multi-label summary: share = fraction of non-success trajectories exhibiting each mode
    # (independent labels, so shares within a (bench, harness, run) do NOT sum to 1).
    multi_summary: list[dict[str, Any]] = []
    for (bench, harness, run_id, mode), n in sorted(multi_counts.items()):
        total = multi_totals[(bench, harness, run_id)]
        multi_summary.append({
            "benchmark": bench, "harness": harness, "run_id": run_id, "failure_mode": mode,
            "n": n, "total_nonsuccess": total, "share": f"{n / total:.6f}" if total else "",
        })

    write_csv(out / "source_manifest.csv", manifest, ["run_id", "run_path", "records"])
    write_csv(out / "failure_modes_by_record.csv", per_record, [
        "benchmark", "harness", "run_id", "task_id", "sample_index", "outcome", "correct", "score_status",
        "predicted", "ground_truth", "failure_mode", "failure_modes", "failure_reason", "n_tokens", "mean_entropy",
        "trajectory_steps", "tool_calls", "tool_results", "tool_errors",
    ])
    write_csv(out / "failure_mode_summary.csv", summary, ["benchmark", "harness", "run_id", "outcome", "failure_mode", "n", "total_failed_or_unknown", "share"])
    write_csv(out / "failure_mode_multi_summary.csv", multi_summary, ["benchmark", "harness", "run_id", "failure_mode", "n", "total_nonsuccess", "share"])

    print(f"Discovered {len(run_dirs)} result stores")
    print(f"Wrote {out / 'failure_mode_summary.csv'}")
    print(f"Wrote {out / 'failure_mode_multi_summary.csv'}")
    print(f"Wrote {out / 'failure_modes_by_record.csv'}")


if __name__ == "__main__":
    sys.exit(main())
