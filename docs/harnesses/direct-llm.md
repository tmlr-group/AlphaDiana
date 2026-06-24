---
sidebar_position: 2
---

# direct_llm (Baseline)

`direct_llm` is the no-harness baseline. It sends a single system+user chat to an
OpenAI-compatible endpoint and scores the returned answer. There are no tools, no
multi-turn loop, and no code execution, so it isolates raw model quality from any
harness scaffolding (compare the agentic harnesses
[opencode](../harnesses/opencode), [openclaw](../harnesses/openclaw), and
[zeroclaw](../harnesses/zeroclaw)).

The agent class is `DirectLLMAgent` (`alphadiana/harness/direct_llm.py:76`,
`name = "direct_llm"`). It is registered with the shared `AgentRegistry`
(`alphadiana/harness/registry.py`) at module import; the engine triggers that
import explicitly in `alphadiana/engine/runner.py`, so a config simply selects
`agent.name: direct_llm`.

## Config Resolution

Three core settings resolve from config first, then fall back to environment
(`_resolve_setting`, `direct_llm.py:143`):

| `agent.config` key | Env fallback | Default |
|---|---|---|
| `model` | `OPENAI_MODEL_NAME` | (required) |
| `api_base` | `OPENAI_BASE_URL` | (required) |
| `api_key` | `OPENAI_API_KEY` | `EMPTY` |

The literal string `EMPTY` (case-insensitive) is treated as *unset* and triggers
the env fallback. For a local vLLM endpoint that needs no real key, set the value
to `sk-EMPTY` (any non-`EMPTY` string passes config validation).

```yaml
agent:
  name: direct_llm
  config:
    model: Qwen/Qwen3.5-27B
    api_base: http://127.0.0.1:8011/v1
    api_key: sk-EMPTY
    temperature: 0.0
    top_p: 0.95
    stream: true
```

Other recognized keys: `temperature` (default `0.7`), `top_p`, `max_tokens`,
`max_completion_tokens`, `request_timeout` (default `600`), `stream` (default
`true`), `stream_total_timeout`, `max_retries` (default `3`), `system_prompt`,
`enable_thinking`, `extra_body`, and the logprob keys below.

### trust_env=False

The agent builds its own OpenAI client over `httpx.Client(trust_env=False)`
(`direct_llm.py:140`). This deliberately ignores inherited `ALL_PROXY` /
`HTTP(S)_PROXY` / SOCKS environment variables so a proxy meant for some other
tool cannot silently break direct calls. (This is the opposite of the standalone
`tool_filter_proxy`, which uses `trust_env=True` precisely so it *can* honor
`HTTP(S)_PROXY` to reach OpenRouter on locked-down hosts.)

## max_tokens Auto-Resolution

If `max_tokens` is left unset, the agent resolves it from the endpoint
(`_resolve_max_tokens`, `direct_llm.py:163`): it `GET`s `{api_base}/models`, reads
`data[0].max_model_len`, and uses `max_model_len - 8192` as a safe ceiling. If the
endpoint is unreachable or returns no usable value, it falls back to `131072`.
Setting `max_tokens` explicitly skips this probe entirely.

## Answer Extraction and Reasoning Recovery

The answer is parsed by `_extract_answer`, which calls
`utils.math_answer.extract_answer_candidate` and prefers a `\boxed{}` value. The
default system prompt (`direct_llm.py:32`) instructs the model to put its final
answer in `\boxed{}`.

Hidden reasoning is recovered three ways:

- `model_extra['reasoning_content']` (Volcengine Kimi)
- `model_extra['reasoning']` (OpenRouter Kimi), both via
  `_extract_reasoning_from_model_extra`
- `<think>...</think>` tag splitting (`_split_think_tags`) for Qwen3 / vLLM

When `finish_reason == 'timeout'` (raised by `stream_total_timeout`), the answer is
set to `None` and the metadata records `failure_reason='timeout'` plus
`directllm_timeout_scored_zero=True`, so the sample is preserved rather than
crashing the run.

## Retry and Timeout

`request_timeout` (default `600s`) bounds each call; `stream_total_timeout` caps a
full streamed response independently. Retries use `_is_retryable`
(`direct_llm.py:362`), which covers the OpenAI SDK `RateLimit` / `Timeout` /
`Connection` / 5xx / `APIError` classes, `httpx`/`httpcore` transport errors, and a
keyword match (`timeout`, `429`, `502`, `503`, `network connection lost`,
`incomplete chunked read`, ...). Backoff is exponential,
`min(2 * 2**attempt, 60) + jitter`, up to `max_retries`.

## Logprob Capture (default on)

`direct_llm` captures per-token logprobs by default through the shared helper
`alphadiana/harness/proxies/logprob_capture.py` (used by every agent). It sets
`logprobs=True` plus `top_logprobs` on the request and walks
`choices[0].logprobs.content` into per-token records. The default `int16` format
quantizes records via `analysis.logprobs.quantize_records_int16`.

| `agent.config` key | Default | Meaning |
|---|---|---|
| `capture_logprobs` | `true` | enable logprob capture |
| `top_logprobs` | `20` | alternatives kept per token |
| `logprobs_format` | `int16` | `int16` (quantized) or `float` |

Final metadata records `logprobs_capture_status` as one of `captured`,
`requested_missing`, or `not_requested`.

## Running

Validate then run a checked-in config:

```bash
python -m alphadiana.cli validate configs/examples/direct_llm_gpqa_diamond.yaml
python -m alphadiana.cli run   configs/examples/direct_llm_gpqa_diamond.yaml \
  -o run_id=directllm_smoke
```

Local-vLLM Qwen example with logprobs and explicit sampling:

```bash
source scripts/activate.sh
export PYTHONPATH=$PWD
export QWEN_VLLM_API_BASE=http://127.0.0.1:8011/v1
export QWEN_VLLM_API_KEY=sk-EMPTY

python -m alphadiana.cli run configs/full_runs/p25_full_directllm_minimax_imo_answerbench.yaml \
  -o run_id=imo_answerbench_direct_llm_qwen35_27b_localvllm_r1 \
  -o agent.config.model='Qwen/Qwen3.5-27B' \
  -o agent.config.api_base="$QWEN_VLLM_API_BASE" \
  -o agent.config.api_key="$QWEN_VLLM_API_KEY" \
  -o agent.config.temperature=0.0 \
  -o agent.config.top_p=0.95 \
  -o agent.config.stream=true
```

For benchmark-specific configs and full-run pointers (including the dedicated
logprob configs), see the per-benchmark pages, for example
[IMO-AnswerBench](../benchmarks/imo-answerbench). Each task is scored and the
record (`task_id`, `problem`, `ground_truth`, `predicted`, `correct`, `score`,
`trajectory`, `token_usage`, `wall_time_sec`, ...) is appended by the result store
at `alphadiana/analysis/io/result_store.py` to `results/<run_id>.jsonl`.
