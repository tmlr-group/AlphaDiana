# ZeroClaw Local-Qwen Rerun Parameters (2026-04-28)

Use this note when rerunning ZeroClaw on the non-coding local-Qwen benchmark
paths after the April 28 shell/runtime-trace config fix.

## What changed

- Pull the latest code before rerunning. AlphaDiana now writes ZeroClaw's
  schema-supported permissive shell controls into generated `config.toml`:
  `require_approval_for_medium_risk=false`,
  `block_high_risk_commands=false`, and shell timeouts derived from
  `agent.config.request_timeout`.
- Use `runtime_trace_mode: full`, not `all`. ZeroClaw 0.6.9 accepts
  `none`, `rolling`, and `full`; the old `all` value is now normalized by
  AlphaDiana but checked-in full-run configs should use `full`.
- Do not add `provider_max_tokens` to ZeroClaw TOML. Keep the public config
  knob as `agent.config.max_tokens`; AlphaDiana applies it through the
  logprob proxy request body.
- Full local-Qwen reruns should use a 128K output cap:
  `agent.config.max_tokens: 131072`. The April 25 25K runs were smoke tests,
  not full-run settings.
- Use conservative concurrency for ZeroClaw recovery on this host:
  `max_concurrent: 2` and `task_retries: 2`.

ZeroClaw 0.6.9 still has no dedicated heredoc allowlist setting. If heredoc
syntax is still denied after this change, treat it as a ZeroClaw tool-policy
limitation rather than a missing AlphaDiana config field.

## Full-run configs

Use these checked-in configs for the five non-coding local-Qwen reruns:

| Benchmark | Config |
| --- | --- |
| AIME 2026 | `configs/full_runs/aime2026_zeroclaw_qwen35_27b_logprobs.yaml` |
| IMO-AnswerBench | `configs/full_runs/imo_zeroclaw_qwen35_27b_logprobs.yaml` |
| GPQA-Diamond | `configs/full_runs/gpqa_zeroclaw_qwen35_27b_logprobs.yaml` |
| HLE multiple-choice | `configs/full_runs/hle_zeroclaw_qwen35_27b_logprobs.yaml` |
| MMMU-Pro vision | `configs/full_runs/mmmu_pro_zeroclaw_qwen35_27b_logprobs.yaml` |

The relevant parameters should be:

```yaml
agent:
  config:
    max_tokens: 131072
    request_timeout: 9300
    runtime_trace_mode: full
    capture_logprobs: true
    top_logprobs: 20
    max_tool_iterations: 100

max_concurrent: 2
task_retries: 2
```

For local vLLM behind Docker/ROCK, the sandbox provider URL must be reachable
from containers. On this host family that is `http://host.docker.internal:8011/v1`; if
your host uses a different bridge IP, override both ZeroClaw provider fields:

```bash
-o agent.config.api_base=http://<container-reachable-host>:8011/v1 \
-o agent.config.provider_api_base=http://<container-reachable-host>:8011/v1
```

## Rerun commands

Run from the repository root:

```bash
git pull
source scripts/activate.sh
python -m alphadiana.cli env
```

Then launch with a raw shell log. Reusing the same `run_id` resumes from
checkpoint by default; completed `valid_scored` task JSONs are skipped and
remaining/error samples are rerun. Use `--redo-all` only when you intentionally
want to discard checkpoint progress.

```bash
RUN_ID=full_aime2026_zeroclaw_qwen35_27b_logprobs
python -u -m alphadiana.cli run configs/full_runs/aime2026_zeroclaw_qwen35_27b_logprobs.yaml \
  2>&1 | tee -a logs/${RUN_ID}.log
```

For the other four configs, replace `RUN_ID` and the YAML path with the target
config's checked-in `run_id` and filename. If you need to force the conservative
settings from the command line, add:

```bash
-o agent.config.max_tokens=131072 \
-o agent.config.request_timeout=9300 \
-o agent.config.runtime_trace_mode=full \
-o max_concurrent=2 \
-o task_retries=2
```

## Post-run checks

Check that the old config warnings and tool-policy symptoms are gone or
reduced:

```bash
rg -n "Unknown observability.runtime_trace_mode|provider_max_tokens|shell command is not allowed|heredoc" \
  logs/${RUN_ID}.log results/${RUN_ID}
```

Also verify task records as sample lists:

```bash
python - <<'PY'
import json
from pathlib import Path
run_id = "full_aime2026_zeroclaw_qwen35_27b_logprobs"
root = Path("results") / run_id / "tasks"
valid = errors = total = 0
for path in sorted(root.glob("*.json")):
    data = json.loads(path.read_text())
    total += len(data)
    for sample in data:
        valid += sample.get("score_status") == "valid_scored"
        errors += bool(sample.get("error"))
print({"samples": total, "valid_scored": valid, "errors": errors})
PY
```

For spot checks, inspect `data[0].response_json.runtime_trace_present`,
`metadata.logprobs_capture_status`, and matching files under
`results/<run_id>/logprobs/` and `results/<run_id>/logprobs_int16/`.
