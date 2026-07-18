---
sidebar_position: 4
---

# Engineering Conventions

Practical rules for editing configs, launching runs, and resuming work without
surprises. Each item below maps to a real behavior in the AlphaDiana codebase,
not a style preference. For the full configuration reference see the
[run guide](../getting-started/quick-start) and the per-harness pages
under [harnesses](../harnesses/zeroclaw).

## Edit YAML configs with `sed`, not a YAML dumper

The configs under `configs/` carry inline comments, block scalars (multi-line
`system_prompt`), and deliberate field ordering. Round-tripping through
`yaml.safe_dump` (or `ruamel` reserialization) drops the comments and reflows
everything, turning a one-line change into a 700-line diff.

When you need to flip a value, patch the line in place:

```bash
# Good: surgical, comment-preserving
sed -i '' 's/^  temperature: 0.7/  temperature: 0.6/' \
  configs/full_runs/swe_verified_mini.yaml
```

```python
# Avoid: rewrites the whole file, loses comments and block scalars
data = yaml.safe_load(open(path))
data["agent"]["config"]["temperature"] = 0.6
yaml.safe_dump(data, open(path, "w"))   # do not do this
```

If a change is a one-off for a single run, prefer a CLI override (below) over
editing the file at all.

## Prefer CLI overrides for run-scoped tweaks

`alphadiana run` accepts repeatable `-o key.path=value` (long form
`--override`). The value is coerced automatically in the order bool then int
then float then string, so `-o num_samples=4` becomes an int and
`-o agent.config.stream=false` becomes a bool. There is no quoting escape hatch:
a string that looks numeric will be coerced.

```bash
alphadiana run config.yaml \
  -o agent.config.temperature=0.5 \
  -o max_concurrent=4
```

Override the things that vary per launch (`run_id`, `output_dir`,
`max_concurrent`) rather than forking a YAML file. Do not, however, use CLI
overrides to "speed up" contract-bound runs: `enable_thinking`, `max_tokens`,
and other reasoning controls are experimental variables, not plumbing. A
downscaled or variant run must get its own `run_id` suffix instead.

## Use named paths in `git add` for `configs/`, never wildcards

The `configs/` tree usually has stray untracked production configs sitting next
to tracked ones. A wildcard add sweeps them in by accident:

```bash
# Avoid: tracks whatever happens to be untracked in configs/
git add configs/full_runs/*.yaml
```

```bash
# Good: explicit named paths, or stage only already-tracked changes
git add configs/full_runs/swe_verified_mini.yaml
git add -u configs/
```

## Use `sk-EMPTY` for local vLLM, never literal `EMPTY`

The validator's `_has_nonempty_value` in
`alphadiana/engine/config/validator.py` treats `None`, the empty string,
the literal `EMPTY` (case-insensitive), and a string that is wholly an
unresolved `$VAR` / `${VAR}` placeholder as **not populated**. Most agents
require a non-empty `api_key`, so a literal `EMPTY` fails validation.

Use any other non-empty string for local vLLM:

```yaml
agent:
  config:
    api_base: http://127.0.0.1:8000/v1
    api_key: sk-EMPTY      # any non-"EMPTY" string passes; "EMPTY" fails
```

The same `$VAR`-placeholder rule means `${OPENAI_API_KEY}` resolves from the
shell, and if the env var is missing it degrades to `''` (caught as missing)
rather than leaking the literal placeholder into the request.

## Run `security_guard` before launching services

`scripts/security_guard.py --check` is a hard pre-flight gate (it is wired into
`start_openclaw.sh`). A non-zero exit blocks the launch; clear the finding
before bringing services up.

```bash
python3 scripts/security_guard.py --check     # exits non-zero on failure
```

Use `--daemon` for a continuous 10s watch loop, and `SECURITY_GUARD_BYPASS=1`
only as a deliberate, documented override. It also warns when the
`kernel.keys.maxkeys` quota is too low for Podman runs.

## Validate before you run

`alphadiana validate` prints `Config is valid.` on success and exits 1 with
indented `  - <error>` lines on failure. Run it before a long job:

```bash
alphadiana validate config.yaml
alphadiana run config.yaml
```

## Checkpoint-resume and `redo_all` semantics

Re-invoking `alphadiana run` on the same config **resumes** rather than
restarts. The runner loads the existing `<run_id>.jsonl` and skips work items
that already completed under the matching scorer:

| `num_samples` | Skip granularity | Source |
| --- | --- | --- |
| `> 1` (e.g. AIME pass@4) | `completed_sample_ids` — per `(task_id, sample_index)` | `Runner.run()` |
| `1` (e.g. GPQA pass@1) | `completed_task_ids` — per `task_id` | `Runner.run()` |

Per-sample artifacts use the suffix `{task_id}.jsonl` for sample 0 and
`{task_id}.sample_{N}.jsonl` for later samples. A scorer
mismatch against the existing records is warned, so resuming with a different
scorer does not silently mix results.

`--redo-all` bypasses the checkpoint and recomputes everything. It is exact
sugar for `-o redo_all=true`, which sets the
[`ExperimentConfig.redo_all`](../configuration/config-schema) flag.

```bash
# Resume: only fills in missing tasks/samples
alphadiana run config.yaml

# Force a clean rerun
alphadiana run config.yaml --redo-all
```

Because resume is the default, the conventional way to extend a run is to
re-launch the same config (optionally lowering `-o max_concurrent` for
truncation-heavy cells). To start fresh data, give the run a new `run_id` rather
than deleting files and reusing the old id.

## `run_id` hygiene

An empty `run_id` is auto-filled with `uuid.uuid4().hex[:12]`, and any `/` in a
`run_id` is replaced with `_` by `ExperimentConfig`. For real runs use a
descriptive, stable id encoding `{date}-{benchmark}-{harness}-{model}-{axis}` so
the checkpoint and the results directory are unambiguous, for example
`20260423-gpqa_diamond-directllm-qwen35_27b-v01`. Distinct variants get distinct
ids; do not reuse one id across configurations.
