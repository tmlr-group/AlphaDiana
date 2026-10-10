"""Token and request-boundary entropy measurements over saved evidence."""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict

from alphadiana.analysis.analyzer import Figure, Measurement
from alphadiana.logprobs import _compute_entropy
from alphadiana.analysis.io.status import VALID_SCORE_STATUS
from alphadiana.analysis.resource_usage import DEFAULT_USAGE_EXTRACTORS, measured_usage


from collections.abc import Sequence
from alphadiana.artifacts import Artifacts
from alphadiana.logprobs import _compute_entropy_stats

__all__ = ["EntropyAnalysis", "RequestEntropy", "_compute_entropy", "_compute_entropy_stats"]

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
