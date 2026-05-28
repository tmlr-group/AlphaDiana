#!/usr/bin/env python3
"""Diagnose the OpenClaw-Podman blank-output failures (AIME 2026 / Qwen3.5-27B).

Hits the vLLM endpoint DIRECTLY (no OpenClaw harness, no logprob proxy) to
isolate whether blank `content` comes from the model itself or from the
harness/proxy layer.

For each probe problem it runs two requests — thinking ON and thinking OFF —
with the same generation params the A/B configs use, and reports:
  finish_reason, len(content), len(reasoning_content), completion_tokens, elapsed.

A blank == content empty/whitespace. If direct-vLLM thinking-ON also blanks,
the root cause is the model/server, not the Podman integration.

Usage:
  python scripts/diagnose_blank_outputs.py
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import time
import urllib.request

API = "http://127.0.0.1:8011/v1/chat/completions"
MODEL = "Qwen/Qwen3.5-27B"

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

# AIME 2026 problem_idx values to probe: 1 passed the smoke; 22/23/25 blanked.
PROBE_IDX = [1, 22, 23, 25]
MAX_TOKENS = 131072


def load_problems() -> dict[int, dict]:
    from datasets import load_dataset
    ds = load_dataset("MathArena/aime_2026", split="train")
    out = {}
    for row in ds:
        idx = int(row.get("problem_idx"))
        if idx in PROBE_IDX:
            out[idx] = {"problem": row["problem"], "answer": str(row.get("answer"))}
    return out


def probe(idx: int, problem: str, answer: str, thinking: bool) -> dict:
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": problem},
        ],
        "temperature": 0.0,
        "top_p": 0.95,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": thinking},
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(API, data=data, headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=9300) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        return {"idx": idx, "thinking": thinking, "error": f"{type(exc).__name__}: {exc}",
                "elapsed": time.time() - t0}
    elapsed = time.time() - t0
    choice = (payload.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    usage = payload.get("usage") or {}
    boxed = "\\boxed{" in content
    return {
        "idx": idx, "thinking": thinking, "answer": answer,
        "finish_reason": choice.get("finish_reason"),
        "content_len": len(content.strip()),
        "reasoning_len": len(reasoning.strip()),
        "completion_tokens": usage.get("completion_tokens"),
        "blank": len(content.strip()) == 0,
        "has_boxed": boxed,
        "elapsed": round(elapsed, 1),
        "content_tail": content.strip()[-160:],
    }


def main() -> int:
    print(f"Loading AIME 2026 probe problems {PROBE_IDX} ...")
    probs = load_problems()
    jobs = [(idx, probs[idx]["problem"], probs[idx]["answer"], think)
            for idx in PROBE_IDX if idx in probs
            for think in (True, False)]
    print(f"Running {len(jobs)} direct-vLLM probes (max_tokens={MAX_TOKENS}, temp=0)...\n")

    results = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(probe, *j): j for j in jobs}
        for fut in cf.as_completed(futs):
            r = fut.result()
            results.append(r)
            tag = f"aime_{r['idx']} think={'ON ' if r['thinking'] else 'OFF'}"
            if r.get("error"):
                print(f"  {tag}  ERROR {r['error']}")
            else:
                print(f"  {tag}  finish={r['finish_reason']:<8} "
                      f"content_len={r['content_len']:<6} reasoning_len={r['reasoning_len']:<7} "
                      f"compl_tok={r['completion_tokens']} boxed={r['has_boxed']} "
                      f"blank={r['blank']} {r['elapsed']}s")

    print("\n=== SUMMARY ===")
    for think in (True, False):
        sub = [r for r in results if r.get("thinking") is think and not r.get("error")]
        blanks = sum(1 for r in sub if r["blank"])
        print(f"  thinking={'ON' if think else 'OFF'}: {blanks}/{len(sub)} blank, "
              f"{sum(1 for r in sub if r['has_boxed'])}/{len(sub)} produced \\boxed answer")
    errs = [r for r in results if r.get("error")]
    if errs:
        print(f"  errors: {len(errs)}")
    print()
    with open("logs/diagnose_blank_outputs.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Full records -> logs/diagnose_blank_outputs.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
