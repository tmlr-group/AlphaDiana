# Harness Prompt Alignment Smokes

Active 3-task local-Qwen smoke matrix for OpenClaw and ZeroClaw.

## Scope

| Dimension | Values |
|---|---|
| Agents | `openclaw`, `zeroclaw` |
| Benchmarks | AIME 2024, IMO-AnswerBench, GPQA-Diamond, HLE multiple-choice, MMMU-Pro vision |
| Token caps | `trunc5k` (`max_tokens: 5120`), `long64k` (`max_tokens: 65536`) |
| Samples | `num_samples: 1` |
| Tasks | `benchmark.config.max_tasks: 3` |
| Concurrency | `max_concurrent: 3` per config, except ZeroClaw `long64k` uses `max_concurrent: 1` |
| Logprobs | `capture_logprobs: true`, `top_logprobs: 20` |

All configs use local `Qwen/Qwen3.5-27B` at `http://127.0.0.1:8011/v1`,
`temperature: 0.0`, `top_p: 0.95`, thinking enabled, and the Harness prompts
from `configs/PROMPTS.md`.

## Run Pattern

Run from the repository root:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export HF_ENDPOINT=https://hf-mirror.com
mkdir -p logs

CONFIG=configs/smokes/harness_prompt_alignment_20260425/gpqa_openclaw_trunc5k_qwen35_27b_logprobs_smoke.yaml
RUN_ID=$(python - <<'PY' "$CONFIG"
import sys, yaml
print(yaml.safe_load(open(sys.argv[1]))["run_id"])
PY
)
python -m alphadiana.cli run "$CONFIG" --redo-all 2>&1 | tee "logs/${RUN_ID}.log"
```

For long local-Qwen runs, keep foreground `tee` logging alive. Completed task
JSONs are checkpoint-resumable; tasks killed mid-stream may need the same run
ID resumed without `--redo-all`.

ZeroClaw `long64k` configs are intentionally serial per config. During the
April 26 smoke, a concurrent resume left two ZeroClaw CLI tasks initialized for
9300 seconds without making a provider request, so no logprob sidecar could be
captured. Resuming the same run IDs with `-o max_concurrent=1` retried only the
failed checkpoint tasks and completed with captured logprobs.

```bash
python -m alphadiana.cli run "$CONFIG" -o max_concurrent=1 \
  2>&1 | tee -a "logs/${RUN_ID}.resume_serial_$(date +%Y%m%d_%H%M%S).log"
```

## Inspection Checklist

For each task JSON, inspect `data[0]`:

- `score_status` is present; normal model wrong answers should score `0`, not
  remain unscored.
- trajectory is non-empty and has no unexpected AlphaDiana-level failure.
- truncation runs either reach `n_tokens == 5120` with preserved artifacts, or
  are explicitly recorded as finishing before the cap.
- `metadata.logprobs_capture_status == captured`.
- float and int16 sidecars exist and line counts match
  `token_entropy_stats.n_tokens`.

## April 26 Evidence

The full OpenClaw/ZeroClaw matrix completed 20/20 run IDs and 60/60 task JSONs
as `valid_scored`. Every task had `metadata.logprobs_capture_status=captured`
and matching float/int16 sidecars.

The ZeroClaw long64k AIME/HLE run IDs initially recorded one
`requested_missing` timeout each under concurrent resume. The final task JSONs
were replaced by successful serial checkpoint resumes:

- `phase12_zeroclaw_aime_t3_thinking_on_long64k_logprobs_20260425`,
  `aime_61`: `valid_scored`, `score=0`, `n_tokens=8435`, serial exit `0`.
- `phase12_zeroclaw_hle_t3_thinking_on_long64k_logprobs_20260425`,
  `hle_11`: `valid_scored`, `score=1`, `n_tokens=5293`, serial exit `0`.
