#!/usr/bin/env python3
"""Compute six-action trajectory frequencies for benchmark result stores.

The classifier is deterministic and uses the compact action space documented in
``analyze_tools/action_space_validation_plan.md``:

  Problem Framing, Plan Formation, Solution Execution, Tool Grounding,
  Result Auditing, Answer Delivery.

The script writes raw action events first, then derives all frequency tables from
those event rows so counts can be audited.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = ROOT / "analyze_tools" / "data" / "six_action_analysis"

ACTIONS = (
    "Problem Framing",
    "Plan Formation",
    "Solution Execution",
    "Tool Grounding",
    "Result Auditing",
    "Answer Delivery",
)

META_JSON_PART_RE = re.compile(r'^\s*\{"part"\s*:')
BOXED_RE = re.compile(r"\\boxed\s*\{[^}]+\}")
FINAL_RE = re.compile(
    r"\b(final answer|the answer is|answer is|correct answer|therefore[,:\s]+(?:the )?answer)\b",
    re.IGNORECASE,
)
PROBLEM_FRAMING_RE = re.compile(
    r"\b(question asks|we need to|i need to|need to determine|given|answer choices|options are|goal is)\b",
    re.IGNORECASE,
)
PLAN_RE = re.compile(
    r"\b(let me (?:break|outline|set up|define|plan|approach|solve)|step by step|first[,:\s]|second[,:\s]|case \d|strategy|approach)\b",
    re.IGNORECASE,
)
EXECUTION_RE = re.compile(
    r"(\d+\s*[\+\-\*/=^]\s*\d+|\\frac|\\sum|\\int|=|"
    r"\b(because|since|therefore|thus|hence|implies|so |using|formula|equation|"
    r"calculate|compute|derive|compare|option [A-Z]|choice [A-Z]|eliminate|rule out|"
    r"mechanism|reaction|density|probability|speed|distance|mass|radius)\b)",
    re.IGNORECASE,
)
AUDIT_RE = re.compile(
    r"\b(check|verify|confirm|double[- ]check|sanity|consistent|inconsistent|"
    r"wait|actually|reconsider|wrong|mistake|error|failed|invalid|does not match|"
    r"mapping|units|format|passes|satisfies)\b",
    re.IGNORECASE,
)
OBSERVATION_INTEGRATION_RE = re.compile(
    r"\b(result|output|observation|search|tool|calculation|python|bash|returned|"
    r"confirms|shows|according to|from the tool|from the search)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RunSpec:
    benchmark: str
    harness: str
    source_id: str
    root: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spec",
        action="append",
        default=[],
        metavar="BENCHMARK:HARNESS:SOURCE_ID:PATH",
        help="Result-store spec. May be repeated.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser


def parse_spec(value: str) -> RunSpec:
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise SystemExit(f"--spec must be BENCHMARK:HARNESS:SOURCE_ID:PATH, got {value!r}")
    benchmark, harness, source_id, path = parts
    return RunSpec(benchmark=benchmark, harness=harness, source_id=source_id, root=Path(path))


def default_specs() -> list[RunSpec]:
    return [
        RunSpec(
            "GPQA",
            "DirectLLM",
            "gpqa_directllm_phase9",
            ROOT
            / "results"
            / "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs"
            / "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs",
        ),
        RunSpec(
            "GPQA",
            "OpenClaw",
            "gpqa_openclaw_v2",
            ROOT / "results" / "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
        ),
        RunSpec(
            "GPQA",
            "OpenCode",
            "gpqa_opencode_v2",
            ROOT / "results" / "full_gpqa_v2_opencode_qwen35_27b_logprobs",
        ),
        RunSpec(
            "GPQA",
            "ZeroClaw",
            "gpqa_zeroclaw_v2",
            ROOT / "results" / "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
        ),
        RunSpec(
            "HLE",
            "OpenClaw",
            "hle_openclaw_merged",
            ROOT / "results" / "quick_260430_hle_openclaw_qwen35_27b_merged",
        ),
    ]


def load_task_records(tasks_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not tasks_dir.exists():
        return records
    for path in sorted(tasks_dir.glob("*.json")):
        with path.open() as f:
            raw = json.load(f)
        items = raw if isinstance(raw, list) else [raw]
        for sample_pos, rec in enumerate(items):
            if not isinstance(rec, dict):
                continue
            rec = dict(rec)
            rec["_task_file"] = path.name
            rec["_sample_pos"] = sample_pos
            records.append(rec)
    return records


def record_success(rec: dict[str, Any]) -> str:
    correct = rec.get("correct")
    if correct is True:
        return "success"
    if correct is False:
        return "failure"
    return "unknown"


def is_meta_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped == "tool":
        return True
    if META_JSON_PART_RE.match(stripped):
        return True
    return False


def normalize_text(text: str) -> str:
    text = re.sub(r"<think>|</think>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_snippet(text: str) -> str:
    """Remove local absolute paths from review snippets."""
    text = re.sub(r"/(?:data0|data1|data2|data3|home|tmp|var|mnt)/[^\s,\"')\]]+", "<ABS_PATH>", text)
    return text


def split_text_segments(text: str) -> list[str]:
    text = text.replace("\r\n", "\n")
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n+", text) if chunk.strip()]
    if not chunks:
        chunks = [line.strip() for line in text.splitlines() if line.strip()]
    segments: list[str] = []
    for chunk in chunks:
        if len(chunk) <= 1400:
            segments.append(chunk)
            continue
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if len(lines) > 1:
            segments.extend(lines)
        else:
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", chunk)
            buf = ""
            for sent in sentences:
                if len(buf) + len(sent) > 1000 and buf:
                    segments.append(buf.strip())
                    buf = sent
                else:
                    buf = f"{buf} {sent}".strip()
            if buf:
                segments.append(buf.strip())
    return segments


def iter_model_segments(trajectory: Any) -> list[dict[str, Any]]:
    if not isinstance(trajectory, list):
        return []
    rows: list[dict[str, Any]] = []
    pending_observation = False
    for step_idx, step in enumerate(trajectory):
        if not isinstance(step, dict):
            continue
        role = str(step.get("role") or "")
        stype = str(step.get("type") or "")
        content = step.get("content")

        if role == "tool" or stype == "tool_result":
            pending_observation = True
            continue
        if role in {"system", "user"} or stype == "system":
            continue
        if role != "assistant" and stype not in {"tool_use", "tool_call", "function_call"}:
            continue

        if stype in {"tool_use", "tool_call", "function_call"}:
            text = normalize_text(content if isinstance(content, str) else "")
            rows.append(
                {
                    "step_id": step_idx,
                    "segment_index": 0,
                    "step_type": stype,
                    "text": text or stype,
                    "after_observation": pending_observation,
                    "is_tool_event": True,
                }
            )
            pending_observation = False
            continue

        if not isinstance(content, str) or is_meta_text(content):
            continue

        segments = split_text_segments(content)
        emitted = False
        for seg_idx, segment in enumerate(segments):
            text = normalize_text(segment)
            if not text or is_meta_text(text):
                continue
            rows.append(
                {
                    "step_id": step_idx,
                    "segment_index": seg_idx,
                    "step_type": stype or "message",
                    "text": text,
                    "after_observation": pending_observation and not emitted,
                    "is_tool_event": False,
                }
            )
            emitted = True
        if emitted:
            pending_observation = False
    return rows


def classify_segment(segment: dict[str, Any], *, is_last_model_segment: bool) -> tuple[str, str, bool]:
    text = segment["text"]
    lower = text.lower()

    has_answer = bool(BOXED_RE.search(text) or FINAL_RE.search(text))
    has_audit = bool(AUDIT_RE.search(text))
    has_execution = bool(EXECUTION_RE.search(text))
    has_plan = bool(PLAN_RE.search(text))
    has_frame = bool(PROBLEM_FRAMING_RE.search(text))
    integrates_observation = bool(segment["after_observation"] and OBSERVATION_INTEGRATION_RE.search(text))

    if has_answer and (is_last_model_segment or BOXED_RE.search(text) or "final answer" in lower):
        return "Answer Delivery", "boxed_or_terminal_answer", False
    if segment["is_tool_event"] or integrates_observation:
        return "Tool Grounding", "tool_event_or_observation_integration", False
    if has_audit and not has_execution:
        return "Result Auditing", "audit_without_execution", False
    if has_audit and re.search(r"\b(wait|actually|reconsider|wrong|mistake|error|failed|invalid|does not match)\b", text, re.IGNORECASE):
        return "Result Auditing", "diagnosis_or_correction", False
    if has_execution:
        return "Solution Execution", "reasoning_calculation_or_comparison", False
    if has_plan:
        return "Plan Formation", "decomposition_or_strategy", False
    if has_frame:
        return "Problem Framing", "task_goal_or_constraints", False

    if len(text.split()) <= 5:
        return "Plan Formation", "short_low_context_default", True
    return "Solution Execution", "substantive_default", True


def event_rows_for_record(spec: RunSpec, rec: dict[str, Any]) -> list[dict[str, Any]]:
    segments = iter_model_segments(rec.get("trajectory"))
    rows: list[dict[str, Any]] = []
    task_id = str(rec.get("task_id") or rec.get("_task_file", "").removesuffix(".json"))
    sample_index = rec.get("sample_index")
    if sample_index is None:
        sample_index = rec.get("_sample_pos", 0)
    outcome = record_success(rec)
    for idx, segment in enumerate(segments):
        action, rule, low_conf = classify_segment(segment, is_last_model_segment=idx == len(segments) - 1)
        rows.append(
            {
                "benchmark": spec.benchmark,
                "harness": spec.harness,
                "source_id": spec.source_id,
                "task_id": task_id,
                "sample_index": sample_index,
                "outcome": outcome,
                "correct": rec.get("correct"),
                "score_status": rec.get("score_status") or "",
                "predicted": "" if rec.get("predicted") is None else str(rec.get("predicted")),
                "ground_truth": "" if rec.get("ground_truth") is None else str(rec.get("ground_truth")),
                "event_index": idx,
                "step_id": segment["step_id"],
                "segment_index": segment["segment_index"],
                "action": action,
                "classification_rule": rule,
                "low_confidence": low_conf,
                "after_observation": segment["after_observation"],
                "is_tool_event": segment["is_tool_event"],
                "text_head": sanitize_snippet(segment["text"][:260]),
            }
        )
    return rows


def pct(num: float, den: float) -> float:
    if den == 0:
        return 0.0
    return 100.0 * num / den


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compute_tables(event_rows: list[dict[str, Any]], ledger_rows: list[dict[str, Any]], output_dir: Path) -> None:
    event_fields = [
        "benchmark",
        "harness",
        "source_id",
        "task_id",
        "sample_index",
        "outcome",
        "correct",
        "score_status",
        "predicted",
        "ground_truth",
        "event_index",
        "step_id",
        "segment_index",
        "action",
        "classification_rule",
        "low_confidence",
        "after_observation",
        "is_tool_event",
        "text_head",
    ]
    write_csv(output_dir / "six_action_events.csv", event_rows, event_fields)
    write_csv(
        output_dir / "six_action_denominator_ledger.csv",
        ledger_rows,
        [
            "benchmark",
            "harness",
            "source_id",
            "task_files",
            "trajectory_records",
            "success_records",
            "failure_records",
            "unknown_records",
            "records_with_events",
            "action_events",
            "low_confidence_events",
        ],
    )

    by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        by_group[(row["benchmark"], row["harness"], row["outcome"])].append(row)

    freq_rows: list[dict[str, Any]] = []
    for key, rows in sorted(by_group.items()):
        benchmark, harness, outcome = key
        total = len(rows)
        counts = Counter(row["action"] for row in rows)
        low = sum(1 for row in rows if str(row["low_confidence"]) == "True")
        for action in ACTIONS:
            count = counts.get(action, 0)
            freq_rows.append(
                {
                    "benchmark": benchmark,
                    "harness": harness,
                    "outcome": outcome,
                    "action": action,
                    "event_count": count,
                    "event_total": total,
                    "event_pct": f"{pct(count, total):.2f}",
                    "low_confidence_event_total": low,
                }
            )
    write_csv(
        output_dir / "six_action_frequency_by_outcome.csv",
        freq_rows,
        [
            "benchmark",
            "harness",
            "outcome",
            "action",
            "event_count",
            "event_total",
            "event_pct",
            "low_confidence_event_total",
        ],
    )

    trajectory_keys = sorted(
        {
            (row["benchmark"], row["harness"], row["source_id"], row["task_id"], row["sample_index"], row["outcome"])
            for row in event_rows
        }
    )
    events_by_traj: dict[tuple[str, str, str, str, Any, str], list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        events_by_traj[
            (row["benchmark"], row["harness"], row["source_id"], row["task_id"], row["sample_index"], row["outcome"])
        ].append(row)

    rate_rows: list[dict[str, Any]] = []
    grouped_trajs: dict[tuple[str, str, str], list[tuple[str, str, str, str, Any, str]]] = defaultdict(list)
    for key in trajectory_keys:
        grouped_trajs[(key[0], key[1], key[5])].append(key)

    for (benchmark, harness, outcome), trajs in sorted(grouped_trajs.items()):
        n = len(trajs)
        for action in ACTIONS:
            hits = sum(1 for traj in trajs if any(row["action"] == action for row in events_by_traj[traj]))
            mean_count = sum(
                sum(1 for row in events_by_traj[traj] if row["action"] == action) for traj in trajs
            ) / n if n else 0.0
            rate_rows.append(
                {
                    "benchmark": benchmark,
                    "harness": harness,
                    "outcome": outcome,
                    "action": action,
                    "trajectory_count": n,
                    "trajectories_with_action": hits,
                    "trajectory_rate_pct": f"{pct(hits, n):.2f}",
                    "mean_events_per_trajectory": f"{mean_count:.3f}",
                }
            )
    write_csv(
        output_dir / "six_action_trajectory_rates.csv",
        rate_rows,
        [
            "benchmark",
            "harness",
            "outcome",
            "action",
            "trajectory_count",
            "trajectories_with_action",
            "trajectory_rate_pct",
            "mean_events_per_trajectory",
        ],
    )

    failure_rows: list[dict[str, Any]] = []
    for (benchmark, harness, outcome), trajs in sorted(grouped_trajs.items()):
        if outcome != "failure":
            continue
        action_totals = Counter()
        last_actions = Counter()
        for traj in trajs:
            rows = events_by_traj[traj]
            action_totals.update(row["action"] for row in rows)
            if rows:
                last_actions[rows[-1]["action"]] += 1
        total_events = sum(action_totals.values())
        failure_rows.append(
            {
                "benchmark": benchmark,
                "harness": harness,
                "failure_trajectories": len(trajs),
                "failure_events": total_events,
                "top_action": action_totals.most_common(1)[0][0] if action_totals else "",
                "top_action_pct": f"{pct(action_totals.most_common(1)[0][1], total_events):.2f}" if total_events else "0.00",
                "terminal_answer_delivery_rate_pct": f"{pct(last_actions.get('Answer Delivery', 0), len(trajs)):.2f}",
                "terminal_result_auditing_rate_pct": f"{pct(last_actions.get('Result Auditing', 0), len(trajs)):.2f}",
                "tool_grounding_event_pct": f"{pct(action_totals.get('Tool Grounding', 0), total_events):.2f}",
                "result_auditing_event_pct": f"{pct(action_totals.get('Result Auditing', 0), total_events):.2f}",
            }
        )
    write_csv(
        output_dir / "six_action_failure_summary.csv",
        failure_rows,
        [
            "benchmark",
            "harness",
            "failure_trajectories",
            "failure_events",
            "top_action",
            "top_action_pct",
            "terminal_answer_delivery_rate_pct",
            "terminal_result_auditing_rate_pct",
            "tool_grounding_event_pct",
            "result_auditing_event_pct",
        ],
    )


def write_report(output_dir: Path, ledger_rows: list[dict[str, Any]]) -> None:
    freq_path = output_dir / "six_action_frequency_by_outcome.csv"
    rate_path = output_dir / "six_action_trajectory_rates.csv"
    failure_path = output_dir / "six_action_failure_summary.csv"

    freq_rows = list(csv.DictReader(freq_path.open()))
    failure_rows = list(csv.DictReader(failure_path.open()))

    lines = [
        "# Six-Action Frequency Report",
        "",
        "## Action Extraction Logic",
        "",
        "The classifier uses exactly six action labels: Problem Framing, Plan Formation, Solution Execution, Tool Grounding, Result Auditing, and Answer Delivery.",
        "",
        "The primary unit is a model-generated action span. System, user, and observation-only tool-result rows are excluded. Assistant text is split by paragraph or long-line boundaries so long DirectLLM outputs can contain multiple actions. Tool-call events are retained as model actions.",
        "",
        "Priority order is Answer Delivery, Tool Grounding, Result Auditing, Solution Execution, Plan Formation, then Problem Framing. If no precise rule fires, substantive assistant text defaults to Solution Execution with `low_confidence=true`; very short low-context text defaults to Plan Formation with `low_confidence=true`.",
        "",
        "## Number Calculation Logic",
        "",
        "- `six_action_events.csv` is the auditable event table. Each row is one extracted action span.",
        "- `six_action_frequency_by_outcome.csv` counts event rows by benchmark, harness, outcome, and action. `event_pct = event_count / event_total * 100` within that benchmark-harness-outcome bucket.",
        "- `six_action_trajectory_rates.csv` counts trajectories containing at least one action. `trajectory_rate_pct = trajectories_with_action / trajectory_count * 100`; `mean_events_per_trajectory` is the mean number of events of that action per trajectory.",
        "- `six_action_denominator_ledger.csv` records task files, trajectory records, outcome counts, records with extracted events, total action events, and low-confidence events.",
        "- `six_action_failure_summary.csv` summarizes failure trajectories only, including dominant failure action composition and terminal action rates.",
        "",
        "For AIME Pass@4, each sample is treated as one trajectory. A sample with `correct=true` is a success trajectory; a sample with `correct=false` is a failure trajectory. Pass@4 task-level success is not used as the action-frequency denominator.",
        "",
        "## Denominator Ledger",
        "",
        "| Benchmark | Harness | Source ID | Records | Success | Failure | Unknown | Events | Low-conf events |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in ledger_rows:
        lines.append(
            f"| {row['benchmark']} | {row['harness']} | `{row['source_id']}` | "
            f"{row['trajectory_records']} | {row['success_records']} | {row['failure_records']} | "
            f"{row['unknown_records']} | {row['action_events']} | {row['low_confidence_events']} |"
        )

    lines.extend(["", "## Event Frequency by Outcome", ""])
    grouped: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)
    totals: dict[tuple[str, str, str], str] = {}
    for row in freq_rows:
        key = (row["benchmark"], row["harness"], row["outcome"])
        grouped[key][row["action"]] = f"{row['event_count']} ({row['event_pct']}%)"
        totals[key] = row["event_total"]
    lines.append("| Benchmark | Harness | Outcome | Events | Problem Framing | Plan Formation | Solution Execution | Tool Grounding | Result Auditing | Answer Delivery |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for key in sorted(grouped):
        benchmark, harness, outcome = key
        vals = grouped[key]
        lines.append(
            f"| {benchmark} | {harness} | {outcome} | {totals[key]} | "
            + " | ".join(vals.get(action, "0 (0.00%)") for action in ACTIONS)
            + " |"
        )

    lines.extend(["", "## Failure Case Summary", ""])
    lines.append("| Benchmark | Harness | Failure traj. | Failure events | Top action | Top action % | Terminal answer % | Terminal audit % | Tool event % | Audit event % |")
    lines.append("|---|---|---:|---:|---|---:|---:|---:|---:|---:|")
    for row in failure_rows:
        lines.append(
            f"| {row['benchmark']} | {row['harness']} | {row['failure_trajectories']} | "
            f"{row['failure_events']} | {row['top_action']} | {row['top_action_pct']} | "
            f"{row['terminal_answer_delivery_rate_pct']} | {row['terminal_result_auditing_rate_pct']} | "
            f"{row['tool_grounding_event_pct']} | {row['result_auditing_event_pct']} |"
        )

    lines.extend(
        [
            "",
            "## Failure Interpretation Guide",
            "",
            "- High Solution Execution on failures means wrong trajectories kept doing internal reasoning or calculation rather than being blocked by missing output.",
            "- High Tool Grounding on failures means tool interaction or observation integration dominated the failed trajectory.",
            "- High Result Auditing on failures means trajectories spent substantial mass checking, diagnosing, or correcting but did not end correct.",
            "- High terminal Answer Delivery on failures means the model still emitted a final answer despite being wrong.",
            "- High terminal Result Auditing on failures means the trajectory often ended in checking, uncertainty, or unresolved correction instead of a final answer.",
            "",
            "## Output Files",
            "",
            f"- `{freq_path.relative_to(ROOT)}`",
            f"- `{rate_path.relative_to(ROOT)}`",
            f"- `{failure_path.relative_to(ROOT)}`",
            f"- `{(output_dir / 'six_action_events.csv').relative_to(ROOT)}`",
            f"- `{(output_dir / 'six_action_denominator_ledger.csv').relative_to(ROOT)}`",
        ]
    )

    (output_dir / "six_action_frequency_report.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    specs = [parse_spec(value) for value in args.spec] if args.spec else default_specs()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    all_events: list[dict[str, Any]] = []
    ledger_rows: list[dict[str, Any]] = []

    for spec in specs:
        tasks_dir = spec.root / "tasks"
        records = load_task_records(tasks_dir)
        events_for_spec: list[dict[str, Any]] = []
        outcome_counts = Counter(record_success(rec) for rec in records)
        task_files = len({rec.get("_task_file") for rec in records})
        records_with_events = 0
        for rec in records:
            rows = event_rows_for_record(spec, rec)
            if rows:
                records_with_events += 1
            events_for_spec.extend(rows)
        all_events.extend(events_for_spec)
        ledger_rows.append(
            {
                "benchmark": spec.benchmark,
                "harness": spec.harness,
                "source_id": spec.source_id,
                "task_files": task_files,
                "trajectory_records": len(records),
                "success_records": outcome_counts.get("success", 0),
                "failure_records": outcome_counts.get("failure", 0),
                "unknown_records": outcome_counts.get("unknown", 0),
                "records_with_events": records_with_events,
                "action_events": len(events_for_spec),
                "low_confidence_events": sum(1 for row in events_for_spec if row["low_confidence"]),
            }
        )

    compute_tables(all_events, ledger_rows, output_dir)
    write_report(output_dir, ledger_rows)
    print(f"wrote {output_dir}")
    print(f"action_events={len(all_events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
