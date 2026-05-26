#!/usr/bin/env python3
"""Paired GPQA degradation diagnostics for DirectLLM vs agent harnesses.

This tool is offline-only. It reads persisted ResultStore artifacts and writes
task-level explanatory features plus harness-level summaries under
``analyze_tools/data``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from alphadiana.analysis.action_events import extract_action_event_rows
from alphadiana.analysis.result_reader import RunBundle, load_run_bundle
from alphadiana.analysis.io.status import VALID_SCORE_STATUS, infer_score_status

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = ROOT / "results"
DEFAULT_DATA_DIR = ROOT / "analyze_tools" / "data"
DEFAULT_DIRECT_RUN = "phase9_directllm_gpqa_diamond_qwen35_27b_logprobs"
DEFAULT_RUNS = {
    "openclaw": "full_gpqa_v2_openclaw_qwen35_27b_logprobs",
    "opencode": "full_gpqa_v2_opencode_qwen35_27b_logprobs",
    "zeroclaw": "full_gpqa_v2_zeroclaw_qwen35_27b_logprobs",
}
CANONICAL_ACTIONS = ("plan", "reason", "tool_use", "verify", "recover", "answer")
ERROR_STATUSES = {"agent_error", "provider_error", "runtime_error", "scorer_error"}
OPTION_RE = re.compile(r"^[ABCD]$")
BOXED_RE = re.compile(r"\\boxed\{\s*([ABCD])\s*\}")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")
csv.field_size_limit(sys.maxsize)

LANGUAGE_MARKERS = {
    "uncertainty": (
        "maybe",
        "perhaps",
        "possibly",
        "not sure",
        "unclear",
        "i think",
        "likely",
        "probably",
    ),
    "self_correction": (
        "wait",
        "actually",
        "reconsider",
        "double check",
        "double-check",
        "however",
        "but",
        "on second thought",
    ),
    "tool_intent": (
        "use tool",
        "tool",
        "search",
        "look up",
        "run python",
        "execute",
        "verify",
        "check",
    ),
    "looping": (
        "let me try",
        "try a different approach",
        "i need to",
        "i should",
        "need to reconsider",
        "let's check",
    ),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--direct-run", default=DEFAULT_DIRECT_RUN)
    parser.add_argument(
        "--harness-run",
        action="append",
        default=[],
        metavar="HARNESS=RUN_ID",
        help="Override or add a harness run. May be repeated.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    harness_runs = dict(DEFAULT_RUNS)
    for item in args.harness_run:
        if "=" not in item:
            raise SystemExit(f"--harness-run must be HARNESS=RUN_ID, got {item!r}")
        harness, run_id = item.split("=", 1)
        harness_runs[harness.strip()] = run_id.strip()

    direct_bundle = resolve_bundle(args.results_dir, args.direct_run)
    direct_records = load_task_leaf_records(direct_bundle)
    bundles = {
        harness: resolve_bundle(args.results_dir, run_id)
        for harness, run_id in harness_runs.items()
    }
    records = {
        harness: load_task_leaf_records(bundle)
        for harness, bundle in bundles.items()
    }
    events = {
        "directllm": events_by_task(direct_bundle, "directllm"),
        **{
            harness: events_by_task(bundle, harness)
            for harness, bundle in bundles.items()
        },
    }
    thresholds = {
        harness: entropy_token_thresholds(recs)
        for harness, recs in records.items()
    }

    task_rows: list[dict[str, object]] = []
    for harness, run_id in harness_runs.items():
        for task_id in sorted(direct_records):
            direct = direct_records[task_id]
            harness_record = records[harness].get(task_id)
            task_rows.append(
                build_task_row(
                    harness=harness,
                    run_id=run_id,
                    task_id=task_id,
                    direct=direct,
                    harness_record=harness_record,
                    direct_events=events["directllm"].get(task_id, []),
                    harness_events=events[harness].get(task_id, []),
                    harness_thresholds=thresholds[harness],
                    harness_run_dir=bundles[harness].run_dir,
                    direct_run_dir=direct_bundle.run_dir,
                )
            )

    summary_rows = build_summary_rows(task_rows)
    cause_rows = build_cause_rows(task_rows)
    output = {
        "analysis": "degradation_diagnostics",
        "direct_run": args.direct_run,
        "harness_runs": harness_runs,
        "task_rows": len(task_rows),
        "summary": summary_rows,
        "cause_buckets": cause_rows,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "degradation_task_features.csv", task_rows)
    write_csv(args.output_dir / "degradation_summary.csv", summary_rows)
    write_csv(args.output_dir / "degradation_cause_buckets.csv", cause_rows)
    (args.output_dir / "degradation_summary.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output_dir / 'degradation_task_features.csv'} ({len(task_rows)} rows)")
    print(f"Wrote {args.output_dir / 'degradation_summary.csv'} ({len(summary_rows)} rows)")
    print(f"Wrote {args.output_dir / 'degradation_cause_buckets.csv'} ({len(cause_rows)} rows)")
    print(f"Wrote {args.output_dir / 'degradation_summary.json'}")
    return 0


def resolve_bundle(results_dir: Path, run_id: str) -> RunBundle:
    bundle = load_run_bundle(results_dir, run_id)
    if bundle.records or bundle.task_records:
        return bundle
    packed = load_run_bundle(results_dir / run_id, run_id)
    if packed.records or packed.task_records:
        return packed
    return bundle


def load_task_leaf_records(bundle: RunBundle) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for task_id, samples in bundle.task_records.items():
        if not samples:
            continue
        record = dict(samples[0])
        record.setdefault("task_id", task_id)
        records[str(record.get("task_id") or task_id)] = record
    if records:
        return records
    for record in bundle.records:
        task_id = str(record.get("task_id") or "").strip()
        if task_id and task_id not in records:
            records[task_id] = dict(record)
    return records


def events_by_task(bundle: RunBundle, harness: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in extract_action_event_rows(bundle, harness=harness):
        if is_instruction_prompt_event(row):
            continue
        grouped[str(row.get("task_id") or "")].append(row)
    return {
        task_id: sorted(rows, key=lambda row: step_sort_key(str(row.get("step_id") or "")))
        for task_id, rows in grouped.items()
    }


def step_sort_key(step_id: str) -> tuple[int, str]:
    return (int(step_id), "") if step_id.isdigit() else (10**9, step_id)


def is_instruction_prompt_event(row: dict[str, Any]) -> bool:
    text = str(row.get("text_span") or "")
    normalized = " ".join(text.split()).lower()
    if not normalized:
        return False
    has_gpqa_instruction = (
        "you are solving expert-level science multiple-choice questions" in normalized
        and "when you have reached your final answer" in normalized
        and "$$\\boxed{a}$$" in normalized
    )
    has_task_payload = "problem:" in normalized or "a." in normalized or "a)" in normalized
    return has_gpqa_instruction and has_task_payload


def entropy_token_thresholds(records: dict[str, dict[str, Any]]) -> dict[str, float]:
    token_values: list[int] = []
    entropy_values: list[float] = []
    for record in records.values():
        if infer_score_status(record) != VALID_SCORE_STATUS:
            continue
        stats = record.get("token_entropy_stats") or {}
        n_tokens = int(stats.get("n_tokens") or 0)
        mean_entropy = stats.get("mean")
        if n_tokens > 0 and isinstance(mean_entropy, (int, float)):
            token_values.append(n_tokens)
            entropy_values.append(float(mean_entropy))
    return {
        "token_q75": percentile(token_values, 0.75),
        "entropy_q25": percentile(entropy_values, 0.25),
    }


def percentile(values: list[int] | list[float], p: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    index = int(p * (len(ordered) - 1))
    return float(ordered[index])


def build_task_row(
    *,
    harness: str,
    run_id: str,
    task_id: str,
    direct: dict[str, Any],
    harness_record: dict[str, Any] | None,
    direct_events: list[dict[str, Any]],
    harness_events: list[dict[str, Any]],
    harness_thresholds: dict[str, float],
    harness_run_dir: Path,
    direct_run_dir: Path,
) -> dict[str, object]:
    direct_valid = is_valid(direct)
    harness_valid = bool(harness_record and is_valid(harness_record))
    direct_correct = bool(direct.get("correct")) if direct_valid else None
    harness_correct = bool(harness_record.get("correct")) if harness_valid and harness_record else None
    paired_outcome = paired_outcome_label(direct_valid, direct_correct, harness_valid, harness_correct)

    direct_text = record_text(direct, direct_events)
    harness_text = record_text(harness_record or {}, harness_events)
    direct_lang = language_features(direct_text, direct.get("predicted"))
    harness_lang = language_features(harness_text, (harness_record or {}).get("predicted"))
    action = action_features(harness_events)
    direct_entropy = entropy_features(direct, direct_run_dir)
    harness_entropy = entropy_features(harness_record or {}, harness_run_dir)
    n_tokens = number_or_zero(harness_entropy["n_tokens"])
    direct_tokens = number_or_zero(direct_entropy["n_tokens"])
    mean_entropy = harness_entropy["mean_entropy"]
    token_ratio = safe_div(n_tokens, direct_tokens)
    low_entropy_long = (
        isinstance(mean_entropy, (int, float))
        and not math.isnan(float(mean_entropy))
        and not math.isnan(harness_thresholds["entropy_q25"])
        and not math.isnan(harness_thresholds["token_q75"])
        and float(mean_entropy) <= harness_thresholds["entropy_q25"]
        and n_tokens >= harness_thresholds["token_q75"]
    )
    cause_bucket = classify_cause(
        paired_outcome=paired_outcome,
        harness_record=harness_record,
        harness_lang=harness_lang,
        action=action,
        low_entropy_long=low_entropy_long,
        token_ratio=token_ratio,
    )

    row: dict[str, object] = {
        "harness": harness,
        "run_id": run_id,
        "task_id": task_id,
        "paired_outcome": paired_outcome,
        "cause_bucket": cause_bucket,
        "direct_score_status": infer_score_status(direct),
        "harness_score_status": infer_score_status(harness_record or {}),
        "direct_correct": bool_or_blank(direct_correct),
        "harness_correct": bool_or_blank(harness_correct),
        "direct_predicted": direct.get("predicted", ""),
        "harness_predicted": (harness_record or {}).get("predicted", ""),
        "direct_ground_truth": direct.get("ground_truth", ""),
        "harness_ground_truth": (harness_record or {}).get("ground_truth", ""),
        "direct_n_tokens": direct_entropy["n_tokens"],
        "harness_n_tokens": harness_entropy["n_tokens"],
        "token_ratio_vs_direct": token_ratio,
        "direct_mean_entropy": direct_entropy["mean_entropy"],
        "harness_mean_entropy": harness_entropy["mean_entropy"],
        "mean_entropy_delta_vs_direct": safe_sub(harness_entropy["mean_entropy"], direct_entropy["mean_entropy"]),
        "harness_token_q75": harness_thresholds["token_q75"],
        "harness_entropy_q25": harness_thresholds["entropy_q25"],
        "low_entropy_long": low_entropy_long,
        "harness_head_entropy_mean": harness_entropy["head_entropy_mean"],
        "harness_tail_entropy_mean": harness_entropy["tail_entropy_mean"],
        "harness_tail_minus_head_entropy": safe_sub(
            harness_entropy["tail_entropy_mean"],
            harness_entropy["head_entropy_mean"],
        ),
        "harness_low_entropy_token_share": harness_entropy["low_entropy_token_share"],
        "harness_top1_prob_mean": harness_entropy["top1_prob_mean"],
        "harness_text_chars": harness_lang["text_chars"],
        "harness_word_count": harness_lang["word_count"],
        "harness_boxed_count": harness_lang["boxed_count"],
        "harness_last_boxed_option": harness_lang["last_boxed_option"],
        "harness_final_line_boxed": harness_lang["final_line_boxed"],
        "harness_malformed_prediction": harness_lang["malformed_prediction"],
        "harness_missing_boxed_answer": harness_lang["missing_boxed_answer"],
        "harness_repeated_ngram_rate": harness_lang["repeated_ngram_rate"],
        "uncertainty_marker_count": harness_lang["uncertainty_marker_count"],
        "self_correction_marker_count": harness_lang["self_correction_marker_count"],
        "tool_intent_marker_count": harness_lang["tool_intent_marker_count"],
        "looping_marker_count": harness_lang["looping_marker_count"],
    }
    row.update(action)
    row["evidence_excerpt"] = compact_excerpt(harness_text)
    return row


def is_valid(record: dict[str, Any]) -> bool:
    if infer_score_status(record) == VALID_SCORE_STATUS:
        return True
    return "score" in record and isinstance(record.get("correct"), bool)


def paired_outcome_label(
    direct_valid: bool,
    direct_correct: bool | None,
    harness_valid: bool,
    harness_correct: bool | None,
) -> str:
    if not direct_valid or not harness_valid:
        return "nonvalid_or_missing"
    if direct_correct and harness_correct:
        return "both_correct"
    if direct_correct and not harness_correct:
        return "regression"
    if not direct_correct and harness_correct:
        return "rescue"
    return "both_wrong"


def record_text(record: dict[str, Any], events: list[dict[str, Any]]) -> str:
    event_text = "\n".join(str(row.get("text_span") or "") for row in events if row.get("text_span"))
    if event_text.strip():
        return event_text
    values = []
    for key in ("raw_output", "rationale", "reasoning_trajectory", "trajectory"):
        value = record.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(json.dumps(item, ensure_ascii=False) for item in value[:20])
    return "\n".join(values)


def language_features(text: str, predicted: Any) -> dict[str, object]:
    normalized = text.strip()
    lower = normalized.lower()
    words = WORD_RE.findall(lower)
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    boxed = BOXED_RE.findall(normalized)
    predicted_text = "" if predicted is None else str(predicted).strip()
    final_line = lines[-1] if lines else ""
    features: dict[str, object] = {
        "text_chars": len(normalized),
        "word_count": len(words),
        "line_count": len(lines),
        "boxed_count": len(boxed),
        "last_boxed_option": boxed[-1] if boxed else "",
        "final_line_boxed": bool(BOXED_RE.search(final_line)),
        "malformed_prediction": not bool(OPTION_RE.fullmatch(predicted_text)),
        "missing_boxed_answer": len(boxed) == 0,
        "repeated_ngram_rate": repeated_ngram_rate(words, n=4),
    }
    for name, markers in LANGUAGE_MARKERS.items():
        features[f"{name}_marker_count"] = sum(lower.count(marker) for marker in markers)
    return features


def repeated_ngram_rate(words: list[str], *, n: int) -> float:
    if len(words) < n:
        return 0.0
    grams = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    counts = Counter(grams)
    repeated = sum(count - 1 for count in counts.values() if count > 1)
    return repeated / len(grams)


def action_features(events: list[dict[str, Any]]) -> dict[str, object]:
    actions = [str(row.get("canonical_action") or "") for row in events]
    counts = Counter(action for action in actions if action)
    first_answer = first_index(actions, "answer")
    first_verify = first_index(actions, "verify")
    first_tool = first_index(actions, "tool_use")
    post_verify_actions = set(actions[first_verify + 1 :]) if first_verify is not None else set()
    tool_subtypes = Counter(str(row.get("tool_subtype") or "other") for row in events if row.get("canonical_action") == "tool_use")
    observation_statuses = Counter(str(row.get("observation_status") or "none") for row in events)
    features: dict[str, object] = {
        "action_count": len(actions),
        "first_action": actions[0] if actions else "",
        "last_action": actions[-1] if actions else "",
        "action_sequence": " ".join(actions),
        "action_entropy": shannon_entropy(counts.values()),
        "tool_subtypes": " ".join(sorted(tool_subtypes)),
        "tool_error_or_fail_count": sum(observation_statuses.get(status, 0) for status in ("error", "fail", "timeout", "truncated")),
        "verify_before_answer": bool(
            first_verify is not None and first_answer is not None and first_verify < first_answer
        ),
        "post_verify_action_change": bool(
            first_verify is not None and any(action not in {"verify", "answer"} for action in post_verify_actions)
        ),
        "tool_before_answer": bool(first_tool is not None and first_answer is not None and first_tool < first_answer),
        "premature_answer": bool(first_answer is not None and first_answer <= 1 and counts.get("tool_use", 0) == 0 and counts.get("verify", 0) == 0),
    }
    for action in CANONICAL_ACTIONS:
        features[f"{action}_count"] = counts.get(action, 0)
    return features


def first_index(values: list[str], target: str) -> int | None:
    try:
        return values.index(target)
    except ValueError:
        return None


def shannon_entropy(values: Any) -> float:
    counts = [float(value) for value in values if value]
    total = sum(counts)
    if total <= 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in counts)


def entropy_features(record: dict[str, Any], run_dir: Path) -> dict[str, object]:
    stats = record.get("token_entropy_stats") or {}
    features = {
        "n_tokens": int(stats.get("n_tokens") or 0),
        "mean_entropy": float(stats["mean"]) if isinstance(stats.get("mean"), (int, float)) else float("nan"),
        "head_entropy_mean": float("nan"),
        "tail_entropy_mean": float("nan"),
        "low_entropy_token_share": float("nan"),
        "top1_prob_mean": float("nan"),
    }
    sidecar = resolve_logprob_sidecar(record, run_dir)
    if sidecar is None:
        return features
    entropies: list[float] = []
    top1_probs: list[float] = []
    try:
        with sidecar.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                entropy = item.get("entropy_nats")
                if isinstance(entropy, (int, float)):
                    entropies.append(float(entropy))
                top1 = top1_probability(item)
                if top1 is not None:
                    top1_probs.append(top1)
    except (OSError, json.JSONDecodeError):
        return features
    if entropies:
        window = min(64, len(entropies))
        features["head_entropy_mean"] = statistics.fmean(entropies[:window])
        features["tail_entropy_mean"] = statistics.fmean(entropies[-window:])
        features["low_entropy_token_share"] = sum(value <= 0.05 for value in entropies) / len(entropies)
    if top1_probs:
        features["top1_prob_mean"] = statistics.fmean(top1_probs)
    return features


def resolve_logprob_sidecar(record: dict[str, Any], run_dir: Path) -> Path | None:
    task_id = str(record.get("task_id") or "").strip()
    refs = [
        record.get("logprobs_int16_path"),
        record.get("logprobs_path"),
        f"logprobs_int16/{task_id}.jsonl" if task_id else "",
        f"logprobs/{task_id}.jsonl" if task_id else "",
    ]
    for ref in refs:
        if not ref:
            continue
        path = Path(str(ref))
        candidates = []
        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.extend([run_dir / path, run_dir.parent / path])
            if path.parts and path.parts[0] == run_dir.name:
                candidates.append(run_dir.parent / path)
        for candidate in candidates:
            if candidate.exists():
                return candidate
    return None


def top1_probability(item: dict[str, Any]) -> float | None:
    top20 = item.get("top20")
    if isinstance(top20, list) and top20:
        prob = top20[0].get("prob_i16")
        if isinstance(prob, (int, float)):
            return max(0.0, min(1.0, float(prob) / 32767.0))
    top_logprobs = item.get("top_logprobs")
    if isinstance(top_logprobs, list) and top_logprobs:
        logprob = top_logprobs[0].get("logprob")
        if isinstance(logprob, (int, float)):
            return math.exp(float(logprob))
    logprob = item.get("logprob")
    if isinstance(logprob, (int, float)):
        return math.exp(float(logprob))
    return None


def classify_cause(
    *,
    paired_outcome: str,
    harness_record: dict[str, Any] | None,
    harness_lang: dict[str, object],
    action: dict[str, object],
    low_entropy_long: bool,
    token_ratio: float,
) -> str:
    status = infer_score_status(harness_record or {})
    if status in ERROR_STATUSES or paired_outcome == "nonvalid_or_missing":
        return "operational_error"
    if paired_outcome != "regression":
        return paired_outcome
    if harness_lang["malformed_prediction"] or harness_lang["missing_boxed_answer"]:
        return "answer_format_or_extraction"
    if low_entropy_long or token_ratio >= 3.0:
        return "long_low_entropy_or_overrun"
    if int(action["tool_use_count"]) > 0 and not action["tool_before_answer"]:
        return "tool_use_not_integrated"
    if int(action["verify_count"]) > 0 and not action["post_verify_action_change"]:
        return "verification_without_conversion"
    if int(action["plan_count"]) + int(action["recover_count"]) >= 3:
        return "planning_recovery_churn"
    if action["premature_answer"]:
        return "premature_answer"
    return "answer_changed_valid"


def build_summary_rows(task_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in task_rows:
        groups[(str(row["harness"]), str(row["paired_outcome"]))].append(row)
    for (harness, outcome), vals in sorted(groups.items()):
        n = len(vals)
        rows.append(
            {
                "harness": harness,
                "paired_outcome": outcome,
                "n": n,
                "share_within_harness": n / sum(1 for row in task_rows if row["harness"] == harness),
                "malformed_prediction_rate": mean_bool(vals, "harness_malformed_prediction"),
                "missing_boxed_answer_rate": mean_bool(vals, "harness_missing_boxed_answer"),
                "low_entropy_long_rate": mean_bool(vals, "low_entropy_long"),
                "mean_token_ratio_vs_direct": mean_number(vals, "token_ratio_vs_direct"),
                "mean_entropy_delta_vs_direct": mean_number(vals, "mean_entropy_delta_vs_direct"),
                "mean_action_count": mean_number(vals, "action_count"),
                "mean_tool_use_count": mean_number(vals, "tool_use_count"),
                "mean_verify_count": mean_number(vals, "verify_count"),
                "mean_plan_count": mean_number(vals, "plan_count"),
                "mean_recover_count": mean_number(vals, "recover_count"),
                "verify_before_answer_rate": mean_bool(vals, "verify_before_answer"),
                "post_verify_action_change_rate": mean_bool(vals, "post_verify_action_change"),
                "premature_answer_rate": mean_bool(vals, "premature_answer"),
                "mean_repeated_ngram_rate": mean_number(vals, "harness_repeated_ngram_rate"),
                "mean_looping_marker_count": mean_number(vals, "looping_marker_count"),
            }
        )
    return rows


def build_cause_rows(task_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    counts: Counter[tuple[str, str, str]] = Counter()
    totals: Counter[tuple[str, str]] = Counter()
    for row in task_rows:
        key = (str(row["harness"]), str(row["paired_outcome"]))
        totals[key] += 1
        counts[(key[0], key[1], str(row["cause_bucket"]))] += 1
    rows = []
    for (harness, outcome, bucket), n in sorted(counts.items()):
        total = totals[(harness, outcome)]
        rows.append(
            {
                "harness": harness,
                "paired_outcome": outcome,
                "cause_bucket": bucket,
                "n": n,
                "share_within_outcome": n / total if total else float("nan"),
            }
        )
    return rows


def mean_bool(rows: list[dict[str, object]], key: str) -> float:
    if not rows:
        return float("nan")
    return sum(bool(row.get(key)) for row in rows) / len(rows)


def mean_number(rows: list[dict[str, object]], key: str) -> float:
    vals = [float(row[key]) for row in rows if isinstance(row.get(key), (int, float)) and not math.isnan(float(row[key]))]
    return statistics.fmean(vals) if vals else float("nan")


def safe_div(a: float, b: float) -> float:
    return a / b if b else float("nan")


def safe_sub(a: object, b: object) -> float:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isnan(float(a)) or math.isnan(float(b)):
            return float("nan")
        return float(a) - float(b)
    return float("nan")


def number_or_zero(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) and not math.isnan(float(value)) else 0.0


def bool_or_blank(value: bool | None) -> object:
    return "" if value is None else value


def compact_excerpt(text: str, limit: int = 280) -> str:
    return " ".join(text.split())[:limit]


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
