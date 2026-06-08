#!/usr/bin/env python3
"""Collect harness-level trajectory artifacts after the agent step (hidden — /eval).

Parses whichever agent log exists (OpenClaw / OpenCode / ZeroClaw / Claude Code) into
two artifacts required by the MoltBench suitcase (Design-1 §11, §14):

  model_calls.jsonl   one ModelCallRecord per model call (best-effort; fields present
                      only when the agent log exposes them).
  model_cost.json     aggregate {total_model_calls, total_input_tokens,
                      total_output_tokens, source, note} — folded into the verifier's
                      K (cost) vector by eval_runner.py before the official eval.

Token counting honesty: not every harness reports usage. When a log has no usage
metadata we still record the calls we can see and set a `note` documenting the gap,
rather than silently reporting zero as if it were complete (Design-5 §7: no silent caps).

Usage:  collect_artifacts.py <artifacts_dir> <agent_name> [model_cost_out]
"""
from __future__ import annotations

import json
import os
import sys


def _read_lines(path: str) -> list[str]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]
    except Exception:
        return []


def _jsonl(path: str) -> list[dict]:
    out = []
    for ln in _read_lines(path):
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def _from_claude(art: str) -> tuple[list[dict], str]:
    """Claude Code stream-json: assistant events carry message.usage.{input,output}_tokens."""
    calls = []
    for obj in _jsonl(os.path.join(art, "claude_output.jsonl")):
        msg = obj.get("message") or {}
        usage = msg.get("usage") or obj.get("usage") or {}
        if usage or obj.get("type") == "assistant":
            calls.append({
                "model_name": msg.get("model", obj.get("model", "")),
                "provider": "openrouter(via-ccr)",
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "event_type": obj.get("type", ""),
            })
    return calls, "claude stream-json message.usage"


def _from_opencode(art: str) -> tuple[list[dict], str]:
    calls = []
    for obj in _jsonl(os.path.join(art, "opencode_output.jsonl")):
        usage = obj.get("usage") or obj.get("tokens") or {}
        if usage or obj.get("role") == "assistant" or obj.get("type") in ("message", "step"):
            calls.append({
                "model_name": obj.get("model", obj.get("modelID", "")),
                "provider": "openrouter",
                "input_tokens": usage.get("input") or usage.get("input_tokens") or usage.get("prompt_tokens"),
                "output_tokens": usage.get("output") or usage.get("output_tokens") or usage.get("completion_tokens"),
                "event_type": obj.get("type", obj.get("role", "")),
            })
    return calls, "opencode json events (usage if present)"


def _from_openclaw(art: str) -> tuple[list[dict], str]:
    calls = []
    try:
        with open(os.path.join(art, "agent_response.json")) as f:
            resp = json.load(f)
        usages = []
        if isinstance(resp, dict):
            if "usage" in resp:
                usages.append(resp["usage"])
            for v in resp.values():
                if isinstance(v, dict) and "usage" in v:
                    usages.append(v["usage"])
        for u in usages:
            calls.append({
                "model_name": resp.get("model", ""), "provider": "openclaw-gateway",
                "input_tokens": u.get("prompt_tokens") or u.get("input_tokens"),
                "output_tokens": u.get("completion_tokens") or u.get("output_tokens"),
                "event_type": "gateway_response",
            })
    except Exception:
        pass
    return calls, "openclaw agent_response.json usage (if present)"


def _from_zeroclaw(art: str) -> tuple[list[dict], str]:
    # ZeroClaw emits plain text — no structured usage. Record nothing but be explicit.
    n = len(_read_lines(os.path.join(art, "zeroclaw_output.txt")))
    note = ("zeroclaw plain-text output exposes no per-call usage; token cost not "
            f"recoverable from logs (output had {n} lines)")
    return [], note


def main() -> int:
    art = sys.argv[1] if len(sys.argv) > 1 else "/workspace/artifacts"
    agent = (sys.argv[2] if len(sys.argv) > 2 else "none").replace("_", "-")
    out = sys.argv[3] if len(sys.argv) > 3 else os.path.join(art, "model_cost.json")

    dispatch = {
        "claude-code": _from_claude, "opencode": _from_opencode,
        "openclaw": _from_openclaw, "zeroclaw": _from_zeroclaw,
    }
    fn = dispatch.get(agent)
    if fn is None:
        calls, note = [], f"agent '{agent}' has no model-call parser (e.g. smoke/none)"
    else:
        calls, note = fn(art)

    with open(os.path.join(art, "model_calls.jsonl"), "w") as f:
        for c in calls:
            f.write(json.dumps(c) + "\n")

    ti = sum(int(c["input_tokens"]) for c in calls if c.get("input_tokens"))
    to = sum(int(c["output_tokens"]) for c in calls if c.get("output_tokens"))
    summary = {
        "total_model_calls": len(calls),
        "total_input_tokens": ti,
        "total_output_tokens": to,
        "tokens_complete": bool(calls) and all(c.get("output_tokens") is not None for c in calls),
        "source": agent,
        "note": note,
    }
    with open(out, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"[collect_artifacts] {agent}: {len(calls)} model calls, "
          f"{ti}+{to} tokens; {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
