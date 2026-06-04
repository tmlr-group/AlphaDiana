
#!/usr/bin/env python3
"""
One-time capture of real Q/K tensors from a LLaMA-2 model.

Loads the model, runs a fixed long-context prompt, hooks into the specified
attention layer's q_proj / k_proj to capture the raw (pre-RoPE) projections,
reshapes them to [B, H, S, D], and saves to a .pt file.

bench_cuda.py will load that file on every subsequent run – no model needed.

Usage (inside Docker, model mounted at /model):
    python prepare_kv.py --model-path /model --output /kv_cache/qk.pt

Key options:
    --layer-idx   Which transformer layer to capture (default: 16, middle of 32)
    --seq-len     Max tokens to feed (default: 2048)
    --dtype       bfloat16 (default) | float16
"""

import argparse
import os
import sys

import torch
from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer

# ---------------------------------------------------------------------------
# Default long-context prompt (~2 000 LLaMA-2 tokens when truncated to 2048).
# Replace with domain-relevant text for more realistic K distributions.
# ---------------------------------------------------------------------------
DEFAULT_PROMPT = """\
The attention mechanism, introduced in "Attention Is All You Need" by Vaswani \
et al. (2017), fundamentally changed the landscape of deep learning by allowing \
models to weigh the importance of different parts of an input sequence when \
producing each element of the output. Unlike recurrent neural networks, which \
process tokens sequentially and are limited by fixed-length hidden states, \
attention computes pairwise interactions between all positions simultaneously. \
This parallelism enables efficient training on modern accelerators and facilitates \
the capture of long-range dependencies that recurrent models struggle with.

The core operation is the scaled dot-product attention: given queries Q, keys K, \
and values V derived from the input via learned projections, the output is \
computed as softmax(QK^T / sqrt(d_k)) V, where d_k is the head dimension. \
Multi-head attention repeats this operation H times with independent projections, \
allowing the model to attend to information from different representation subspaces \
at different positions. The outputs of all heads are concatenated and projected \
back to the model dimension.

Transformer-based large language models such as GPT, LLaMA, and PaLM stack many \
such attention layers with interleaved feed-forward networks and layer normalisation. \
During autoregressive generation, the model computes key and value projections for \
every previously generated token – collectively known as the KV cache – and reuses \
them at each subsequent step without recomputation. For long contexts or large batch \
sizes, the KV cache dominates memory consumption: a single LLaMA-2-7B layer stores \
two tensors of shape [batch, heads, seq_len, head_dim] in bfloat16, totalling \
roughly 2 * 32 * 4096 * 128 * 2 bytes ≈ 64 MB per layer, or ~2 GB for all 32 layers \
at a sequence length of 4096 with batch size 1.

Quantisation of the KV cache is a natural approach to reducing this memory footprint. \
Naive uniform quantisation to INT4 or INT8 is straightforward but loses information \
when token representations contain large-magnitude outlier channels – a well-documented \
property of transformer hidden states. Structured approaches such as SmoothQuant and \
LLM.int8() identify and handle outliers explicitly, while learned codebook methods \
(e.g., KVQuant, ZipCache) train separate quantisers for different layers.

The Quantised JL Transform (QJL) takes a different approach rooted in randomised \
linear algebra. Rather than quantising each element independently, QJL first applies \
a random linear projection (the Johnson-Lindenstrauss sketch) to each key vector, \
mapping it from R^d to R^m where m << d, and then binarises the projected vector. \
The key insight is that the sign pattern of the projected vector carries enough \
geometric information to estimate inner products: by the Johnson-Lindenstrauss lemma, \
distances and angles are approximately preserved under random projections with \
high probability when m = O(log(n) / epsilon^2).

Concretely, for a query q and a quantised key tilde_k = sign(W k), the approximate \
attention logit is estimated as (pi/2) ||q|| ||k|| cos(theta_hat), where theta_hat is \
the angle estimated from the Hamming distance between sign(W q) and sign(W k). \
Outlier channels are handled separately: the top-r channels by magnitude are \
identified per group and their projections are stored at higher precision, while the \
remaining inlier channels use the binary sketch. This hybrid scheme achieves low \
distortion across diverse layers and sequence positions with a small number of bits.

The CUDA implementation of QJL consists of four custom kernels: qjl_quant computes \
the JL projection and packs the binary sketch; qjl_score evaluates the approximate \
inner product from the packed sketch; qjl_gqa_score extends this to grouped-query \
attention; and quantization handles the value cache with asymmetric group-wise \
INT2/INT4 quantisation. The kernels are optimised for A100 / A800 GPUs (compute \
capability 8.0) and use half-precision matrix multiply units, shared memory tiling, \
and warp-level reduction to maximise throughput.

Benchmarking QJL accurately requires realistic key tensors. Synthetic Gaussian keys \
underestimate distortion because they lack the heavy-tailed channel distributions \
present in real transformer activations. Key tensors captured from actual model \
inference – where certain head dimensions consistently produce large activations \
across many tokens – create the sparse, peaky structures that challenge purely \
uniform quantisation schemes and highlight the advantage of QJL's outlier-aware design.

This file was generated by prepare_kv.py as a default long-context seed prompt. \
For best results, replace it with a representative sample from your target domain, \
such as a scientific article, legal document, or code repository. The only requirement \
is that the tokenised length reaches the desired seq_len so that the KV cache is \
fully populated before capture. Padding tokens should be avoided; real text tokens \
produce more natural activation patterns.

Reproduction note: the captured tensors are deterministic given the same model \
weights, prompt, and floating-point mode. Store them alongside the model commit \
hash and the capture configuration (layer index, dtype, seq_len) to ensure that \
baseline and optimised measurements use identical inputs.
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Capture real Q/K tensors from LLaMA-2")
    p.add_argument("--model-path", required=True,
                   help="HuggingFace-format LLaMA-2 directory (mounted at /model)")
    p.add_argument("--output", default="/kv_cache/qk.pt",
                   help="Destination .pt file (default: /kv_cache/qk.pt)")
    p.add_argument("--layer-idx", type=int, default=16,
                   help="Attention layer index to capture (default: 16)")
    p.add_argument("--seq-len", type=int, default=2048,
                   help="Max tokens to feed (default: 2048)")
    p.add_argument("--dtype", choices=["bfloat16", "float16"], default="bfloat16")
    p.add_argument("--prompt", type=str, default=None,
                   help="Override default prompt (inline string)")
    p.add_argument("--prompt-file", type=str, default=None,
                   help="Override default prompt (path to .txt file)")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Capture logic
# ---------------------------------------------------------------------------

def capture_qk(model_path, layer_idx, prompt, seq_len, dtype):
    """
    Load model, register forward hooks on q_proj/k_proj of *layer_idx*,
    run one forward pass, and return (query, key) where:
        query : [1, H, 1, D]   – last-token query (pre-RoPE)
        key   : [1, H, S, D]   – full-sequence keys (pre-RoPE)
    """
    print(f"[prepare_kv] Loading tokenizer from {model_path} ...", file=sys.stderr)
    tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True)

    print(f"[prepare_kv] Loading model (dtype={dtype}, device_map=auto) ...",
          file=sys.stderr)
    torch_dtype = torch.bfloat16 if dtype == "bfloat16" else torch.float16
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        torch_dtype=torch_dtype,
        device_map="auto",
        # 'eager' bypasses SDPA / flash-attn so forward hooks reach q_proj / k_proj
        attn_implementation="eager",
    )
    model.eval()

    cfg = model.config
    num_heads = cfg.num_attention_heads
    num_kv_heads = getattr(cfg, "num_key_value_heads", num_heads)
    head_dim = cfg.hidden_size // num_heads
    print(f"[prepare_kv] num_heads={num_heads}, num_kv_heads={num_kv_heads}, "
          f"head_dim={head_dim}, capturing layer {layer_idx}", file=sys.stderr)

    # ---- register hooks ----
    captured = {}
    attn = model.model.layers[layer_idx].self_attn

    def _q_hook(module, inp, out):
        captured["q"] = out.detach()

    def _k_hook(module, inp, out):
        captured["k"] = out.detach()

    h_q = attn.q_proj.register_forward_hook(_q_hook)
    h_k = attn.k_proj.register_forward_hook(_k_hook)

    # ---- tokenise ----
    first_device = next(model.parameters()).device
    inputs = tokenizer(
        prompt,
        return_tensors="pt",
        truncation=True,
        max_length=seq_len,
        add_special_tokens=True,
    ).to(first_device)
    actual_len = inputs["input_ids"].shape[1]
    print(f"[prepare_kv] Prompt tokenised to {actual_len} tokens", file=sys.stderr)

    # ---- forward pass ----
    with torch.no_grad():
        _ = model(**inputs)

    h_q.remove()
    h_k.remove()

    # ---- reshape [B, S, H*D] → [B, H, S, D] ----
    q_raw = captured["q"].cpu()   # [1, S, num_heads * head_dim]
    k_raw = captured["k"].cpu()   # [1, S, num_kv_heads * head_dim]  (GQA: fewer KV heads)

    q = q_raw.view(1, actual_len, num_heads, head_dim).transpose(1, 2).contiguous()
    k = k_raw.view(1, actual_len, num_kv_heads, head_dim).transpose(1, 2).contiguous()

    # Last-token query: [1, H, 1, D]
    query = q[:, :, -1:, :]

    del model
    torch.cuda.empty_cache()

    return query, k, {"num_heads": num_heads, "num_kv_heads": num_kv_heads,
                      "head_dim": head_dim, "seq_len": actual_len,
                      "layer_idx": layer_idx, "dtype": dtype,
                      "model_path": model_path}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    # Resolve prompt
    if args.prompt_file:
        with open(args.prompt_file) as f:
            prompt = f.read()
    elif args.prompt:
        prompt = args.prompt
    else:
        prompt = DEFAULT_PROMPT

    query, key, meta = capture_qk(
        args.model_path, args.layer_idx, prompt, args.seq_len, args.dtype
    )

    print(f"[prepare_kv] Captured: query={tuple(query.shape)}, "
          f"key={tuple(key.shape)}", file=sys.stderr)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    torch.save({"query": query, "key": key, "meta": meta}, args.output)
    print(f"[prepare_kv] Saved → {args.output}", file=sys.stderr)
    print(f"[prepare_kv] meta: {meta}", file=sys.stderr)


if __name__ == "__main__":
    main()
