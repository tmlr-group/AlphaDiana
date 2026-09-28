"""Token entropy conditioned on observed model-request boundaries."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Sequence

from alphadiana.analysis.analyzer import Figure, Measurement
from alphadiana.analysis.entropy import _compute_entropy
from alphadiana.artifacts import Artifacts


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
                    "Generation after tool feedback",
                    "Token position within model request",
                    "Top-K entropy (nats)",
                    group="series",
                    xscale="log",
                )
            ],
            coverage,
        )
