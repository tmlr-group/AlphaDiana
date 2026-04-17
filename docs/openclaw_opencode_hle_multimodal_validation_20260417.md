# OpenClaw and Opencode HLE Multimodal Validation

Date: April 17, 2026

## Scope

Validate two things on top of main:

- `opencode` must support real image-backed HLE tasks.
- `openclaw` must be rerun on an image-backed HLE task and its trajectory/result
  inspected for anomalies.

## Environment

- Worktree: `/path/to/users/xxx/projects/AlphaDiana-dev`
- Local main ROCK ports:
  - admin `9027`
  - proxy `9080`
  - redis `6379`
  - ray `6386`
- Separate `pr23` ROCK ports were left untouched:
  - admin `9000`
  - proxy `9005`
  - redis `6381`

## Code-Level Outcome

- Added `alphadiana/agent/opencode.py` on main and registered it in
  `alphadiana/runner/runner.py`.
- Added `alphadiana/utils/attachments.py` for shared attachment helpers.
- Extended `alphadiana/benchmark/hle.py` so HLE image questions populate
  `task.attachments`.
- Updated `alphadiana/agent/openclaw.py` so wrapper requests forward multimodal
  user content and trajectory capture can normalize non-string request content.

## Validation 1: Opencode Against an Actual Multimodal Backend

First, the OpenAI-compatible MiniMax endpoint was probed with image-backed HLE input and
returned a provider-side 400 stating that `minimax-m2.5` is not a multimodal
model on that endpoint. That invalidates it as an image-backed HLE target.

Then the same task was rerun against:

- base URL: `https://api-inference.modelscope.cn/v1`
- model: `Qwen/Qwen3.5-27B`

Command shape:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_TOKEN=...
export OPENAI_BASE_URL=https://api-inference.modelscope.cn/v1
export OPENAI_API_KEY=...
export OPENAI_MODEL_NAME='Qwen/Qwen3.5-27B'
python -m alphadiana.cli run configs/examples/opencode_minimax_hle.yaml \
  -o benchmark.config.dataset_index=53 \
  -o benchmark.config.max_tasks=1 \
  -o run_id=opencode_hle_imageprobe_modelscope_20260417 \
  -o output_dir=./results/opencode_hle_imageprobe_modelscope_20260417 \
  -o redo_all=true
```

Observed result:

- Run completed successfully.
- Result artifact:
  `results/opencode_hle_imageprobe_modelscope_20260417/opencode_hle_imageprobe_modelscope_20260417/tasks/hle_53.json`
- The task record contains:
  - `metadata.transport = "openai_multimodal"`
  - `trajectory[1].attachments = ["image_1"]`
  - final model output `$$\\boxed{E}$$`

Interpretation:

- The answer was wrong on this sample, but the path is genuinely multimodal.
- This is sufficient to show that `opencode` now supports image-backed HLE tasks
  through a real multimodal call chain.

## Validation 2: OpenClaw Historical Failure Snapshot

Before the runtime-config fix, the same HLE sample was rerun through OpenClaw
against the same ModelScope backend. That historical run is kept here because
it captured the original failure mode.

Command shape:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_TOKEN=...
export ROCK_CONFIG="$PWD/ref/ROCK/rock-conf/rock-local-proxy.yml"
python -m alphadiana.cli run configs/examples/openclaw_minimax_hle.yaml \
  -o agent.config.OPENAI_BASE_URL=https://api-inference.modelscope.cn/v1 \
  -o agent.config.OPENAI_API_KEY=... \
  -o agent.config.OPENAI_MODEL_NAME='Qwen/Qwen3.5-27B' \
  -o benchmark.config.dataset_index=53 \
  -o benchmark.config.max_tasks=1 \
  -o run_id=openclaw_hle_imageprobe_modelscope_20260417_r2 \
  -o output_dir=./results/openclaw_hle_imageprobe_modelscope_20260417_r2 \
  -o redo_all=true
```

Observed historical artifact:

- `results/openclaw_hle_imageprobe_modelscope_20260417_r2/openclaw_hle_imageprobe_modelscope_20260417_r2/tasks/hle_53.json`

What the artifact proves:

- `request_messages[0].content` is a list, not plain text.
- That list contains:
  - a text item with the HLE question
  - an `image_url` item carrying a base64 `data:image/png` payload
- The returned assistant message still says:
  `I don't see the sorting algorithm in your message.`

Historical interpretation:

- The AlphaDiana wrapper is forwarding the image correctly.
- The image is still being dropped or ignored inside the OpenClaw runtime
  stack after the local wrapper sends it.
- Therefore OpenClaw image-backed HLE is still not a valid evaluation path on
  main, even with a multimodal backend.

This historical conclusion is superseded by the April 17 follow-up summary
below.

## Targeted Test Status

The related targeted tests passed during this work:

```bash
source scripts/activate.sh
export PYTHONPATH=/path/to/users/xxx/projects/AlphaDiana-dev/ref/ROCK:$PWD
export ROCK_CONFIG=/path/to/users/xxx/projects/AlphaDiana-dev/ref/ROCK/rock-conf/rock-local-proxy.yml
pytest -q tests/test_hle_benchmark.py tests/test_opencode_agent.py tests/test_fix_0312_features.py tests/test_openclaw_runtime.py
```

Observed result: `45 passed`

## Bottom Line

- `opencode` on main now has a working image-capable path for HLE.
- OpenClaw's inner runtime image-loss bug is fixed, and the image-backed HLE
  path now works end to end.

## OpenClaw Follow-up Summary

Date: April 17, 2026

### User-Facing Outcome

- The OpenClaw image-input bug was not in the AlphaDiana wrapper. It was inside
  the OpenClaw runtime configuration.
- OpenClaw now keeps forwarded HLE images through its internal runtime path.
- A final rerun with a valid ModelScope token completed successfully on
  `hle_53`.
- The assistant no longer responded with
  `I don't see the sorting algorithm in your message.`
- The final answer for that sample was still `E` while the ground truth is `D`,
  so the remaining issue is score quality, not image transport.

### Files Changed

- `alphadiana/agent/openclaw_runtime.py`
- `openclaw_deploy/openclaw.json`
- `tests/test_openclaw_runtime.py`

### Verification Summary

- Targeted tests:
  `pytest -q tests/test_hle_benchmark.py tests/test_opencode_agent.py tests/test_fix_0312_features.py tests/test_openclaw_runtime.py`
- Observed result:
  `45 passed`
- Final single-task artifact:
  `results/openclaw_hle_imageprobe_fixcheck_r3_20260417/openclaw_hle_imageprobe_fixcheck_r3_20260417/tasks/hle_53.json`

### Detailed Process Record

The full debugging trail, including root-cause analysis, sandbox evidence,
failed interim rerun, and final successful rerun, is archived in:

- `context/P25-three-benchmarks/openclaw-hle-multimodal-fix-20260417.md`
