# Current Eval Status

Updated on April 17, 2026 for main-branch multimodal validation.

## Latest Multimodal Validation

- Opencode + HLE image task:
  verified on main with a direct OpenAI-compatible multimodal path against
  `https://api-inference.modelscope.cn/v1` and `Qwen/Qwen3.5-27B`.
  The single-task probe `hle_53` completed successfully with
  `metadata.transport = openai_multimodal`, which confirms the image attachment
  was actually sent to a multimodal backend.
- OpenClaw + HLE image task:
  follow-up work on `fix/opencode-hle-multimodal-20260417` confirmed the real
  image-loss root cause and fixed it in the OpenClaw runtime config path.
  A final rerun with a valid ModelScope token completed successfully and wrote
  `results/openclaw_hle_imageprobe_fixcheck_r3_20260417/.../tasks/hle_53.json`.
  The assistant returned a normal complexity analysis with `$$\\boxed{E}$$`
  instead of the old missing-image complaint. The answer is still wrong on this
  sample, but the OpenClaw image transport path now works end to end.
- Detailed evidence and exact commands:
  `docs/openclaw_opencode_hle_multimodal_validation_20260417.md`
- Internal debugging trail:
  `context/P25-three-benchmarks/openclaw-hle-multimodal-fix-20260417.md`

## Recommended Smoke Paths

- OpenClaw + IMO-AnswerBench:
  `python -m alphadiana.cli run configs/examples/openclaw_minimax_imo_answerbench.yaml`
- OpenClaw + HLE:
  `python -m alphadiana.cli run configs/examples/openclaw_minimax_hle.yaml`
- Opencode + HLE:
  `python -m alphadiana.cli run configs/examples/opencode_minimax_hle.yaml`
- terminal-bench-2:
  `python -m alphadiana.cli run configs/examples/terminal_bench2_minimax.yaml`

Common local environment:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export OPENAI_BASE_URL=https://api.example.com/v1
export OPENAI_API_KEY=sk-...
export OPENAI_MODEL_NAME=minimax-m2.5
export HF_ENDPOINT=https://hf-mirror.com
```

Additional requirements:

- HLE needs `HF_TOKEN` with access to `cais/hle`.
- terminal-bench-2 needs `TERMINAL_BENCH2_DIR` pointing to a local clone.

## Latest Local Execution Evidence

Observed on April 15, 2026 in `/tmp/AlphaDiana-dev-pr25-latest` with
`OPENAI_BASE_URL=https://api.example.com/v1` and `OPENAI_MODEL_NAME=minimax-m2.5`:

- OpenClaw + IMO-AnswerBench:
  benchmark load completed, ROCK sandbox started, OpenClaw gateway warmup succeeded,
  and the first live benchmark `/chat/completions` request was issued successfully.
- OpenClaw + HLE:
  gated dataset load completed with `HF_TOKEN`, ROCK sandbox started, OpenClaw gateway warmup succeeded,
  and the first live benchmark `/chat/completions` request was issued successfully.
- terminal-bench-2:
  `tb2_db-wal-recovery` loaded, evaluation container started, multiple live LLM API rounds completed,
  and the verifier wrote `/tmp/tb2_logs/tb2_db-wal-recovery/verifier/reward.txt`.

## Known Fixed Issues On This Branch

- `scripts.full_pipeline` is restored, so `tests/test_external_benchmark_pipeline.py` can collect again.
- `tomli` is included in the `all` extra and in the quickstart dependency verification path.
- OpenClaw auto-deploy can now resolve provider settings from either local `OPENAI_*` exports or `agent.config`.
- Explicit `ROCK_*` environment variables now override stale values from `.rock_ports.env`.
- HLE benchmark tasks now preserve dataset images as `task.attachments`.
- Opencode is registered on main and can use a direct multimodal OpenAI-compatible
  call path for image-backed tasks.
- OpenClaw request capture now tolerates multimodal `content` lists when
  recording trajectories and artifacts.
- OpenClaw runtime deploy now marks the configured provider model as
  image-capable so inner OpenClaw sessions retain forwarded HLE images.

## Remaining Watch Items

- HLE dataset access is still externally gated; missing `HF_TOKEN` should fail fast.
- The current OpenAI-compatible `minimax-m2.5` backend rejects multimodal requests for image
  tasks and is not a valid image-backed HLE target.
- Local `.env` may still hold an invalid ModelScope token; use a fresh valid
  token for ModelScope-backed reruns.
- For local provider-backed OpenClaw smokes, keep the token budget conservative unless the provider limit is already confirmed.
- terminal-bench-2 scores should be interpreted separately from simple path-validity smoke results.
