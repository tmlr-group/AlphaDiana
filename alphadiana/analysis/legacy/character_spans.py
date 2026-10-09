"""Archived custom-spans-v1 character segmentation, retained for reproduction."""

import json


def character_targets(steps, span_chars):
    targets = []
    for index, step in enumerate(steps):
        if step.get("role") in {
            "system",
            "developer",
            "user",
            "tool",
            "function",
        } or step.get("type") in {"tool_result", "observation"}:
            continue
        text = (
            step.get("content") or step.get("text") or json.dumps(step, sort_keys=True)
        )
        if not isinstance(text, str):
            text = json.dumps(text)
        for start in range(0, len(text), span_chars):
            end = min(len(text), start + span_chars)
            evidence = {
                "target": text[start:end],
                "step_type": step.get("type"),
                "before": text[max(0, start - 1000) : start],
                "after": text[end : end + 1000],
                "previous_step": (
                    json.dumps(steps[index - 1], sort_keys=True)[-1000:]
                    if index
                    else ""
                ),
                "next_step": (
                    json.dumps(steps[index + 1], sort_keys=True)[:1000]
                    if index + 1 < len(steps)
                    else ""
                ),
            }
            targets.append(
                (
                    evidence,
                    {
                        "step_index": index,
                        "span_start": start,
                        "span_end": end,
                        "sequence_index": len(targets),
                    },
                )
            )
    return targets
