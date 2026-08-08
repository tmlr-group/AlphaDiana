---
sidebar_position: 2
---

# Run Config Schema

Every AlphaDiana run is defined by a single YAML file. The CLI parses it into the
`ExperimentConfig` dataclass (`alphadiana/engine/config/experiment_config.py`), validates
it, and hands the four blocks (agent / benchmark / sandbox / scorer) to the runner. The
annotated core-shape reference lives at `configs/schema.yaml` and this page
summarizes it. Harness and benchmark `config` blocks are open pass-throughs;
their dedicated pages are the reference for path-specific keys and defaults.

## Top-level shape

```yaml
run_id: my-experiment-v01      # optional; auto-filled if blank

agent:
  name: direct_llm             # required
  version: "1.0"               # required
  config: {}                   # pass-through dict for the harness

benchmark:
  name: aime                   # required
  config: {}

sandbox: null                  # null, or { name, config }

scorer:
  name: math_verify            # required
  config: {}

max_concurrent: 1              # default 1 (1..64)
num_samples: 1                 # default 1; pass@k when > 1
output_dir: ./results          # default ./results
metadata: {}                   # free-form tags
```

`sandbox: null` is used for `direct_llm` and for OpenCode controller modes that
self-manage their runtime. The generic ZeroClaw harness requires a live sandbox.

## Top-level fields

These map one-to-one onto `ExperimentConfig` in
`alphadiana/engine/config/experiment_config.py`.

| Key | Type | Default | Notes |
|---|---|---|---|
| `run_id` | string | `uuid4().hex[:12]` | Empty value auto-fills a UUID; `ExperimentConfig.__post_init__()` replaces any `/` with `_`. |
| `agent.name` | string | required | e.g. `direct_llm`, `openclaw`, `opencode`, `zeroclaw`, `swebench_docker`. |
| `agent.version` | string | required | Version label containing at least one digit, such as `"1.0"` or `"0.6.9"`; `"latest"` is rejected. |
| `agent.config` | dict | `{}` | Open pass-through to the harness (see below). |
| `benchmark.name` | string | required | e.g. `aime`, `gpqa_diamond`, `hle`, `mmmu_pro`, `terminal_bench2`, `swe_bench`. |
| `benchmark.config` | dict | `{}` | `split`, `year`, `subset`, `data_path`, etc. |
| `sandbox` | null \| object | `null` | `{ name, config }`; name in `local` / `rock` / `podman` / `swebench_container`. |
| `scorer.name` | string | required | Registered values are listed in the scorer table below. |
| `scorer.config` | dict | `{}` | Scorer-specific params. |
| `max_concurrent` | int | `1` | Parallel task executions; validator requires `1 <= n <= 64`. |
| `num_samples` | int | `1` | Independent samples per task; `> 1` reports pass@k / avg@k. |
| `output_dir` | string | `./results` | Result-store root. |
| `redo_all` | bool | `false` | Ignore checkpoint and rerun everything (CLI sugar: `--redo-all`). |
| `task_retries` | int | `0` | Retry attempts per task; validator requires `>= 0`. |
| `task_retry_on_recoverable_only` | bool | `false` | Retry only on recoverable errors. |
| `sandbox_retries` | int | `1` | Sandbox startup retries. |
| `strict_report` | bool | `false` | Exit non-zero when the report finds missing samples, invalid scored rows, or error records. |
| `strict_isolation` | bool | `false` | For ROCK auto-create/predeploy paths, turn setup failures into hard errors instead of shared-gateway fallback. |
| `metadata` | dict | `{}` | Free-form tags (`author`, `gpu`, `notes`, ...). |

:::tip Sample counts are protocol choices
`num_samples` is a top-level run setting, not a benchmark-loader setting. The
validator accepts any positive value for GPQA or AIME. Record the chosen value
and keep it fixed across cells being compared.
:::

## The four blocks

### agent.config

`agent.config` is an open dict. The validator enforces only a small required core; unknown
keys are passed straight through to the harness. Common LLM fields have
harness-specific defaults:

| Key | Default | Notes |
|---|---|---|
| `model` / `model_name` | env-filled | Model id; `direct_llm`/`zeroclaw` use `model`, `opencode` uses `model_name`. |
| `api_base` | env-filled | OpenAI-compatible base URL. |
| `api_key` | env-filled | Use `sk-EMPTY` or another non-empty placeholder for keyless local vLLM. |
| `temperature` | harness-specific | DirectLLM/OpenClaw use `0.7`, ZeroClaw uses `0.0`, and OpenCode leaves it to the provider when omitted. |
| `max_tokens` / `max_completion_tokens` | harness | Output budget. |
| `request_timeout` / `timeout` | harness-specific | DirectLLM 600s, OpenClaw 1800s, ZeroClaw 1200s; OpenCode uses `timeout` (1200s in the generic agent). |
| `stream` / `streaming` | harness-specific | DirectLLM/OpenClaw default on; ZeroClaw's stock CLI consumes non-streaming JSON; OpenCode is optional. |
| `enable_thinking` | `None` | Reasoning toggle (see note below). |
| `capture_logprobs` | harness-specific | DirectLLM defaults to `true`; other transports require explicit support/configuration. |
| `system_prompt` | harness | Optional system-prompt text. |
| `memory_scope` | unset | Optional explicit scope: `intra_task`, `cross_sample`, or `cross_task`. Stateful scopes force sequential dispatch. |
| `persistent_memory` | `false` | Must be false for `intra_task` and true for `cross_sample`/`cross_task` when `memory_scope` is explicit. |
| `memory_enabled` | harness-specific | Enable the harness-native memory execution path independently of cross-work-item persistence. |
| `strict_memory` | `false` | When true, missing native memory evidence fails the work item instead of silently falling back. |
| `bridge_port` | `8080` | ZeroClaw Podman bridge listener, container exposure, health-probe, and published API port. |

Harness-specific keys (e.g. `controller_mode`, `tools_profile`,
`system_prompt_override`, and the nested `env{}` for `swebench_docker` modes) are documented
per-harness. See [`zeroclaw`](../harnesses/zeroclaw), [`opencode`](../harnesses/opencode),
and [`openclaw`](../harnesses/openclaw).

:::caution Reasoning is the experimental variable
There is no single canonical reasoning field. `direct_llm` reads `enable_thinking` and
`extra_body`; reasoning text is parsed back out of provider responses. Never CLI-override
reasoning controls (or `max_tokens`) on contract runs to "speed up" an experiment, and never
inject reasoning flags into the proxy plumbing.
:::

### benchmark.config

Benchmark-specific. Typical keys: `split`, `year`, `subset`, `data_path`. For smoke configs
you may also see pinned `dataset_index` / `max_tasks`.

### sandbox

Set to `null` when the harness manages its own runtime. When present, `sandbox.name`
selects the backend (`local`, `rock`, `podman`, `swebench_container`) and `sandbox.config`
carries backend params (ROCK `admin_base_url` / `proxy_base_url` / `image` / `memory` /
`cpus`; Podman `ports` / `network` / `name_prefix`; etc.). A ROCK preflight runs only for
ROCK-backed runs.

### scorer

| Scorer | When to use | Notes |
|---|---|---|
| `math_verify` | Recommended default for math | SymPy / math-verify symbolic equivalence; falls back to normalized string match. |
| `numeric` | Numeric answers | Tolerance default `1e-6`. |
| `exact_match` | Exact string answers | Math-aware normalization + strict equality; does not equate `1/2` and `0.5`. |
| `llm_judge` | Open-ended (e.g. HLE) | Needs `api_base` / `api_key` / `judge_model`. |
| `swebench_pro` | SWE-bench Pro | Requires `eval_script_path` and `scripts_dir`. |
| `swe_bench` | SWE-bench Verified | Runs the official SWE-bench evaluator against the submitted patch. |
| `terminal_bench2` | Terminal-Bench 2 | Converts the task-container verifier reward to a binary score. |
| `imo_verify` | IMO-AnswerBench | Repo-local conservative math-answer verifier; required for this benchmark. |

## Environment-variable interpolation

`ExperimentConfig.from_yaml()` resolves the environment in two phases:

1. **Expand** — `_expand_env_vars` runs `os.path.expandvars` on every string in the document,
   so `$VAR` and `${VAR}` are substituted from the shell before CLI overrides are merged.
2. **Clear** — `_clear_unresolved_env_placeholders` blanks any string that is *wholly* an
   unresolved `${VAR}`. A missing variable degrades to `""` rather than leaking a literal
   placeholder.

```yaml
agent:
  name: openclaw
  config:
    api_base: "${ROCK_PROXY_URL}/sandboxes/${SANDBOX_ID}/proxy/v1"
    api_key: "${OPENAI_API_KEY}"
```

### Agent env defaults

When an agent field is left blank, `_apply_agent_env_defaults()` in
`alphadiana/engine/config/experiment_config.py` fills it from the environment.
This is why example configs leave `model` / `api_base` /
`api_key` empty and rely on `.env` loaded via `source scripts/activate.sh`.

| Agent field | Env var | Applies to |
|---|---|---|
| `api_base` | `OPENAI_BASE_URL` | direct_llm, openclaw, zeroclaw, opencode, terminal_bench2_* |
| `api_key` | `OPENAI_API_KEY` | same |
| `model` | `OPENAI_MODEL_NAME` | direct_llm, openclaw, zeroclaw, tb2_docker, tb2_zeroclaw |
| `model_name` | `OPENAI_MODEL_NAME` | opencode, tb2 variants |

For OpenClaw auto-deploy configs that provide both `rock_agent_config_path` and
`openclaw_config_path`, `OPENAI_BASE_URL` is the model-provider endpoint inside
the sandbox. It does not fill `api_base`; the runner first deploys the OpenClaw
gateway and then supplies that gateway address to the harness.

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8000/v1
export OPENAI_API_KEY=sk-EMPTY
export OPENAI_MODEL_NAME=Qwen/Qwen3.5-27B
```

:::note The `EMPTY` sentinel
`ConfigValidator._has_nonempty_value()` treats `None`, `""`, `EMPTY` (case-insensitive),
and a lone `$VAR` / `${VAR}` placeholder as **not populated**. For local vLLM,
use `api_key: "sk-EMPTY"` or another non-empty placeholder.
:::

## CLI overrides

`alphadiana run` accepts repeatable `-o key.path=value` (long form `--override`). Each
override is parsed by `parse_override()` in
`alphadiana/engine/config/experiment_config.py`: it splits on the first
`=`, builds a nested dict from the dotted key path, and deep-merges it after env expansion.

Value coercion is automatic and order-sensitive — `true`/`false` to bool, then int, then
float, else string. There is no quoting escape hatch, so a string that looks numeric will be
coerced.

```bash
alphadiana run config.yaml -o agent.config.temperature=0.5 -o max_concurrent=4
alphadiana run config.yaml --redo-all          # == -o redo_all=true
```

## run_id conventions

An empty `run_id` is auto-filled with `uuid4().hex[:12]`, and any `/` becomes `_`. Real
configs use descriptive kebab/underscore ids encoding `{date?}-{benchmark}-{harness}-{model}-{axis/version}`:

```
20260423-gpqa_diamond-directllm-qwen35_27b-v01
exp2-zw-aime-memory-passk
micro_aime2026_opencode_kimi_k26_memory_cross_sample
```

Downscaled or variant runs must use a distinct `run_id` suffix rather than CLI-overriding
contract params.

## Running

```bash
alphadiana validate config.yaml      # prints "Config is valid." or lists errors and exits 1
alphadiana run config.yaml           # run (resumes from checkpoint by default)
alphadiana run config.yaml --redo-all
alphadiana report ./results          # scan root JSONL files and regenerate reports
alphadiana batch c1.yaml c2.yaml --parallel
alphadiana env                       # ROCK service + port-ownership health
```

From a local checkout the equivalent module form works too:

```bash
python -m alphadiana.cli run config.yaml -o run_id=my_test -o output_dir=/tmp/runs/my_test -o max_concurrent=5
```

Re-invoking the same config **resumes**: `run` loads the existing `<run_id>` records and
skips already-completed tasks (or samples, when `num_samples > 1`) unless `--redo-all` is
given. The result store lives under `output_dir/<run_id>/`
(see `alphadiana/analysis/io/result_store.py`).

## Editing configs

Edit YAML with `sed`, not a `yaml.safe_dump` round-trip — PyYAML drops comments and block
scalars and produces huge spurious diffs. When committing under `configs/`, list named files
in `git add` (or use `git add -u`); never use wildcards.
