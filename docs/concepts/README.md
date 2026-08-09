# Concepts & Design

Reasoning performance in an agent system depends on more than the base model.
It is also shaped by the harness that wraps the model, the tool interface and
execution environment, and the evaluation protocol. AlphaDiana treats the
*harness* as a first-class variable: the same model can score very differently
under a single-turn baseline, a multi-turn tool-using agent, or a CLI agent
with persistent memory. These pages explain the design that makes those
comparisons fair and reproducible.

## The pages

- **[Harness-Aware Evaluation](./harness-aware-evaluation.md)** — why the harness
  is a measured variable, the `Agent` contract every harness implements, and
  how the registry plugs them in.
- **[Evaluation Axes](./evaluation-axes.md)** — matched Tool, Skill, and Memory
  condition bundles, including the prompt/runtime changes each intervention
  actually makes.
- **[Isolation & Fairness](./isolation-and-fairness.md)** — task-scoped sandbox and
  container runtimes, what "isolated" does and does not mean, and the controls
  that keep two runs comparable.

## The contract every harness shares

A harness is an [`Agent`](./harness-aware-evaluation.md#decoupling-model-from-harness)
implementation. The abstract base class lives in
`alphadiana/harness/base.py` and defines three methods plus two class
attributes:

```python
class Agent(ABC):
    name: str
    version: str

    def setup(self, config: dict) -> None: ...
    def solve(self, task: BenchmarkTask, sandbox=None) -> AgentResponse: ...
    def teardown(self) -> None: ...  # no-op by default
```

The engine instantiates the agent, sets `agent.version`, calls `setup()` once,
then calls `solve()` per task (`alphadiana/engine/runner.py`). Every harness,
from the no-framework baseline to the CLI agents, returns the same
`AgentResponse` dataclass (`alphadiana/harness/base.py`), so the scorer, result
store, and report generator never need to know which harness produced a record.

Core `AgentResponse` fields:

| Field | Meaning |
|---|---|
| `answer` | extracted final answer |
| `trajectory` | normalized list of steps (`list[dict]`) |
| `raw_output` | unmodified agent output |
| `token_usage` | prompt / completion token counts |
| `token_entropy_stats` | per-token logprob/entropy summary |
| `wall_time_sec` | end-to-end solve time |
| `metadata` | per-harness extras |

Extended observability fields are also carried on the same dataclass:
`reasoning_trajectory`, `request_messages`, `response_json`, `sandbox_id`,
`gateway_url`, `artifact_manifest`, `system_prompt`, `finish_reason`, and the
workspace snapshot fields.

## Registry and plug-in flow

Harnesses are selected by name in YAML (`agent.name`). The
`AgentRegistry` (`alphadiana/harness/registry.py`) is a classmethod-only
singleton over `_registry: dict[str, Type[Agent]]`:

| Method | Behavior |
|---|---|
| `AgentRegistry.register(name, cls)` | record a harness under a key |
| `AgentRegistry.get(name)` | return the class, or raise `KeyError` listing available names |
| `AgentRegistry.list()` | sorted list of registered names |

Registration is **import-triggered, not auto-discovered**. The runner
explicitly imports each harness module (with `# noqa: F401`) so the
module-level `AgentRegistry.register(...)` call at the bottom of each file
fires. The same import-for-side-effect pattern registers benchmarks, sandboxes,
and scorers. Adding a new harness therefore means *both* a `register()` call in
the module *and* an import line in `alphadiana/engine/runner.py`.

The registered keys map to the four built-in harnesses:

| `agent.name` | Harness | Source |
|---|---|---|
| `direct_llm` | single-turn baseline, no tools | `alphadiana/harness/direct_llm.py` |
| `openclaw` | multi-turn agent + tools + code execution | [OpenClaw](../harnesses/openclaw.md) |
| `opencode` | wraps the `opencode` CLI | [OpenCode](../harnesses/opencode.md) |
| `zeroclaw` | ZeroClaw CLI bridge | [ZeroClaw](../harnesses/zeroclaw.md) |

## The baseline: `direct_llm`

`DirectLLMAgent` (`alphadiana/harness/direct_llm.py`, `name="direct_llm"`) is
the clean comparison point: a single system+user chat to an OpenAI-compatible
endpoint, with **no tools, no multi-turn loop, and no code execution**. It is
the "engine on a test bench" used as the reference for matched harness
comparisons.

It builds its own client with `httpx.Client(trust_env=False)` so inherited
SOCKS/HTTP proxy environment variables cannot break it. Config resolution falls
back to environment variables: `model` ← `OPENAI_MODEL_NAME`, `api_base` ←
`OPENAI_BASE_URL`, `api_key` ← `OPENAI_API_KEY`. Use `sk-EMPTY` for local vLLM,
because the literal string `EMPTY` (case-insensitive) is treated as unset.

Selected `agent.config` keys:

| Key | Default | Notes |
|---|---|---|
| `temperature` | `0.7` | sampling temperature |
| `max_tokens` | auto | unset → reads `max_model_len` from `/models`, uses `max_model_len - 8192` (fallback `131072`) |
| `request_timeout` | `600` | per-request timeout (seconds) |
| `max_retries` | `3` | exponential backoff on retryable errors |
| `stream` | `True` | streamed completion |
| `capture_logprobs` | `True` | logprob capture is on by default |
| `top_logprobs` | `20` | top-k logprobs per token |
| `logprobs_format` | `int16` | `int16` (quantized) or `float` |
| `system_prompt` / `enable_thinking` / `extra_body` | — | prompt and provider passthrough |

Answers are extracted via `\boxed{}` parsing; reasoning is recovered from
provider-specific `reasoning_content` / `reasoning` fields or `<think>...</think>`
tag splitting. A streaming timeout sets `answer=None` and records the failure in
`metadata` rather than crashing the run.

## Two proxies, opposite jobs

AlphaDiana ships two request proxies that share no wiring and serve opposite
purposes:

- **`LogprobCaptureProxy`** (`alphadiana/harness/proxies/logprob_proxy.py`) is an
  **in-process** threaded HTTP proxy that a CLI harness starts itself. The
  in-sandbox agent's OpenAI traffic is routed through it so the harness can
  inject `logprobs=True` / `top_logprobs` into `POST /chat/completions` and
  capture per-token records. This is observability plumbing.
- **`tool_filter_proxy`** (`alphadiana/harness/proxies/tool_filter_proxy.py`) is a
  **standalone** aiohttp CLI proxy launched manually, not imported by any
  harness. It is an experimental intervention on the request stream: it can
  filter the advertised `tools` by allow/block regex, strip or replace the
  system prompt (including the section-aware `--harness-strip` mode), and
  control OpenRouter provider routing and reasoning. It runs with
  `trust_env=True` so it honors `HTTP(S)_PROXY` on locked-down hosts, the exact
  opposite of `direct_llm`.

These map to the toggles described in [Evaluation Axes](./evaluation-axes.md): the
shared logprob helper `logprob_capture.py` is imported by `direct_llm` and all
three CLI harnesses, and the tool-filter proxy is how a "no tools" or
"stripped-prompt" condition is applied uniformly across harnesses.

## Skills

Skills are file bundles under `alphadiana/harness/skills/<name>/`, each with a
top-level `SKILL.md` (YAML frontmatter: `name`, `description`). A bare
`skill_folder` name (e.g. `advanced-maths`) resolves to the packaged bundle; a
value with `/` or an existing path is used directly. Mounting differs by
harness, OpenCode copies the bundle into `<workdir>/skills/<name>` while
ZeroClaw uploads each file into the sandbox, but in **neither case is skill text
auto-injected** into context. The system prompt must instruct the model to read
the mounted `SKILL.md`, which makes skill efficacy a prompt-level concern.
See the per-harness pages for the exact mount paths.

## How a run flows

```text
YAML config
   │  ExperimentConfig.from_yaml
   ▼
Runner.setup        # registry resolution: benchmark, agent, sandbox, scorer
   │
Runner.run          # per task:
   ├── benchmark.load_tasks
   ├── agent.solve(task, sandbox)  -> AgentResponse
   ├── scorer.score
   └── result_store.append          # alphadiana/analysis/io/result_store.py
   │
Runner.teardown
   ▼
results/<run_id>.jsonl  ->  report generation / dashboard
```

The orchestration code lives in `alphadiana/engine/`, the result store in
`alphadiana/analysis/io/result_store.py`. Continue with
[Harness-Aware Evaluation](./harness-aware-evaluation.md) for the rationale, or
jump to a specific harness under [Harnesses](../harnesses/openclaw.md).
