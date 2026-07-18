#!/usr/bin/env python3
"""
Mini-SGLang throughput benchmark.

Uses /generate endpoint (SSE stream) to measure throughput.
mini-sglang only has /generate and /v1/chat/completions — both return SSE.

Output JSON:
  {
    "throughput_tok_per_s": 5026.3,
    "throughput_req_per_s": 39.3,
    "p50_ttft_ms": 95.0,
    "p90_ttft_ms": 101.0,
    "total_output_tokens": 8192,
    "total_time_sec": 1.63,
    "correct": true
  }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time

import httpx


def _make_prompt(input_len: int) -> str:
    """Approx input_len tokens using repeated words."""
    return ("hello world " * input_len).strip()


async def _send_one(
    client: httpx.AsyncClient,
    base_url: str,
    prompt: str,
    max_tokens: int,
) -> tuple[int, float, float]:
    """Send one request via /generate (SSE), collect all chunks.

    Returns (output_tokens, ttft_sec, total_sec).
    /generate SSE format:
      data: <incremental text>\n
      ...
      data: [DONE]\n
    """
    t0 = time.perf_counter()
    ttft = None
    total_chars = 0

    try:
        async with client.stream(
            "POST",
            f"{base_url}/generate",
            json={
                "prompt": prompt,
                "max_tokens": max_tokens,
                "ignore_eos": True,
            },
            timeout=300.0,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                if ttft is None:
                    ttft = time.perf_counter() - t0
                total_chars += len(data)
    except Exception as exc:
        raise exc

    t1 = time.perf_counter()
    # APPROXIMATE token count. The SSE /generate stream gives incremental text, not
    # token-count metadata, so we estimate output tokens as chars/4 (the usual English
    # heuristic). This is a *relative* measure: baseline and candidate are benchmarked
    # with the SAME model, prompts, and tokenizer, so the chars/4 bias cancels in the
    # OG ratio (candidate/baseline). It is NOT an absolute token count and will be
    # systematically biased for other tokenizers / non-English text. Documented in
    # warmup_policy.json ("token_count": "approx chars/4") and the reproduce guide.
    output_tokens = max(1, total_chars // 4) if total_chars > 0 else max_tokens
    return output_tokens, ttft or (t1 - t0), t1 - t0


async def _run_benchmark(
    base_url: str,
    num_prompts: int,
    input_len: int,
    output_len: int,
) -> dict:
    prompt = _make_prompt(input_len)

    # Allow real server-side concurrency: default httpx caps at 100 connections,
    # which would silently throttle high-concurrency pressure tests.
    limits = httpx.Limits(
        max_connections=max(100, num_prompts),
        max_keepalive_connections=max(20, num_prompts),
    )
    async with httpx.AsyncClient(limits=limits) as client:
        t_start = time.perf_counter()
        tasks = [
            _send_one(client, base_url, prompt, output_len)
            for _ in range(num_prompts)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        t_end = time.perf_counter()

    errors = [r for r in results if isinstance(r, Exception)]
    successes = [r for r in results if not isinstance(r, Exception)]

    if not successes:
        return {
            "throughput_tok_per_s": 0.0,
            "throughput_req_per_s": 0.0,
            "p50_ttft_ms": 0.0,
            "p90_ttft_ms": 0.0,
            "total_output_tokens": 0,
            "total_time_sec": t_end - t_start,
            "correct": False,
            "num_success": 0,
            "num_error": len(errors),
            "error": f"{len(errors)} requests failed: {errors[0]}",
        }

    total_tokens = sum(r[0] for r in successes)
    ttfts = sorted(r[1] for r in successes)
    total_time = t_end - t_start

    p50 = statistics.median(ttfts) * 1000
    p90_idx = int(len(ttfts) * 0.9)
    p90 = ttfts[min(p90_idx, len(ttfts) - 1)] * 1000

    return {
        "throughput_tok_per_s": round(total_tokens / total_time, 2),
        "throughput_req_per_s": round(len(successes) / total_time, 2),
        "p50_ttft_ms": round(p50, 1),
        "p90_ttft_ms": round(p90, 1),
        "total_output_tokens": total_tokens,
        "total_time_sec": round(total_time, 2),
        "correct": len(errors) == 0,
        "num_success": len(successes),
        "num_error": len(errors),
    }


def _aggregate_passes(passes: list[dict], warmup: int) -> dict:
    """Combine per-pass results into a steady-state summary.

    The first ``warmup`` passes are excluded from the official throughput
    (cold start: CUDA graph capture + flashinfer JIT + cache fill). The
    reported throughput is the MEDIAN over the measured passes, with min/max
    as a cheap dispersion proxy (Design-5 §12: serving noise needs warmup
    exclusion + repeated measurement).
    """
    measured = passes[warmup:] if warmup < len(passes) else passes[-1:]
    # Defensive .get(): a failed pass (no successful requests) returns a reduced
    # dict — never let one bad pass KeyError-crash the whole eval/harness.
    tputs = [p.get("throughput_tok_per_s", 0.0) for p in measured]
    correct = all(p.get("correct", False) for p in measured) and bool(measured)
    median_tput = round(statistics.median(tputs), 2) if tputs else 0.0
    ttft_p50 = round(statistics.median([p.get("p50_ttft_ms", 0.0) for p in measured]), 1) if measured else 0.0
    # Dispersion + 95% CI over measured passes (Design-5 §12: serving tasks must
    # report confidence intervals, not a single number). CI is half-width of the
    # normal-approx mean interval; with <2 passes it is undefined (0.0).
    mean_tput = round(statistics.fmean(tputs), 2) if tputs else 0.0
    stdev_tput = round(statistics.stdev(tputs), 2) if len(tputs) >= 2 else 0.0
    ci95 = round(1.96 * stdev_tput / (len(tputs) ** 0.5), 2) if len(tputs) >= 2 else 0.0
    return {
        "throughput_tok_per_s": median_tput,
        "throughput_tok_per_s_mean": mean_tput,
        "throughput_tok_per_s_min": round(min(tputs), 2) if tputs else 0.0,
        "throughput_tok_per_s_max": round(max(tputs), 2) if tputs else 0.0,
        "throughput_tok_per_s_stdev": stdev_tput,
        "throughput_tok_per_s_ci95": ci95,  # median ± ci95 ≈ 95% interval of the mean
        "p50_ttft_ms": ttft_p50,
        "correct": correct,
        "num_success": measured[-1].get("num_success", 0) if measured else 0,
        "num_error": measured[-1].get("num_error", 0) if measured else 0,
        "warmup_passes": warmup,
        "measured_passes": len(measured),
        "cold_throughput_tok_per_s": passes[0].get("throughput_tok_per_s", 0.0) if passes else 0.0,
        "per_pass_throughput": [p.get("throughput_tok_per_s", 0.0) for p in passes],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-SGLang throughput benchmark")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=31000)
    parser.add_argument("--num-prompts", type=int, default=64)
    parser.add_argument("--input-len", type=int, default=128)
    parser.add_argument("--output-len", type=int, default=128)
    parser.add_argument("--warmup", type=int, default=0,
                        help="warmup passes to discard before measuring (default 0)")
    parser.add_argument("--repeats", type=int, default=1,
                        help="measured passes after warmup; report median (default 1)")
    parser.add_argument("--output-json", default="/tmp/eval_result.json")
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    total_passes = max(1, args.warmup + args.repeats)
    passes = [
        asyncio.run(
            _run_benchmark(base_url, args.num_prompts, args.input_len, args.output_len)
        )
        for _ in range(total_passes)
    ]

    if args.warmup == 0 and args.repeats == 1:
        result = passes[0]  # backward-compatible single-pass shape
    else:
        result = _aggregate_passes(passes, args.warmup)

    print(json.dumps(result, indent=2))

    os.makedirs(os.path.dirname(os.path.abspath(args.output_json)), exist_ok=True)
    with open(args.output_json, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
