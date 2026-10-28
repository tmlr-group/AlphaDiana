"""Constructed fixed-span fixtures, not human calibration data."""

import hashlib
import json


def fixed_item(item_id, text, position=None, before="", after=""):
    item = dict(
        item_id=item_id, full_text=text, context_text=before, following_text=after
    )
    if position is not None:
        item["sequence_index"] = position
    return item


def attach_targets(artifacts, items):
    rows = artifacts.records(latest=False)
    for index, row in enumerate(rows):
        trace = artifacts.file(row, "normalized_trace")
        name = f"targets-{index}.json"
        target = artifacts.artifact_root(row) / name
        target.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
                    "items": items,
                }
            ),
            encoding="utf8",
        )
        row["artifact_manifest"]["files"]["action_targets"] = name
    artifacts.path.write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf8"
    )
