"""Measurement contracts and execution, resource-usage, token-entropy and request-entropy calculations. Token and request weighting remain distinct."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from alphadiana.results.artifacts import Artifacts
from alphadiana.results.logprobs import _compute_entropy, _compute_entropy_stats
from alphadiana.results.statistics import RunStatistics, compute_reliability_summary
from alphadiana.results.status import VALID_SCORE_STATUS


@dataclass(frozen=True)
class MeasuredUsage:
    output_tokens: int | None
    provenance: dict


UsageExtractor = Callable[[Artifacts, dict], MeasuredUsage | None]


def _count(value):
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        else None
    )


def openclaw_session_usage(source: Artifacts, record: dict) -> MeasuredUsage | None:
    """Use only the retained selected task session, never every runtime session."""
    files = (record.get("artifact_manifest") or {}).get("files", {})
    if not files.get("openclaw_session"):
        return None
    path = source.file(record, "openclaw_session")
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    evidence = {
        "source": "openclaw_session",
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if not rows or rows[0].get("type") != "session" or rows[0].get("version") != 3:
        return MeasuredUsage(None, dict(evidence, missing="unsupported_session_schema"))
    messages = [row for row in rows if isinstance(row.get("message"), dict)]
    user_text = []
    for row in messages:
        message = row["message"]
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            user_text.append(content)
        elif isinstance(content, list):
            user_text.extend(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
    problem = str(record.get("problem") or "").strip()
    if not problem or not any(problem in text for text in user_text):
        return MeasuredUsage(None, dict(evidence, missing="task_identity_unverified"))
    assistants = [row for row in messages if row["message"].get("role") == "assistant"]
    ids = [row.get("id") for row in assistants]
    if any(not identity for identity in ids) or len(set(ids)) != len(ids):
        raise ValueError(
            f"Missing or duplicate native assistant message identity: {path}"
        )
    totals = {
        "input": 0,
        "output": 0,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": 0,
    }
    for row in assistants:
        usage = row["message"].get("usage") or {}
        counts = {key: _count(usage.get(key)) for key in totals}
        if any(value is None for value in counts.values()):
            return MeasuredUsage(
                None,
                dict(
                    evidence,
                    missing="incomplete_request_usage",
                    assistant_messages=len(assistants),
                ),
            )
        if counts["totalTokens"] != sum(
            counts[key] for key in ("input", "output", "cacheRead", "cacheWrite")
        ):
            raise ValueError(
                f"Native token totals disagree: {path}, message {row['id']}"
            )
        for key, value in counts.items():
            totals[key] += value
    if not assistants:
        return MeasuredUsage(None, dict(evidence, missing="no_assistant_messages"))
    return MeasuredUsage(
        totals["output"],
        dict(
            evidence,
            native_totals=totals,
            assistant_messages=len(assistants),
            task_identity_verified=True,
        ),
    )


def opencode_event_usage(source: Artifacts, record: dict) -> MeasuredUsage | None:
    """Aggregate completed native steps from one retained, task-bound session."""
    files = (record.get("artifact_manifest") or {}).get("files", {})
    key = "opencode_output"
    if not files.get(key):
        reference = files.get("response_stream")
        if (
            not isinstance(reference, str)
            or Path(reference).name != "opencode_output.jsonl"
        ):
            return None
        key = "response_stream"
    path = source.file(record, key)
    raw = path.read_bytes()
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    evidence = {
        "source": "opencode_output",
        "artifact_key": key,
        "path": str(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    prompt = source.file(record, "prompt_text")
    problem = str(record.get("problem") or "").strip()
    if prompt is None or not problem or problem not in prompt.read_text():
        return MeasuredUsage(None, dict(evidence, missing="task_identity_unverified"))
    evidence["prompt_sha256"] = hashlib.sha256(prompt.read_bytes()).hexdigest()
    sessions = {row.get("sessionID") for row in rows}
    expected = (record.get("metadata") or {}).get("session_id")
    if len(sessions) != 1 or None in sessions or (expected and sessions != {expected}):
        return MeasuredUsage(
            None, dict(evidence, missing="session_identity_unverified")
        )
    pending, seen_parts, outputs, native = {}, set(), [], []
    for row in rows:
        if row.get("type") not in {"step_start", "step_finish"}:
            continue
        part = row.get("part") or {}
        part_id, message = part.get("id"), part.get("messageID")
        if not part_id or part_id in seen_parts or not message:
            raise ValueError(f"Missing or duplicate native step identity: {path}")
        seen_parts.add(part_id)
        if part.get("sessionID") != row["sessionID"]:
            raise ValueError(f"Native step session identity disagrees: {path}")
        if row["type"] == "step_start":
            pending[message] = pending.get(message, 0) + 1
            continue
        if not pending.get(message):
            raise ValueError(f"Native step finish has no matching start: {path}")
        pending[message] -= 1
        tokens = part.get("tokens") or {}
        output = _count(tokens.get("output"))
        if output is None:
            return MeasuredUsage(
                None, dict(evidence, missing="incomplete_request_usage")
            )
        outputs.append(output)
        native.append({"message_id": message, "part_id": part_id, "tokens": tokens})
    if not outputs or any(pending.values()):
        return MeasuredUsage(None, dict(evidence, missing="incomplete_native_steps"))
    return MeasuredUsage(
        sum(outputs),
        dict(
            evidence,
            completed_steps=len(outputs),
            native_usage=native,
            task_identity_verified=True,
        ),
    )


DEFAULT_USAGE_EXTRACTORS = (openclaw_session_usage, opencode_event_usage)


def measured_usage(
    source: Artifacts,
    record: dict,
    extractors: Sequence[UsageExtractor] = DEFAULT_USAGE_EXTRACTORS,
) -> MeasuredUsage:
    usage = record.get("token_usage") or {}
    output = _count(usage.get("completion_tokens", usage.get("output_tokens")))
    if output is not None:
        return MeasuredUsage(
            output,
            {
                "source": "persisted_token_usage",
                **((record.get("metadata") or {}).get("token_usage_provenance") or {}),
            },
        )
    for extractor in extractors:
        result = extractor(source, record)
        if result is not None:
            return result
    return MeasuredUsage(
        None, {"source": "unavailable", "missing": "no_measured_output_usage"}
    )


@dataclass(frozen=True)
class Figure:
    name: str
    table: str
    kind: str
    x: str
    y: str
    title: str
    xlabel: str
    ylabel: str
    group: str | None = None
    xscale: str = "linear"
    value: str = "count"
    value_label: str = "Transitions"
    color_limits: tuple[float, float] | None = None
    lower: str | None = None
    upper: str | None = None
    ellipse: bool = False


@dataclass
class Measurement:
    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    figures: list[Figure] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)


class AnalysisPlugin(Protocol):
    """A custom measurement needs no edits to Runner or built-in analyzers."""

    name: str

    def measure(self, artifacts: Sequence[Artifacts]) -> Measurement: ...


def _number(value: Any) -> float | None:
    import math

    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


class ExecutionMetrics:
    name = "execution"

    def __init__(
        self, *, usage_extractors: Sequence[UsageExtractor] = DEFAULT_USAGE_EXTRACTORS
    ):
        self.usage_extractors = tuple(usage_extractors)

    def measure(self, artifacts: Sequence[Artifacts]) -> Measurement:
        summary, samples, reliability = [], [], []
        coverage = {}
        for source in artifacts:
            records = source.records()
            manifest = source.manifest()
            base = RunStatistics.from_records(records, manifest)
            records = base.results
            expected = base.expected_sample_count
            if expected < len(records):
                raise ValueError(
                    f"Manifest sample count smaller than records: {source.path}"
                )
            label = source.path.stem
            correct, scored = base.correct, len(base.valid)
            tokens, times = [], []
            for row in records:
                usage = measured_usage(source, row, self.usage_extractors)
                token_count = usage.output_tokens
                elapsed = _number(row.get("wall_time_sec"))
                entropy_stats = row.get("token_entropy_stats") or {}
                entropy = (
                    _number(entropy_stats.get("mean"))
                    if entropy_stats.get("n_tokens", 0)
                    else None
                )
                if token_count is not None:
                    tokens.append(token_count)
                if elapsed is not None:
                    times.append(elapsed)
                samples.append(
                    {
                        "run": label,
                        "task_id": row["task_id"],
                        "sample_index": row.get("sample_index", 0),
                        "correct": row.get("correct"),
                        "score_status": row.get("score_status"),
                        "output_tokens": token_count,
                        "usage_provenance": usage.provenance,
                        "seconds": elapsed,
                        "entropy_nats": entropy,
                    }
                )
            rates = compute_reliability_summary(records, manifest)
            for key, title in (("pass_at_k", "Pass"), ("avg_at_k", "Avg")):
                reliability.append(
                    {
                        "run": label,
                        "metric": f"{title}@{rates['num_samples']}",
                        "value": rates[key] if expected else None,
                        "requested": expected,
                        "observed": len(records),
                    }
                )
            summary.append(
                {
                    "run": label,
                    "benchmark": manifest.get(
                        "benchmark_name",
                        records[0].get("benchmark_name", "") if records else "",
                    ),
                    "agent": manifest.get(
                        "agent_name",
                        records[0].get("agent_name", "") if records else "",
                    ),
                    "requested": expected,
                    "observed": len(records),
                    "scored": scored,
                    "correct": correct,
                    "accuracy": correct / expected if expected else None,
                    "num_samples": rates["num_samples"],
                    "pass_at_k": rates["pass_at_k"] if expected else None,
                    "avg_at_k": rates["avg_at_k"] if expected else None,
                    "mean_output_tokens": sum(tokens) / len(tokens) if tokens else None,
                    "mean_seconds": sum(times) / len(times) if times else None,
                    "tokens_per_correct": sum(tokens) / correct
                    if correct and len(tokens) == len(records)
                    else None,
                    "seconds_per_correct": sum(times) / correct
                    if correct and len(times) == len(records)
                    else None,
                }
            )
            coverage[label] = {
                "requested": expected,
                "observed": len(records),
                "scored": scored,
                "missing_samples": base.missing_samples,
                "tokens_observed": len(tokens),
                "time_observed": len(times),
                "action_labeling": "reported separately in annotated_actions; enable judge=True or an explicit annotation plugin",
            }
        figures = [
            Figure(
                "reliability",
                "reliability",
                "bar",
                "run",
                "value",
                "Task reliability",
                "Run",
                "Fraction",
                group="metric",
            ),
            Figure(
                "mean_tokens",
                "performance",
                "barh",
                "run",
                "mean_output_tokens",
                "Mean output tokens per attempt",
                "Run",
                "Output tokens",
            ),
            Figure(
                "mean_time",
                "performance",
                "barh",
                "run",
                "mean_seconds",
                "Mean elapsed time per attempt",
                "Run",
                "Seconds",
            ),
            Figure(
                "accuracy",
                "performance",
                "barh",
                "run",
                "accuracy",
                "Task accuracy",
                "Run",
                "Correct / requested",
            ),
            Figure(
                "efficiency",
                "performance",
                "barh",
                "run",
                "tokens_per_correct",
                "Output cost per correct outcome",
                "Run",
                "Output tokens / correct",
            ),
            Figure(
                "time_efficiency",
                "performance",
                "barh",
                "run",
                "seconds_per_correct",
                "Elapsed cost per correct outcome",
                "Run",
                "Seconds / correct",
            ),
            Figure(
                "entropy_tokens",
                "samples",
                "scatter",
                "output_tokens",
                "entropy_nats",
                "Generation entropy and output length",
                "Output tokens",
                "Top-K entropy (nats)",
                "run",
            ),
        ]
        return Measurement(
            {
                "performance": summary,
                "reliability": reliability,
                "samples": samples,
            },
            figures,
            coverage,
        )


__all__ = [
    "EntropyAnalysis",
    "RequestEntropy",
    "_compute_entropy",
    "_compute_entropy_stats",
]


class EntropyAnalysis:
    """Token-weighted entropy after observed tool feedback, plus length scatter.

    Position one is the first generated token of a request explicitly marked as
    following tool feedback. No trajectory-wide position is relabeled post-tool.
    Each bin retains counts, call counts, boundaries and descriptive standard
    error; token autocorrelation means this is not a confidence interval.
    """

    name = "entropy"

    def __init__(
        self,
        *,
        bin_ratio=2,
        min_observations=5,
        usage_extractors=DEFAULT_USAGE_EXTRACTORS,
    ):
        if (
            not isinstance(bin_ratio, (int, float))
            or not math.isfinite(bin_ratio)
            or bin_ratio <= 1
        ):
            raise ValueError("bin_ratio must be finite and greater than one")
        if type(min_observations) is not int or min_observations <= 0:
            raise ValueError("min_observations must be a positive integer")
        self.bin_ratio, self.min_observations = bin_ratio, min_observations
        self.usage_extractors = tuple(usage_extractors)

    def measure(self, artifacts):
        bins, calls = defaultdict(lambda: [0, 0.0, 0.0]), defaultdict(set)
        scatter, coverage = [], {}
        for source in artifacts:
            run = source.path.stem
            counts = {
                "missing_sidecars": 0,
                "missing_attribution": 0,
                "excluded_unscored": 0,
                "missing_top_k": 0,
                "post_tool_tokens": 0,
                "other_request_tokens": 0,
                "missing_output_usage": 0,
                "sources": [],
            }
            for row in source.records():
                if row.get("score_status") != VALID_SCORE_STATUS:
                    counts["excluded_unscored"] += 1
                    continue
                outcome = "Correct" if row.get("correct") is True else "Incorrect"
                path = source.file(row, "logprobs_float")
                if path is None:
                    counts["missing_sidecars"] += 1
                    continue
                counts["sources"].append(
                    {
                        "path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
                observed_count, observed_sum = 0, 0.0
                with path.open() as stream:
                    for line in stream:
                        token = json.loads(line)
                        if not token.get("top_logprobs"):
                            counts["missing_top_k"] += 1
                            continue
                        entropy = _compute_entropy(token["top_logprobs"])
                        if not math.isfinite(entropy):
                            raise ValueError("Nonfinite token entropy")
                        observed_count += 1
                        observed_sum += entropy
                        position, call, follows = (
                            token.get("call_token_index"),
                            token.get("call_id"),
                            token.get("follows_tool_result"),
                        )
                        if (
                            type(position) is not int
                            or position < 0
                            or not isinstance(call, str)
                            or not call
                            or type(follows) is not bool
                        ):
                            counts["missing_attribution"] += 1
                            continue
                        if not follows:
                            counts["other_request_tokens"] += 1
                            continue
                        counts["post_tool_tokens"] += 1
                        index = math.floor(math.log(position + 1, self.bin_ratio))
                        key = run, outcome, index
                        accumulator = bins[key]
                        accumulator[0] += 1
                        accumulator[1] += entropy
                        accumulator[2] += entropy * entropy
                        calls[key].add(
                            (row["task_id"], row.get("sample_index", 0), call)
                        )
                usage = measured_usage(source, row, self.usage_extractors).output_tokens
                if usage is None:
                    counts["missing_output_usage"] += 1
                if observed_count and usage is not None and usage > 0:
                    scatter.append(
                        {
                            "run": run,
                            "outcome": outcome,
                            "series": f"{run} / {outcome}",
                            "task_id": row["task_id"],
                            "sample_index": row.get("sample_index", 0),
                            "output_tokens": usage,
                            "log10_output_tokens": math.log10(usage),
                            "observed_entropy_tokens": observed_count,
                            "entropy_nats": observed_sum / observed_count,
                        }
                    )
            coverage[run] = counts
        rows, plotted = [], []
        for (run, outcome, index), (n, total, squared) in sorted(bins.items()):
            mean = total / n
            stderr = math.sqrt(max(0, squared / n - mean * mean) / n)
            left, right = self.bin_ratio**index, self.bin_ratio ** (index + 1)
            row = {
                "run": run,
                "outcome": outcome,
                "series": f"{run} / {outcome}",
                "bin_left": left,
                "bin_right": right,
                "token_position": math.sqrt(left * right),
                "entropy_nats": mean,
                "standard_error": stderr,
                "lower": max(0, mean - stderr),
                "upper": mean + stderr,
                "n_tokens": n,
                "n_calls": len(calls[run, outcome, index]),
            }
            rows.append(row)
            if n >= self.min_observations:
                plotted.append(row)
        coverage["method"] = {
            "alignment": "request start after explicit tool feedback",
            "bin_ratio": self.bin_ratio,
            "min_observations": self.min_observations,
            "position": "one-based, geometric bin midpoint",
            "weighting": "observed tokens",
            "entropy": "renormalized observed top-K, nats",
            "band": "mean +/- descriptive standard error; not a confidence interval",
            "length": "measured output usage; missing usage is not imputed",
            "ellipse": "2-standard-deviation covariance contour, at least 10 samples",
            "suppressed_bins": len(rows) - len(plotted),
        }
        return Measurement(
            {
                "post_tool_bins": rows,
                "post_tool_entropy": plotted,
                "entropy_length": scatter,
            },
            [
                Figure(
                    "post_tool_entropy",
                    "post_tool_entropy",
                    "line",
                    "token_position",
                    "entropy_nats",
                    "Entropy after tool feedback",
                    "Post-tool token position",
                    "Top-K entropy (nats)",
                    group="series",
                    xscale="log",
                    lower="lower",
                    upper="upper",
                ),
                Figure(
                    "entropy_length",
                    "entropy_length",
                    "scatter",
                    "log10_output_tokens",
                    "entropy_nats",
                    "Output length and entropy",
                    "log10 output tokens",
                    "Mean top-K entropy (nats)",
                    group="series",
                    ellipse=True,
                ),
            ],
            coverage,
        )


class RequestEntropy:
    """Equal-call mean entropy in fixed token bins after explicit tool feedback.

    This is descriptive conditioning, not a causal intervention. Old captures
    without request attribution are reported as missing and never reconstructed
    by matching token text. Entropy uses the renormalized observed top-K support.
    """

    name = "request_entropy"

    def __init__(self, *, bin_size: int = 32):
        if isinstance(bin_size, bool) or not isinstance(bin_size, int) or bin_size <= 0:
            raise ValueError("bin_size must be a positive integer")
        self.bin_size = bin_size

    def measure(self, artifacts: Sequence[Artifacts]) -> Measurement:
        call_bins = defaultdict(list)
        coverage = {}
        for source in artifacts:
            label = source.path.stem
            counts = {
                "tokens": 0,
                "attributed_tokens": 0,
                "missing_top_k": 0,
                "missing_sidecars": 0,
                "sources": [],
            }
            for row in source.records():
                path = source.file(row, "logprobs_float")
                if path is None:
                    counts["missing_sidecars"] += 1
                    continue
                counts["sources"].append(
                    {
                        "path": str(path),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
                with path.open() as stream:
                    for line in stream:
                        token = json.loads(line)
                        counts["tokens"] += 1
                        call = token.get("call_id")
                        index = token.get("call_token_index")
                        follows = token.get("follows_tool_result")
                        if (
                            not isinstance(call, str)
                            or not call
                            or type(index) is not int
                            or index < 0
                            or type(follows) is not bool
                        ):
                            continue
                        counts["attributed_tokens"] += 1
                        top = token.get("top_logprobs")
                        if not top:
                            counts["missing_top_k"] += 1
                            continue
                        condition = (
                            "After tool feedback" if follows else "Other requests"
                        )
                        key = (
                            label,
                            condition,
                            index // self.bin_size,
                            row["task_id"],
                            row.get("sample_index", 0),
                            call,
                        )
                        call_bins[key].append(_compute_entropy(top))
            counts["missing_attribution"] = (
                counts["tokens"] - counts["attributed_tokens"]
            )
            coverage[label] = counts
        bins = defaultdict(list)
        for (run, condition, index, task, sample, call), entropies in call_bins.items():
            bins[(run, condition, index)].append(
                (sum(entropies) / len(entropies), len(entropies))
            )
        rows = [
            {
                "run": run,
                "condition": condition,
                "series": f"{run} / {condition}",
                "token_position": index * self.bin_size + 1,
                "entropy_nats": sum(value for value, _ in calls) / len(calls),
                "n_calls": len(calls),
                "n_tokens": sum(count for _, count in calls),
            }
            for (run, condition, index), calls in sorted(bins.items())
        ]
        coverage["method"] = {
            "bin_size": self.bin_size,
            "weighting": "equal call means per bin",
            "entropy": "renormalized observed top-K, nats",
            "interpretation": "descriptive; not causal",
        }
        return Measurement(
            {"entropy_by_request_position": rows},
            [
                Figure(
                    "entropy_by_request_position",
                    "entropy_by_request_position",
                    "line",
                    "token_position",
                    "entropy_nats",
                    "Entropy by request position",
                    "Token position within model request",
                    "Top-K entropy (nats)",
                    group="series",
                    xscale="log",
                )
            ],
            coverage,
        )
