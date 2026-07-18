---
sidebar_position: 1
---

# Configuration

Every AlphaDiana experiment is described by a single YAML file with four required
sections (`agent`, `benchmark`, `scorer`, plus `sandbox` when needed) and a handful
of top-level keys (`run_id`, `max_concurrent`, `num_samples`, `output_dir`,
`metadata`). The file is parsed into the `ExperimentConfig` dataclass and launched
through the `alphadiana` CLI, where dotted `-o key.path=value` flags can override any
field at the command line. This page is the orientation map: see
[Config Schema](./config-schema) for the full field reference and
[CLI and Overrides](./cli-and-overrides) for command syntax and run-id conventions.
`configs/schema.yaml` is the authoritative, fully commented field reference; the
files under `configs/examples/` are smoke configs, useful as copy-paste starting
points but not production settings.

## The four sections

A config has four blocks plus top-level run controls. The minimal, env-driven
`direct_llm` example (`configs/examples/direct_llm.yaml`) is the basis for the shape
below; the `sandbox: null` and `num_samples: 1` lines are shown here for
illustration (both are optional and default to those values when omitted, so the
on-disk example leaves them out):

```yaml
run_id: ""

agent:
  name: direct_llm
  version: "1.0"
  config:
    # Leave blank to reuse OPENAI_* from .env via scripts/activate.sh.
    model: ""
    api_base: ""
    api_key: ""
    temperature: 0.6
    max_tokens:

benchmark:
  name: aime
  config:
    dataset: "HuggingFaceH4/aime_2024"
    split: "train"

sandbox: null            # null for direct_llm / self-managing CLI harnesses

scorer:
  name: numeric
  config:
    tolerance: 1e-6

max_concurrent: 1
num_samples: 1
output_dir: "./results"
```

| Section | Required | Selects | Notes |
| --- | --- | --- | --- |
| `agent` | yes | the harness | `name` + `version` + open pass-through `config` dict |
| `benchmark` | yes | the task set | `name` + benchmark-specific `config` |
| `sandbox` | optional | the execution environment | `null`, or `{name, config}`; required for `terminal_bench` / `osworld` |
| `scorer` | yes | the grader | `name` + scorer-specific `config` |

## Valid names

These are the values the validator and registries accept. Unknown keys inside any
`config` block are passed through to the harness untouched.

| Block | Accepted `name` values |
| --- | --- |
| `agent.name` | `direct_llm`, `openclaw`, `opencode`, `zeroclaw`, `swebench_docker`, `external_benchmark_docker`, `external_benchmark_podman`, `terminal_bench2_*` |
| `scorer.name` | `numeric`, `math_verify`, `exact_match`, `llm_judge`, `swebench_pro`, `swe_bench`, `terminal_bench2`, `external_benchmark`, `imo_verify`, `external_benchmark_qjl`, `decodingtrust` |
| `sandbox.name` | `null`, `local`, `rock`, `podman`, `swebench_container`, `decodingtrust` |

`sandbox: null` is correct for `direct_llm` and for CLI harnesses
([OpenCode](../harnesses/opencode), [ZeroClaw](../harnesses/zeroclaw)) that
self-manage their own containers via `controller_mode`. ROCK-backed runs add a
preflight that checks admin/proxy/redis reachability and port ownership before the
run starts; non-ROCK runs skip it.

## Top-level run controls

| Key | Default | Meaning |
| --- | --- | --- |
| `run_id` | auto | empty becomes `uuid.uuid4().hex[:12]`; any `/` is replaced with `_` |
| `max_concurrent` | `1` | parallel task executions; validated to the range `1..64` |
| `num_samples` | `1` | independent samples per task for pass@k (AIME uses 4, GPQA always 1) |
| `output_dir` | `./results` | where result files and the run report land |
| `task_retries` | `0` | per-task retry budget (must be `>= 0`); the `from_yaml` path defaults to `0` when the key is absent, though the dataclass default is `1` |
| `strict_report`, `strict_isolation` | `false` | stricter reporting / isolation gates |
| `metadata` | `{}` | free-form `author` / `gpu` / `notes` tags |

`ExperimentConfig` lives in
`alphadiana/engine/config/experiment_config.py`;
`ConfigValidator` lives at `alphadiana/engine/config/validator.py`. Result files
are written and read through `alphadiana/analysis/io/result_store.py`.

## agent.config: the open pass-through

`agent.config` is a free-form dict. The validator only enforces a small required
core (a non-empty `model` for `direct_llm`, a non-empty `api_base` for most agents
unless an auto-deploy or podman runtime supplies it); every other key flows straight
to the harness. Common LLM fields:

| Key | Default | Notes |
| --- | --- | --- |
| `model` / `model_name` | env | `model` for `direct_llm`/`zeroclaw`, `model_name` for `opencode` |
| `api_base`, `api_key` | env | filled from `OPENAI_BASE_URL` / `OPENAI_API_KEY` when blank |
| `temperature`, `top_p` | `0.7` | `direct_llm` defaults `temperature` to `0.7` when absent |
| `max_tokens` / `max_completion_tokens` | none | output length cap |
| `request_timeout` | `600` | per-request HTTP timeout (seconds) |
| `stream` | `true` | stream responses when the backend supports it |
| `capture_logprobs` | `true` | with `top_logprobs` (20) and `logprobs_format` (`int16`) |
| `enable_thinking`, `extra_body` | none | reasoning controls; see below |

When the LLM fields are blank, `_apply_agent_env_defaults` fills them from
`OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL_NAME` (loaded via
`source scripts/activate.sh`). This is why the example configs leave
`model`/`api_base`/`api_key` empty.

:::caution api_key sentinel
The validator treats `None`, `""`, and the literal string `EMPTY`
(case-insensitive) as missing. For a local vLLM endpoint use `api_key: "EMPTY"` or
`"sk-EMPTY"`. Any non-literal-`EMPTY` string passes; literal `EMPTY` fails
validation.
:::

:::note Reasoning is the experimental variable
There is no single canonical reasoning field. `direct_llm` reads `enable_thinking`
and `extra_body`; OpenCode configs use `enable_thinking: true`; ZeroClaw uses
`runtime_trace_mode: full`. Do not push reasoning controls through CLI overrides on
contract runs; treat reasoning effort as the variable under study, not plumbing.
:::

## Environment-variable expansion

Strings are expanded in two phases at load time. First, `os.path.expandvars`
resolves `$VAR` and `${VAR}` in every string (so `${SANDBOX_ID}`,
`${ROCK_BASE_URL}`, `${OPENAI_BASE_URL}` come from the shell). After the CLI
overrides are merged, any string that is *wholly* an unresolved `${VAR}` is blanked
to `""`, so a missing variable degrades to empty rather than leaking a literal
placeholder.

## Running and overriding

```bash
alphadiana run config.yaml
alphadiana run config.yaml -o agent.config.temperature=0.5 -o max_concurrent=4
alphadiana run config.yaml --redo-all     # == -o redo_all=true
alphadiana validate config.yaml
alphadiana report ./results
alphadiana batch a.yaml b.yaml --parallel
alphadiana env                            # ROCK service + port health
```

`-o` (long form `--override`) is repeatable and takes a dotted `key.path=value`.
Values are coerced automatically in order `bool -> int -> float -> str`, so
`-o num_samples=4` becomes an int and `-o agent.config.stream=false` becomes a bool.
There is no quoting escape, so a string-valued field that looks numeric will be
coerced. Re-running the same config resumes from the existing `<run_id>` result
files (skipping completed task or sample ids) unless `--redo-all` is passed. See
[CLI and Overrides](./cli-and-overrides) for the full command and run-id reference.

## Editing configs safely

Edit YAML with `sed` or by hand, not by round-tripping through `yaml.safe_dump`:
the dumper drops comments and block scalars and produces large spurious diffs.
Downscaled or variant runs must use a distinct `run_id` suffix rather than CLI
overrides of contract parameters.

## A note on the configs/ tree

Two grammars coexist under `configs/`. Per-experiment configs (`examples/`,
`full_runs/`, `memory_experiments/`, `micro_runs/`) match `configs/schema.yaml` and
run with `alphadiana run`. Campaign manifests such as
`configs/full_runs/swe_verified_mini.yaml` use a different top-level shape
(`campaign_id`, `defaults.run_id_prefix`, `models[]`, `path_templates[]`) and are
consumed by the `rollout_campaign` runner, not by `alphadiana run`. The
[benchmark guides](../benchmarks/) cover those campaign flows.
