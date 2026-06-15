# mini-SGLang Optimization Catalog

> Verified optimization techniques from real SGLang / mini-SGLang PRs.
> Each entry includes: rationale, key files, implementation guidance, and expected impact.
> Used as reference material for MoltBench Easy/Hard task prompts.

---

## 1. Better Estimation Policy (mini-sglang#97)

| Field | Detail |
|-------|--------|
| **Source** | [mini-sglang#97](https://github.com/sgl-project/mini-sglang/pull/97) |
| **Impact** | +15.71% throughput (64 concurrent, Qwen3-0.6B on A800) |
| **Difficulty** | Medium |
| **Status in base image** | NOT implemented (reverted to original for MoltBench) |

### Rationale

The default scheduler uses a conservative memory estimation: `sum(remain_len)` for all
in-flight requests. This over-reserves KV cache memory, reducing the number of requests
that can be batched together, which directly limits throughput.

The optimization replaces this with a **page-aligned estimation** using `alloc_delta`
(the actual number of new pages needed), and introduces a **dynamic `new_token_ratio`**
that exponentially decays as decode rounds succeed (from 0.7 down to ~0.098). This
allows the scheduler to pack more requests per batch when memory pressure is low.

### Key Files and Changes

**`python/minisgl/env.py`** — Add configuration constants:
```python
SCHEDULE_CONSERVATIVENESS = 1.0    # Controls how aggressive scheduling is
INIT_NEW_TOKEN_RATIO = 0.7        # Initial estimate of tokens-to-generate ratio
MIN_NEW_TOKEN_RATIO_FACTOR = 0.14 # Floor: ratio won't decay below INIT * 0.14 = 0.098
NEW_TOKEN_RATIO_DECAY_STEPS = 600 # How many successful decodes before min ratio
CLIP_MAX_NEW_TOKENS = 4096        # Safety cap on estimated new tokens per request
```

**`python/minisgl/scheduler/decode.py`** — Core scheduling logic:
- Add `_EstimatePolicy` class that tracks `new_token_ratio` across rounds
- Replace conservative `inflight_tokens = sum(remain_len)` with page-aligned `alloc_delta`
- Add `on_decode_success()` callback: each successful decode step reduces `new_token_ratio`
  by a decay factor, allowing more aggressive scheduling over time
- The ratio decays exponentially: `ratio *= decay_factor` per successful step

**`python/minisgl/scheduler/prefill.py`** — Prefill admission:
- Modify `PrefillAdder._get_required_size()` to use page-aligned memory calculation
- Use dynamic `new_token_ratio` for estimating output length of new requests
- This allows more requests to be admitted into the prefill batch

### Implementation Guidance

1. First read `env.py` and add the configuration constants
2. Read `scheduler/decode.py`, understand the current `inflight_tokens` calculation
3. Create `_EstimatePolicy` class with `new_token_ratio` tracking
4. Modify the decode scheduling loop to use `alloc_delta` instead of `sum(remain_len)`
5. Add decay logic: after each successful decode batch, reduce `new_token_ratio`
6. Update `scheduler/prefill.py` to use the new ratio for admission control
7. Test with `/eval/run_eval` — expect +10-16% throughput improvement

### Verification

Before: ~4,813 tok/s (64 concurrent, input=128, output=128, Qwen3-0.6B on A800)
After:  ~5,026 tok/s (+4.4% in this config; up to +15.71% in other workloads)

---

## 2. Avoid cudaStreamSync in Decode (sglang#7563)

| Field | Detail |
|-------|--------|
| **Source** | [sglang#7563](https://github.com/sgl-project/sglang/pull/7563) |
| **Impact** | +4% throughput (Qwen3-0.6B) |
| **Difficulty** | Easy (single-line removal) |
| **Status in base image** | NOT implemented |

### Rationale

Unnecessary `torch.cuda.synchronize()` calls in the decode path force the CPU to wait
for all pending GPU operations to complete. This creates a pipeline stall: the GPU sits
idle while the CPU processes the synchronization, then the CPU sits idle while the GPU
runs the next operation. Removing non-essential sync calls allows the GPU and CPU to
overlap their work.

### Key Files and Changes

**Search target**: `grep -rn "synchronize" python/minisgl/`

Look for `torch.cuda.synchronize()` calls in:
- `engine/engine.py` — `_sync_get_memory()` helper
- `scheduler/scheduler.py` — `shutdown()` method (safe to remove — only runs at exit)

**WARNING**: Do NOT remove these synchronize calls:
- `scheduler/scheduler.py` `copy_done.synchronize()` in `_process_last_data()` — needed
  for GPU→CPU token copy correctness. Removing this causes all requests to fail.
- `attention/fi.py` `self.last_event.synchronize()` — needed for FlashInfer buffer safety
- `engine/graph.py` CUDA graph capture sync — needed for graph correctness

### Implementation Guidance

1. `grep -rn "synchronize" python/minisgl/` to find all calls
2. Read context of each call to determine if it's in the hot path (decode loop)
3. Remove only the non-essential ones (shutdown, memory query)
4. Test with `/eval/run_eval` — verify `correct: true` before keeping changes
5. If any request fails, immediately revert: `git checkout -- .`

### Verification

Expected: ~4% throughput improvement. Small but safe optimization.

---

## 3. FlashInfer page_size > 1 (mini-sglang#110)

| Field | Detail |
|-------|--------|
| **Source** | [mini-sglang#110](https://github.com/sgl-project/mini-sglang/pull/110) |
| **Impact** | Reduces KV cache fragmentation |
| **Difficulty** | N/A — **NOT IMPLEMENTABLE** |
| **Status in base image** | NOT implemented |

### Rationale

Default `page_size=1` causes maximum PagedAttention fragmentation. Larger page sizes
(e.g., 16) reduce the number of page table entries and improve memory locality.

### WARNING

**Current flashinfer 0.6.3 does NOT support `page_size > 1` API.**
Changing page_size will cause `TypeError` in flashinfer's `begin_forward()` method,
crashing all requests. This optimization requires upgrading flashinfer to a newer version.

**Do NOT attempt this optimization.**

---

## 4. Fused Temperature + Softmax Sampling (sglang#20501)

| Field | Detail |
|-------|--------|
| **Source** | [sglang#20501](https://github.com/sgl-project/sglang/pull/20501) |
| **Impact** | 1.09–4.46x sampling speedup |
| **Difficulty** | Hard (requires Triton kernel writing) |
| **Status in base image** | NOT implemented |

### Rationale

Standard sampling pipeline:
```python
logits = logits / temperature        # Step 1: scale by temperature
probs = F.softmax(logits, dim=-1)    # Step 2: softmax
token = sample(probs)                # Step 3: sample from distribution
```

Each step launches a separate CUDA kernel with its own launch overhead and memory
round-trip. Fusing steps 1-2 into a single in-place operation eliminates one kernel
launch and one memory read/write cycle.

### Key Files and Changes

**Search target**: `grep -rn "temperature\|softmax" python/minisgl/`

Look for sampling logic in:
- `engine/sample.py` or `engine/sampler.py`
- Any file that does `logits / temperature` followed by `F.softmax`

**Note**: The current mini-sglang code may already use flashinfer's optimized sampling
kernels (`top_k_renorm_prob`, `sampling_from_probs`). If so, manual fusion won't
improve performance — flashinfer's native implementation is already fused.

### Implementation Guidance

1. Read the sampling code to understand current implementation
2. Check if flashinfer kernels are already used (if yes, skip this optimization)
3. If using PyTorch native ops, fuse temperature scaling into softmax:
   ```python
   # Before (two kernels):
   logits = logits / temperature
   probs = F.softmax(logits, dim=-1)
   
   # After (one kernel, in-place):
   logits.div_(temperature)
   probs = F.softmax(logits, dim=-1)
   ```
4. For maximum impact, write a custom Triton kernel that does temperature + softmax + sampling in one pass
5. Test thoroughly — sampling correctness is critical

### Verification

Expected: 1.09-4.46x speedup on the sampling step. Overall throughput impact depends
on how much time is spent in sampling vs. attention/MLP compute.

---

## 5. Overlap Scheduling (sglang#19347)

| Field | Detail |
|-------|--------|
| **Source** | [sglang#19347](https://github.com/sgl-project/sglang/issues/19347) |
| **Impact** | Theoretical: significant (overlaps CPU scheduling with GPU compute) |
| **Difficulty** | Very Hard (architectural change) |
| **Status in base image** | NOT implemented |

### Rationale

Current scheduling is synchronous: the CPU must finish scheduling the next batch before
the GPU can start executing it. With overlap scheduling, the CPU prepares batch N+1 while
the GPU is still executing batch N, hiding scheduling latency behind GPU compute.

### Key Files

- `scheduler/scheduler.py` — main scheduling loop
- Would require threading or async redesign of the scheduler

### Implementation Guidance

This is a significant architectural change that goes beyond simple code modification.
Not recommended as a first optimization target. Consider only if other optimizations
are insufficient and the agent has remaining rounds.

---

## 6. Skip KV Cache for Embedding Mode (sglang#21971)

| Field | Detail |
|-------|--------|
| **Source** | [sglang#21971](https://github.com/sgl-project/sglang/pull/21971) |
| **Impact** | +36% in embedding mode |
| **Difficulty** | Medium |
| **Status in base image** | NOT implemented |

### Rationale

When running in embedding/encode-only mode, KV cache is unnecessary. Skipping KV cache
allocation and using variable-length FlashAttention directly saves memory and eliminates
page management overhead.

### Note

This optimization is only relevant for embedding workloads. The standard MoltBench
benchmark uses generative (decode) workloads, so this PR's impact may be minimal.
Consider only if the workload profile changes.

---

## Summary Table

| # | PR | Technique | Impact | Difficulty | Implementable? |
|---|-----|-----------|--------|------------|----------------|
| 1 | mini-sglang#97 | Better Estimation Policy | +15.71% | Medium | **Yes** (primary Easy target) |
| 2 | sglang#7563 | Avoid cudaStreamSync | +4% | Easy | Yes (but limited impact) |
| 3 | mini-sglang#110 | page_size > 1 | — | N/A | **No** (flashinfer 0.6.3 incompatible) |
| 4 | sglang#20501 | Fused sampling | 1-4x sampling | Hard | Maybe (flashinfer may already do this) |
| 5 | sglang#19347 | Overlap scheduling | Significant | Very Hard | Possible but complex |
| 6 | sglang#21971 | Skip KV cache (embed) | +36% embed | Medium | Not relevant for decode workload |

### Recommended Optimization Order

1. **PR#97 (Better Estimation Policy)** — highest impact, medium difficulty, well-documented
2. **PR#7563 (Avoid cudaStreamSync)** — small but safe, easy to implement
3. **PR#20501 (Fused sampling)** — high potential but check if already optimized by flashinfer
4. Others — too complex or not applicable for current setup
