#!/usr/bin/env python3
"""Host-controlled QJL attempt verifier service."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from uuid import uuid4
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import evaluate_candidate_isolated

ARTIFACTS = Path(os.environ.get("QJL_ARTIFACTS_DIR", "/artifacts"))
MAX_ATTEMPTS = int(os.environ.get("QJL_MAX_ATTEMPTS", "5"))
PORT = int(os.environ.get("QJL_VERIFIER_PORT", "8081"))
TOKEN = os.environ["QJL_RUN_EVAL_TOKEN"]
LOCK = threading.Lock()
EVAL_LOCK = threading.Lock()
STARTED = time.monotonic()
TOTAL_TIMEOUT_SEC = int(os.environ.get("QJL_TOTAL_TIMEOUT_SEC", "3600"))
EXECUTION_ID = os.environ.get("QJL_EXECUTION_ID", "unknown")
BASELINE_RESULT: dict = {}
BASELINE_ERROR = ""


def append_jsonl(name: str, payload: dict) -> None:
    with (ARTIFACTS / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def event(event_type: str, **payload: object) -> None:
    attempt_id = str(payload.pop("attempt_id", ""))
    timestamp = datetime.now(timezone.utc).isoformat()
    append_jsonl(
        "events.jsonl",
        {
            "event_id": uuid4().hex,
            "run_id": EXECUTION_ID,
            "episode_id": EXECUTION_ID,
            "stage_id": "stage_02_improvement",
            "attempt_id": attempt_id,
            "agent_id": "qjl-verifier",
            "event_type": event_type,
            "timestamp": timestamp,
            "payload": payload,
            "content_hash": hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "parent_event_id": None,
            "visibility": "host",
        },
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "MoltBenchQJLVerifier/1"

    def _json(self, status: int, payload: dict) -> None:
        encoded = json.dumps(payload, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            if BASELINE_ERROR:
                self._json(503, {"status": "error", "error": BASELINE_ERROR})
            else:
                self._json(200, {"status": "ok", "baseline": BASELINE_RESULT})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/eval/run_eval":
            self._json(404, {"error": "not_found"})
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._json(403, {"error": "forbidden"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 32 * 1024 * 1024:
                self._json(413, {"error": "candidate_payload_too_large"})
                return
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "invalid_json"})
            return

        if time.monotonic() - STARTED >= TOTAL_TIMEOUT_SEC:
            self._json(429, {"error": "episode_time_budget_exhausted"})
            return
        if not EVAL_LOCK.acquire(blocking=False):
            self._json(409, {"error": "attempt_in_progress"})
            return

        with LOCK:
            state_path = ARTIFACTS / "attempt_state.json"
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
            else:
                state = {"attempts": 0, "best_gain": 0.0}
            if state["attempts"] >= MAX_ATTEMPTS:
                EVAL_LOCK.release()
                self._json(429, {"error": "attempt_budget_exhausted"})
                return
            state["attempts"] += 1
            attempt = state["attempts"]
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

        label = f"attempt_{attempt:03d}"
        try:
            result = evaluate_candidate_isolated(
                payload,
                artifact_dir=ARTIFACTS / "logs",
                label=label,
            )
        finally:
            EVAL_LOCK.release()
        full_result = {
            **result,
            "attempt": attempt,
            "attempt_id": label,
            "stage_id": "stage_02_improvement",
        }
        append_jsonl(
            "attempts.jsonl",
            {
                "attempt": attempt,
                "candidate_files": sorted(payload.get("files", {})),
            },
        )
        append_jsonl("verifier_results.jsonl", full_result)
        event("run_eval_call", attempt_id=label)
        event("verifier_result", attempt_id=label, result=result)
        event(
            "budget_event",
            attempts_used=attempt,
            attempts_limit=MAX_ATTEMPTS,
        )

        gain = float(result.get("metrics", {}).get("gain", 0.0))
        if bool(result.get("correct")):
            with LOCK:
                current = json.loads(state_path.read_text(encoding="utf-8"))
                if gain > float(current.get("best_gain", 0.0)):
                    current["best_gain"] = gain
                    state_path.write_text(
                        json.dumps(current, indent=2),
                        encoding="utf-8",
                    )
                    best = ARTIFACTS / "best_workspace_snapshot"
                    shutil.rmtree(best, ignore_errors=True)
                    best.mkdir(parents=True)
                    for relative, content in payload.get("files", {}).items():
                        target = best / Path(relative).name
                        target.write_text(content, encoding="utf-8")

        feedback = {
            "attempt": attempt,
            "attempt_id": label,
            "stage_id": "stage_02_improvement",
            "correct": bool(result.get("correct")),
            "score": float(result.get("score", 0.0)),
            "metrics": result.get("metrics", {}),
            "wall_time_sec": result.get("wall_time_sec", 0.0),
            "verifier_status": result.get("verifier_status", "FAIL"),
            "failure_reason": result.get("failure_reason"),
            "feedback": (
                "candidate accepted"
                if result.get("correct")
                else "candidate rejected"
            ),
        }
        self._json(200, feedback)

    def log_message(self, fmt: str, *args: object) -> None:
        append_jsonl("service_access.jsonl", {"message": fmt % args})


def main() -> None:
    global BASELINE_ERROR, BASELINE_RESULT
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    seed_snapshot = ARTIFACTS / "initial_workspace_snapshot"
    if not any(seed_snapshot.glob("*.cu")):
        shutil.rmtree(seed_snapshot, ignore_errors=True)
        shutil.copytree("/opt/qjl-seed/qjl_kernel/csrc", seed_snapshot)
    best_snapshot = ARTIFACTS / "best_workspace_snapshot"
    if not any(best_snapshot.glob("*.cu")):
        shutil.rmtree(best_snapshot, ignore_errors=True)
        shutil.copytree(seed_snapshot, best_snapshot)
    for name in ("attempts.jsonl", "events.jsonl", "verifier_results.jsonl"):
        (ARTIFACTS / name).touch(exist_ok=True)
    seed_candidate = json.loads(
        Path("/verifier/seed_candidate.json").read_text(encoding="utf-8")
    )
    BASELINE_RESULT = evaluate_candidate_isolated(
        seed_candidate,
        artifact_dir=ARTIFACTS / "logs",
        label="baseline",
    )
    (ARTIFACTS / "baseline_eval.json").write_text(
        json.dumps(BASELINE_RESULT, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if not BASELINE_RESULT.get("correct"):
        BASELINE_ERROR = str(
            BASELINE_RESULT.get("failure_reason") or "same-seed baseline failed"
        )
    else:
        os.environ["QJL_BASELINE_RUNTIME_MS"] = str(
            BASELINE_RESULT["metrics"]["runtime_ms"]
        )
        with LOCK:
            state_path = ARTIFACTS / "attempt_state.json"
            state_path.write_text(
                json.dumps({"attempts": 0, "best_gain": 1.0}, indent=2),
                encoding="utf-8",
            )
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
