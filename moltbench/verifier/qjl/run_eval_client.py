#!/usr/bin/env python3
"""Agent-facing client for the host-controlled QJL verifier."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 1:
        print("run_eval does not accept benchmark parameter overrides", file=sys.stderr)
        return 2
    root = Path("/workspace/repo")
    files = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((root / "qjl_kernel" / "csrc").glob("*.cu"))
    }
    request = urllib.request.Request(
        os.environ["MOLTBENCH_VERIFIER_URL"],
        data=json.dumps({"files": files}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['MOLTBENCH_RUN_EVAL_TOKEN']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        timeout = int(os.environ.get("MOLTBENCH_ATTEMPT_TIMEOUT_SEC", "1200"))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        payload = json.loads(exc.read().decode("utf-8"))
        print(json.dumps(payload, sort_keys=True))
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("verifier_status") in {"PASS", "FAIL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
