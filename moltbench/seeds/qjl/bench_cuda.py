#!/usr/bin/env python3
"""
MoltBench evaluation for QJL KV-cache quantization.

Two tensor modes:
  real      --kv-path /kv_cache/qk.pt  (generated once by prepare_kv.py)
  synthetic  no --kv-path or file absent (random Gaussian Q/K, fast fallback)

Regardless of mode, outputs a single JSON object to stdout.
Exit code 0 iff correctness_pass is True.

Typical agent loop:
    # edit qjl_kernel/csrc/*.cu or bench args, then:
    cd /workspace/repo/qjl_kernel && python setup.py build_ext --inplace
    python bench_cuda.py --kv-path /kv_cache/qk.pt --key-quant-bits 128
"""

import argparse
import json
import os
import sys

import numpy as np
import torch

sys.path.insert(0, "/workspace/repo")
from models.llama2_utils_qjl import QJLKeyQuantizer, QJLSketch


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="QJL benchmark")

    # --- tensor source ---
    p.add_argument("--kv-path", type=str, default="/kv_cache/qk.pt",
                   help="Path to pre-captured Q/K file from prepare_kv.py. "
                        "Falls back to synthetic tensors if file is absent.")

    # --- QJL config (seed defaults are deliberately sub-optimal) ---
    p.add_argument("--key-quant-bits", type=int, default=256,
                   help="Projection dimension for QJL sketch (seed: 256, optimal: 128-160)")
    p.add_argument("--outlier-count", type=int, default=8,
                   help="Number of outlier dimensions per group")
    p.add_argument("--group-size", type=int, default=32,
                   help="Group size for quantization")
    p.add_argument("--buffer-size", type=int, default=128,
                   help="Buffer size for streaming updates")

    # --- synthetic-mode tensor geometry (ignored when real file is loaded) ---
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--seq-len", type=int, default=2048)
    p.add_argument("--n-heads", type=int, default=32)
    p.add_argument("--head-dim", type=int, default=128)

    # --- benchmark control ---
    p.add_argument("--warmup", type=int, default=5)
    p.add_argument("--repeats", type=int, default=20)
    p.add_argument("--distortion-threshold", type=float, default=0.8)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--dtype", choices=["float16", "bfloat16"], default="bfloat16")

    return p.parse_args()


# ---------------------------------------------------------------------------
# Tensor loading
# ---------------------------------------------------------------------------

def load_real_qk(kv_path, device, dtype):
    """
    Load Q/K tensors saved by prepare_kv.py.

    Returns:
        Q    : [B, H, 1, D]  – last-token query
        K    : [B, H, S, D]  – full-sequence keys
        meta : dict with capture metadata
    """
    data = torch.load(kv_path, map_location="cpu", weights_only=True)
    Q = data["query"].to(device=device, dtype=dtype)
    K = data["key"].to(device=device, dtype=dtype)
    meta = data.get("meta", {})
    return Q, K, meta


def make_synthetic_qk(args, device, dtype):
    """Generate random Gaussian Q/K (fast fallback)."""
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)
    B, H, S, D = args.batch_size, args.n_heads, args.seq_len, args.head_dim
    K = torch.randn(B, H, S, D, dtype=dtype, device=device)
    Q = torch.randn(B, H, 1, D, dtype=dtype, device=device)
    return Q, K, {}


# ---------------------------------------------------------------------------
# Core benchmark
# ---------------------------------------------------------------------------

def run_once(sketch, K, Q, args):
    """One quantize-then-score pass.  Returns approximate attention scores."""
    quantizer = QJLKeyQuantizer(
        sketch,
        args.outlier_count,
        args.buffer_size,
        args.group_size,
        args.key_quant_bits,
    )
    quantizer.build_sketch(K)
    return quantizer.attention_score(Q)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    dtype = torch.bfloat16 if args.dtype == "bfloat16" else torch.float16
    device = torch.device("cuda")

    # ---- load or generate Q/K ----
    if os.path.exists(args.kv_path):
        Q, K, meta = load_real_qk(args.kv_path, device, dtype)
        tensor_source = "real"
        print(f"[bench] Loaded real Q/K from {args.kv_path}", file=sys.stderr)
    else:
        Q, K, meta = make_synthetic_qk(args, device, dtype)
        tensor_source = "synthetic"
        if args.kv_path != "/kv_cache/qk.pt":
            print(f"[bench] WARNING: {args.kv_path} not found, using synthetic tensors",
                  file=sys.stderr)

    B, H, _, D = Q.shape
    Hk = K.shape[1]
    S = K.shape[2]

    # GQA: expand K so it matches Q's head count (repeat each KV head H//Hk times)
    if Hk != H:
        if H % Hk != 0:
            raise ValueError(f"num_heads ({H}) must be divisible by num_kv_heads ({Hk})")
        K = K.repeat_interleave(H // Hk, dim=1)  # [B, H, S, D]

    # Exact attention scores (ground truth)
    exact = torch.matmul(Q, K.transpose(-1, -2))  # [B, H, 1, S]

    # ---- build QJL sketch (one-time, not timed) ----
    gen = torch.Generator(device=device)
    gen.manual_seed(args.seed)
    sketch = QJLSketch(
        dim=(D, args.key_quant_bits),
        dim_outlier=args.key_quant_bits,
        device=device,
        rng=gen,
        rot=True,
        rht=False,
    )

    # ---- warmup ----
    for _ in range(args.warmup):
        run_once(sketch, K, Q, args)
        torch.cuda.synchronize()

    # ---- timed runs ----
    times_ms = []
    last_scores = None
    for _ in range(args.repeats):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)

        start.record()
        scores = run_once(sketch, K, Q, args)
        end.record()
        torch.cuda.synchronize()

        times_ms.append(start.elapsed_time(end))
        last_scores = scores

    # ---- metrics ----
    distortion = (
        torch.linalg.norm(last_scores - exact) / torch.linalg.norm(exact)
    ).item()

    t = np.array(times_ms)
    runtime_ms = float(np.median(t))
    correctness_pass = distortion < args.distortion_threshold

    result = {
        "runtime_ms": round(runtime_ms, 3),
        "distortion": round(distortion, 6),
        "correctness_pass": correctness_pass,
        "tensor_source": tensor_source,
        "config": {
            "key_quant_bits": args.key_quant_bits,
            "outlier_count": args.outlier_count,
            "group_size": args.group_size,
            "buffer_size": args.buffer_size,
            "batch_size": B,
            "seq_len": S,
            "n_heads": H,
            "head_dim": D,
            "dtype": args.dtype,
        },
        "capture_meta": meta,
        "stats": {
            "mean_ms": round(float(np.mean(t)), 3),
            "std_ms": round(float(np.std(t)), 3),
            "min_ms": round(float(np.min(t)), 3),
            "max_ms": round(float(np.max(t)), 3),
            "median_ms": round(runtime_ms, 3),
            "warmup_runs": args.warmup,
            "timed_runs": args.repeats,
        },
    }

    # Single JSON line to stdout (MoltBench contract)
    print(json.dumps(result))
    sys.exit(0 if correctness_pass else 1)


if __name__ == "__main__":
    main()
