#!/usr/bin/env python3
"""Probe AIME 2026 streaming behavior through direct vLLM and OpenClaw.

This is a diagnostic for OpenClaw/Podman blank-output failures. It records the
SSE timeline for blank-prone AIME 2026 problems, distinguishing
``reasoning_content`` chunks from visible ``content`` chunks.

Examples:
  python scripts/probe_aime_streaming.py --mode direct --indices 22
  python scripts/probe_aime_streaming.py --mode both --indices 22 --output logs/aime_stream_probe.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

DEFAULT_DIRECT_API_BASE = "http://127.0.0.1:8011/v1"
DEFAULT_MODEL = "Qwen/Qwen3.5-27B"
DEFAULT_GATEWAY_CONFIG = "configs/experiments/podman_ab_aime2026/openclaw_podman.yaml"
DEFAULT_PROBE_INDICES = (22, 23, 25)

SYSTEM_PROMPT = (
    "You are an expert problem solver. When given a problem, actively use your "
    "available tools and skills throughout your reasoning process. Do not attempt "
    "to solve problems purely in your head when tools can help. Use code execution, "
    "search, or any other available capabilities to verify intermediate steps, "
    "explore approaches, and confirm your final answer.\n\n"
    "When you have reached your final answer, you MUST present it in the following "
    "format:\n\n$$\\boxed{your answer here}$$\n\n"
    "Do not skip the boxed format. The boxed answer must appear at the very end of "
    "your response and contain only the final answer, not explanations."
)


def load_aime_problems(indices: list[int]) -> dict[int, dict[str, str]]:
    from datasets import load_dataset

    wanted = set(indices)
    ds = load_dataset("MathArena/aime_2026", split="train")
    out: dict[int, dict[str, str]] = {}
    for row in ds:
        idx = int(row.get("problem_idx"))
        if idx in wanted:
            out[idx] = {
                "problem": str(row["problem"]),
                "answer": str(row.get("answer", "")),
            }
    missing = sorted(wanted - set(out))
    if missing:
        raise RuntimeError(f"AIME 2026 dataset missing problem_idx values: {missing}")
    return out


def direct_payload(problem: str, *, model: str, max_tokens: int) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": problem},
        ],
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": max_tokens,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": True},
    }


def gateway_payload(problem: str, *, agent_config: dict[str, Any], max_tokens: int) -> dict[str, Any]:
    system_prompt = str(agent_config.get("system_prompt") or SYSTEM_PROMPT)
    payload = {
        "model": str(agent_config.get("model") or "openclaw"),
        "messages": [
            {"role": "user", "content": f"{system_prompt}\n\n{problem}"},
        ],
        "temperature": float(agent_config.get("temperature", 0.0)),
        "top_p": float(agent_config.get("top_p", 0.95)),
        "max_tokens": int(agent_config.get("max_tokens") or max_tokens),
        "stream": True,
    }
    if "enable_thinking" in agent_config:
        payload["chat_template_kwargs"] = {
            "enable_thinking": bool(agent_config.get("enable_thinking"))
        }
    return payload


def _iter_sse_data_lines(response: httpx.Response):
    event_lines: list[str] = []
    for line in response.iter_lines():
        if line is None:
            continue
        line = line.strip()
        if not line:
            if event_lines:
                yield "\n".join(event_lines)
                event_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            event_lines.append(line[5:].strip())
    if event_lines:
        yield "\n".join(event_lines)


def stream_probe(
    *,
    label: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    start = time.monotonic()
    last_event_at: float | None = None
    first_event_at: float | None = None
    first_reasoning_at: float | None = None
    first_content_at: float | None = None
    max_gap = 0.0
    event_count = 0
    reasoning_event_count = 0
    content_event_count = 0
    empty_delta_event_count = 0
    done_received = False
    finish_reason = ""
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    timeline: list[dict[str, Any]] = []
    status_code = 0
    response_headers: dict[str, str] = {}
    error = ""

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, read=timeout), trust_env=False) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                status_code = response.status_code
                response_headers = dict(response.headers)
                response.raise_for_status()
                for data in _iter_sse_data_lines(response):
                    now = time.monotonic()
                    elapsed = now - start
                    if last_event_at is not None:
                        max_gap = max(max_gap, now - last_event_at)
                    last_event_at = now
                    if first_event_at is None:
                        first_event_at = elapsed
                    if data == "[DONE]":
                        done_received = True
                        timeline.append({"t": round(elapsed, 3), "done": True})
                        continue
                    try:
                        item = json.loads(data)
                    except json.JSONDecodeError:
                        timeline.append({
                            "t": round(elapsed, 3),
                            "parse_error": True,
                            "raw_prefix": data[:200],
                        })
                        continue

                    event_count += 1
                    choice = (item.get("choices") or [{}])[0]
                    delta = choice.get("delta") or choice.get("message") or {}
                    if not isinstance(delta, dict):
                        delta = {}
                    content = delta.get("content") or ""
                    reasoning = (
                        delta.get("reasoning_content")
                        or delta.get("reasoning")
                        or delta.get("thinking")
                        or ""
                    )
                    if isinstance(content, list):
                        content = "".join(
                            part.get("text", "") if isinstance(part, dict) else str(part)
                            for part in content
                        )
                    if content:
                        content = str(content)
                        content_parts.append(content)
                        content_event_count += 1
                        if first_content_at is None:
                            first_content_at = elapsed
                    if reasoning:
                        reasoning = str(reasoning)
                        reasoning_parts.append(reasoning)
                        reasoning_event_count += 1
                        if first_reasoning_at is None:
                            first_reasoning_at = elapsed
                    if not content and not reasoning:
                        empty_delta_event_count += 1
                    chunk_finish = choice.get("finish_reason")
                    if chunk_finish:
                        finish_reason = str(chunk_finish)
                    timeline.append({
                        "t": round(elapsed, 3),
                        "content_chars": len(content),
                        "reasoning_chars": len(reasoning),
                        "finish_reason": chunk_finish or "",
                    })
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"

    elapsed_total = time.monotonic() - start
    content_text = "".join(content_parts)
    reasoning_text = "".join(reasoning_parts)
    return {
        "label": label,
        "url": url,
        "status_code": status_code,
        "response_content_type": response_headers.get("content-type", ""),
        "elapsed_sec": round(elapsed_total, 3),
        "event_count": event_count,
        "reasoning_event_count": reasoning_event_count,
        "content_event_count": content_event_count,
        "empty_delta_event_count": empty_delta_event_count,
        "done_received": done_received,
        "finish_reason": finish_reason,
        "first_event_sec": round(first_event_at, 3) if first_event_at is not None else None,
        "first_reasoning_sec": round(first_reasoning_at, 3) if first_reasoning_at is not None else None,
        "first_content_sec": round(first_content_at, 3) if first_content_at is not None else None,
        "last_event_sec": round(last_event_at - start, 3) if last_event_at is not None else None,
        "max_gap_sec": round(max_gap, 3),
        "content_len": len(content_text.strip()),
        "reasoning_len": len(reasoning_text.strip()),
        "blank_content": len(content_text.strip()) == 0,
        "has_boxed": "\\boxed{" in content_text,
        "content_tail": content_text.strip()[-240:],
        "reasoning_tail": reasoning_text.strip()[-240:],
        "error": error,
        "timeline": timeline,
    }


class StartedGateway:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.manager = None
        self.agent_config: dict[str, Any] = {}
        self.api_base = ""
        self.token = "OPENCLAW"
        self.metadata: dict[str, Any] = {}

    def __enter__(self) -> "StartedGateway":
        from alphadiana.agent.openclaw_runtime import OpenClawPodmanRuntimeManager

        cfg = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self.agent_config = dict((cfg.get("agent") or {}).get("config") or {})
        self.manager = OpenClawPodmanRuntimeManager(self.agent_config)
        info = self.manager.ensure_ready(None)
        self.api_base = str(info.get("api_base") or "").rstrip("/")
        self.token = str(info.get("gateway_token") or self.agent_config.get("gateway_token") or "OPENCLAW")
        self.metadata = dict(info.get("metadata") or {})
        if not self.api_base:
            raise RuntimeError("OpenClaw Podman gateway did not report api_base")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.manager is not None:
            self.manager.teardown()


def parse_indices(raw: str) -> list[int]:
    indices: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        indices.append(int(part))
    return indices


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("direct", "gateway", "both"), default="direct")
    parser.add_argument("--indices", default=",".join(str(i) for i in DEFAULT_PROBE_INDICES))
    parser.add_argument("--direct-api-base", default=DEFAULT_DIRECT_API_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--gateway-api-base", default="")
    parser.add_argument("--gateway-config", default=DEFAULT_GATEWAY_CONFIG)
    parser.add_argument("--max-tokens", type=int, default=131072)
    parser.add_argument("--timeout", type=float, default=9300.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    indices = parse_indices(args.indices)
    output = Path(args.output) if args.output else Path(
        f"logs/aime_stream_probe_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    problems = load_aime_problems(indices)
    records: list[dict[str, Any]] = []

    gateway_ctx = None
    gateway_config: dict[str, Any] = {}
    gateway_api_base = args.gateway_api_base.rstrip("/")
    gateway_token = "OPENCLAW"
    gateway_metadata: dict[str, Any] = {}
    try:
        if args.mode in {"gateway", "both"} and not gateway_api_base:
            gateway_ctx = StartedGateway(Path(args.gateway_config))
            started = gateway_ctx.__enter__()
            gateway_api_base = started.api_base
            gateway_config = started.agent_config
            gateway_token = started.token
            gateway_metadata = dict(started.metadata)
        elif args.mode in {"gateway", "both"}:
            cfg = yaml.safe_load(Path(args.gateway_config).read_text(encoding="utf-8")) or {}
            gateway_config = dict((cfg.get("agent") or {}).get("config") or {})

        for idx in indices:
            problem = problems[idx]["problem"]
            answer = problems[idx]["answer"]
            if args.mode in {"direct", "both"}:
                print(f"[direct] aime_{idx} streaming probe...", flush=True)
                rec = stream_probe(
                    label=f"direct/aime_{idx}",
                    url=f"{args.direct_api_base.rstrip('/')}/chat/completions",
                    headers={"Content-Type": "application/json"},
                    payload=direct_payload(problem, model=args.model, max_tokens=args.max_tokens),
                    timeout=args.timeout,
                )
                rec.update({"problem_idx": idx, "answer": answer, "mode": "direct"})
                records.append(rec)
                print(
                    f"  done: elapsed={rec['elapsed_sec']}s events={rec['event_count']} "
                    f"reasoning={rec['reasoning_event_count']} content={rec['content_event_count']} "
                    f"first_reasoning={rec['first_reasoning_sec']} first_content={rec['first_content_sec']} "
                    f"blank={rec['blank_content']} error={rec['error'] or '-'}",
                    flush=True,
                )
            if args.mode in {"gateway", "both"}:
                print(f"[gateway] aime_{idx} streaming probe via {gateway_api_base}...", flush=True)
                rec = stream_probe(
                    label=f"openclaw-podman/aime_{idx}",
                    url=f"{gateway_api_base.rstrip('/')}/chat/completions",
                    headers={
                        "Authorization": f"bearer {gateway_token}",
                        "Content-Type": "application/json",
                    },
                    payload=gateway_payload(
                        problem,
                        agent_config=gateway_config,
                        max_tokens=args.max_tokens,
                    ),
                    timeout=args.timeout,
                )
                rec.update({
                    "problem_idx": idx,
                    "answer": answer,
                    "mode": "gateway",
                    "gateway_metadata": gateway_metadata,
                })
                records.append(rec)
                print(
                    f"  done: elapsed={rec['elapsed_sec']}s events={rec['event_count']} "
                    f"reasoning={rec['reasoning_event_count']} content={rec['content_event_count']} "
                    f"first_reasoning={rec['first_reasoning_sec']} first_content={rec['first_content_sec']} "
                    f"blank={rec['blank_content']} error={rec['error'] or '-'}",
                    flush=True,
                )
    finally:
        if gateway_ctx is not None:
            gateway_ctx.__exit__(None, None, None)

    summary = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "indices": indices,
        "mode": args.mode,
        "direct_api_base": args.direct_api_base,
        "gateway_api_base": gateway_api_base,
        "records": records,
    }
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Wrote {output}")
    return 1 if any(r.get("error") for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
