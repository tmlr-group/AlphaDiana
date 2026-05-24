from __future__ import annotations

import json
from pathlib import Path

from alphadiana.utils.lifecycle_events import append_lifecycle_event


def test_append_lifecycle_event_writes_redacted_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "lifecycle" / "task.jsonl"

    append_lifecycle_event(
        path,
        run_id="run1",
        task_id="task1",
        sample_index=0,
        stage="provider_connected",
        metadata={
            "status_code": 200,
            "command": "OPENAI_API_KEY=secret python -m thing",
            "Authorization": "Bearer secret",
        },
    )

    event = json.loads(path.read_text(encoding="utf-8"))
    assert event["run_id"] == "run1"
    assert event["task_id"] == "task1"
    assert event["stage"] == "provider_connected"
    assert event["metadata"]["command"] == "OPENAI_API_KEY=<redacted> python -m thing"
    assert event["metadata"]["Authorization"] == "<redacted>"
