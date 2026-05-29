# 20260529 TODO experiments — configs

AlphaDiana YAML configs for the (benchmark, harness, model) combinations
flagged for the 2026-05-29 sweep. One file per experiment, slug-matched
to its runbook under
[`context/runbook/20260529_todo_exps/`](../../context/runbook/20260529_todo_exps/).

## Layout

| Path | Contents |
| --- | --- |
| `configs/20260529_todo_exps/` (this folder) | 15 AlphaDiana YAML configs + this README |
| `context/runbook/20260529_todo_exps/` | 15 matching operator runbooks |

File-name convention is identical in both folders so the runbook and
its config are one-to-one by slug:

```
configs/20260529_todo_exps/<slug>.yaml
context/runbook/20260529_todo_exps/<slug>.md
```

## How to run

Use the runbook for the experiment, not this YAML directly — the
runbook documents the env vars, smoke command, full-run command, and
HF upload pattern. The YAML in this folder is the canonical config
the runbook's `python -m alphadiana.cli run` line points at.

```bash
# Example (substitute the slug you want to run):
python -m alphadiana.cli run \
  configs/20260529_todo_exps/mmmu_pro_vision-directllm-gemma-4-31b-it.yaml \
  -o run_id=$(date -u +%Y%m%d)-mmmu_pro_vision-directllm-gemma-4-31b-it-v01 \
  -o output_dir=$WORK_ROOT/results
```

## Validation

All 15 configs pass `python -m alphadiana.cli validate`. The four
`terminal_bench2-*` configs additionally require `TERMINAL_BENCH2_DIR`
and `OPENAI_BASE_URL` to be exported per the runbook before
`validate` succeeds.

## Parameter contract

See `context/runbook/20260529_todo_exps/README.md`. Each YAML here
bakes in the contract for its harness; do not edit per-cell defaults
without updating the matching runbook.
