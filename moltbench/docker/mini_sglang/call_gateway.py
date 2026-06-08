#!/usr/bin/env python3
"""Send the optimization task to the OpenClaw gateway and wait for completion.

Called from run_task.sh after the gateway is up.

Usage:
  python3 /eval/call_gateway.py \
    --url http://127.0.0.1:8080 \
    --token OPENCLAW \
    --baseline 5026.3 \
    --max-rounds 10 \
    --difficulty easy \
    --timeout 1800 \
    --output /workspace/artifacts/agent_response.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import httpx


def _served_model_name() -> str:
    """Friendly name of the model mini-SGLang serves (the model being OPTIMIZED), so the
    agent prompt always corresponds to the config and never goes stale when the served
    model is swapped (0.6B -> 4B -> 8B). Pluggable, single source, in priority order:

      1. SERVED_MODEL_NAME env   — explicit config override (cleanest);
      2. derived from MODEL_PATH  — HF / modelscope cache layout.

    NOTE: this is the served/optimized model, NOT OPENAI_MODEL_NAME (the LLM that drives
    the agent harness). They are intentionally different and must not be conflated.
    """
    explicit = os.environ.get("SERVED_MODEL_NAME", "").strip()
    if explicit:
        return explicit
    p = os.environ.get("MODEL_PATH", "")
    # HF cache layout: .../models--Qwen--Qwen3-4B/snapshots/<hash>
    for seg in p.split("/"):
        if seg.startswith("models--"):
            return seg[len("models--"):].split("--")[-1]
    # modelscope layout: .../Qwen/Qwen3-0___6B  (HF uses ___ for a dot)
    tail = p.rstrip("/").split("/")[-1]
    return tail.replace("___", ".") if tail else "the target model"


# ---------------------------------------------------------------------------
# PR context — included in Easy prompt, omitted in Hard prompt
# ---------------------------------------------------------------------------

PR_CONTEXT_EASY = """\
Verified mini-SGLang / SGLang optimization PRs (from real merge records).
Use these as implementation guidance. The percentages below are HISTORICAL reference
results from the original PRs (measured on small models); the actual gain on the model
served here may differ — always trust your own /eval/run_eval measurement, not these
numbers.

1. mini-sglang#97 "Better Estimation Policy" -- ~+15.71% throughput (historical reference)
   Rationale: The default scheduler uses conservative memory estimation
     (sum of remain_len for all in-flight requests). This over-reserves
     KV cache memory, reducing batch size and throughput.
   Optimization: Replace conservative estimation with page-aligned alloc_delta;
     add dynamic new_token_ratio that decays exponentially with decode success
     (0.7 -> 0.098), allowing more aggressive scheduling over time.
   Key files: env.py, scheduler/decode.py, scheduler/prefill.py
   Key changes:
     - env.py: add SCHEDULE_CONSERVATIVENESS, INIT_NEW_TOKEN_RATIO,
               MIN_NEW_TOKEN_RATIO_FACTOR, NEW_TOKEN_RATIO_DECAY_STEPS
     - decode.py: add _EstimatePolicy class, replace inflight_tokens calculation
                  with page-aligned alloc_delta, add on_decode_success() decay
     - prefill.py: PrefillAdder._get_required_size() with page-aligned calc
   Status: NOT implemented in current code -- try this first

2. sglang#7563 "Avoid cudaStreamSync in decode" -- ~+4% (historical reference)
   Rationale: Unnecessary torch.cuda.synchronize() in decode loop causes
     GPU pipeline stall. Removing non-essential sync calls allows GPU/CPU overlap.
   Key files: search for synchronize/cuda_sync calls in engine.py, scheduler.py
   Key change: Remove non-essential torch.cuda.synchronize() calls in decode path
   WARNING: Do NOT remove copy_done.synchronize() in _process_last_data() --
     it is needed for GPU->CPU token copy correctness (all requests will fail).
   WARNING: Do NOT remove self.last_event.synchronize() in fi.py --
     it is needed for FlashInfer buffer safety.
   Status: NOT implemented, try this after PR#97

3. mini-sglang#110 "FlashInfer page_size>1" -- Reduce KV cache fragmentation
   WARNING: Current flashinfer 0.6.3 does NOT support page_size>1 API.
   Do NOT change page_size. It will cause TypeError and crash all requests.
   Status: NOT implementable (requires flashinfer upgrade)

4. sglang#20501 "Fused temperature+softmax sampling" -- 1.09-4.46x sampling speedup
   Rationale: Fuse temperature scaling and softmax into single in-place operation
     to eliminate one kernel launch and memory round-trip.
   Key files: search for temperature / softmax calls in sampler.py or sampling.py
   Note: Check if flashinfer's optimized sampling kernels are already in use.
     If so, manual fusion won't help -- flashinfer's native implementation is already fused.
   Status: NOT implemented (complex, try if other optimizations insufficient)
"""

PR_CONTEXT_HARD = """\
You should explore the mini-SGLang source code to identify performance bottlenecks
and optimization opportunities. Consider areas such as:
  - Memory management and KV cache allocation strategies
  - GPU/CPU synchronization overhead
  - Scheduling policies (prefill admission, decode batching)
  - Sampling pipeline efficiency
  - CUDA kernel launch overhead

Read the source code carefully, form hypotheses about bottlenecks, implement changes,
and verify with /eval/run_eval. Use git to track and revert changes as needed.
"""


def build_system_prompt(baseline: float, max_rounds: int, difficulty: str) -> str:
    if difficulty == "easy":
        pr_section = f"## Reference PRs (Optimization Guidance)\n{PR_CONTEXT_EASY}"
    else:
        pr_section = f"## Optimization Strategy\n{PR_CONTEXT_HARD}"

    return f"""\
You are a systems optimization engineer. Your task is to optimize the mini-SGLang
code in /workspace/repo/ to maximize {_served_model_name()} inference throughput (tokens/second).

## Actions
1. Read and modify any file under /workspace/repo/python/minisgl/
2. Use the bash tool to run: `/eval/run_eval`  (one "attempt")
   - Executable at /eval/run_eval (outside your workspace, use the bash tool only)
   - It restarts the server with your code changes and benchmarks it automatically
   - ~90-120 seconds the first time (model load + flashinfer JIT), faster after
   - Returns normalized JSON: {{"attempt": N, "correct": true/false,
     "score": <OG>, "metric": {{"optimization_gain": <OG>, "candidate_throughput": ...,
     "baseline_throughput": ...}}, "feedback": "...", "error": null}}
   - Read the "feedback" field -- it tells you whether to commit or revert.
   - Do NOT read /eval/ files with file tools -- they are outside your workspace.
3. Use `cd /workspace/repo && git checkout -- .` to revert bad changes

## Current State
- Known baseline throughput: ~{baseline:.1f} tok/s. The official baseline is measured
  in-situ on the original code; your score is OG = your_throughput / baseline.
- Server is NOT running. Call /eval/run_eval first to verify current performance.
- You have {max_rounds} attempts (each /eval/run_eval call = 1 attempt). After the
  budget is exhausted, run_eval refuses and you must stop.
- The first attempt may show lower throughput due to cold start; this is excluded from
  the official steady-state score automatically.

{pr_section}

## Workflow
1. Call /eval/run_eval first (verify server starts, get current OG)
2. Read source code (grep/cat), pick an optimization target
3. Edit code, call /eval/run_eval to verify
4. score>1 + correct=true -> `cd /workspace/repo && git add -A && git commit -m "optimization"`
5. score<=1, correct=false, or error -> MUST revert: `cd /workspace/repo && git checkout -- .`
6. Repeat until attempts exhausted. Report best OG.

IMPORTANT: If run_eval returns correct=false or error, you MUST `git checkout -- .` to revert ALL changes before continuing. Otherwise the next attempt will also fail.

## Constraints
- All dependencies pre-installed (torch, flashinfer, triton, httpx). Do NOT pip install anything.
- Do NOT manually kill or start the server. Only use /eval/run_eval.
- Do NOT modify model weights (/data0/models/) or eval scripts (/eval/).
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--token", default="OPENCLAW")
    parser.add_argument("--baseline", type=float, required=True)
    parser.add_argument("--max-rounds", type=int, default=10)
    parser.add_argument("--difficulty", default="easy", choices=["easy", "hard"])
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--output", default="/workspace/artifacts/agent_response.json")
    parser.add_argument("--emit-prompt", action="store_true",
                        help="print the combined task prompt (system+user) to stdout and exit; "
                             "used by CLI-agent adapters (opencode/zeroclaw/claude-code) that take "
                             "a single prompt string instead of the openclaw gateway.")
    args = parser.parse_args()

    system_prompt = build_system_prompt(args.baseline, args.max_rounds, args.difficulty)
    user_message = (
        f"Known baseline throughput is approximately {args.baseline:.1f} tok/s. "
        "Please call /eval/run_eval first to verify current performance, "
        "then optimize /workspace/repo/python/minisgl/ to maximize throughput. "
        "Call /eval/run_eval after each change to verify. "
        "Report best throughput and improvement ratio."
    )

    # Single source of truth for the task prompt: gateway agents (openclaw) POST it as
    # system+user messages; CLI agents (opencode/zeroclaw/claude-code) capture it here as
    # one string. Keeps every harness on identical task instructions.
    if args.emit_prompt:
        print(system_prompt + "\n\n" + user_message)
        return

    payload = {
        "model": "openclaw",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "stream": False,
        "max_tokens": 32768,
    }

    headers = {
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
    }

    print(f"[call_gateway] Sending task to {args.url}/v1/chat/completions "
          f"(difficulty={args.difficulty}, max_rounds={args.max_rounds}, "
          f"timeout={args.timeout}s)...", flush=True)

    raw_output = ""
    finish_reason = ""
    error = None
    t0 = time.time()

    try:
        with httpx.Client(timeout=float(args.timeout)) as client:
            resp = client.post(
                f"{args.url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            elapsed = time.time() - t0
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                raw_output = msg.get("content") or msg.get("reasoning_content") or ""
                finish_reason = choices[0].get("finish_reason", "")
            error = data.get("error")
    except httpx.TimeoutException:
        print("[call_gateway] Request timed out.", flush=True)
        error = "timeout"
    except Exception as exc:
        print(f"[call_gateway] Request failed: {exc}", flush=True)
        error = str(exc)

    elapsed = time.time() - t0

    result = {
        "raw_output": raw_output,
        "elapsed_sec": round(elapsed, 1),
        "finish_reason": finish_reason,
        "error": error,
        "difficulty": args.difficulty,
    }

    import os
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"[call_gateway] Done in {elapsed:.1f}s. Output saved to {args.output}", flush=True)
    if raw_output:
        print(f"[call_gateway] Agent output preview: {raw_output[:300]}...", flush=True)


if __name__ == "__main__":
    main()
