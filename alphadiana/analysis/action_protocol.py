"""The fixed-target six-class contract shared by API and human-audit runner."""

from importlib.resources import files

PROTOCOL = "human-protocol-v2"
LABELS = (
    "Understanding",
    "Planning",
    "Reasoning",
    "Tool Use",
    "Verification",
    "Finalization",
)


def prompt_bytes() -> bytes:
    return files("alphadiana.analysis").joinpath("action_prompt.md").read_bytes()


def request_for(row: dict) -> dict:
    """Preserve the original UI target/context; expose no reference labels."""
    if not isinstance(row, dict):
        raise ValueError("A fixed target must be an object")
    if not isinstance(row.get("item_id"), str) or not row["item_id"].strip():
        raise ValueError("A fixed target needs a nonempty item_id")
    if not isinstance(row.get("full_text"), str) or not row["full_text"].strip():
        raise ValueError("A fixed target needs nonempty full_text")
    for field in ("context_text", "following_text"):
        if field not in row or (
            row[field] is not None and not isinstance(row[field], str)
        ):
            raise ValueError(f"A fixed target needs text or null {field}")
    return {
        "before_context": row["context_text"] or "",
        "actions": [{"action_id": row["item_id"], "text": row["full_text"]}],
        "after_context": row["following_text"] or "",
    }


def validate_response(value: dict, item_id: str) -> str:
    if not isinstance(value, dict) or set(value) != {"actions"}:
        raise ValueError("Expected only the actions object")
    actions = value["actions"]
    if not isinstance(actions, list) or len(actions) != 1:
        raise ValueError("Expected exactly one fixed target action")
    row = actions[0]
    if (
        not isinstance(row, dict)
        or set(row) != {"action_id", "label"}
        or row["action_id"] != item_id
        or row["label"] not in LABELS
    ):
        raise ValueError("Invalid action ID or category")
    return row["label"]
