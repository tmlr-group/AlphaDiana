# direct_llm (Baseline)

`DirectLLMAgent` in `alphadiana/harness/direct_llm/agent.py` calls an OpenAI-compatible provider directly. It is the baseline for measuring provider/model behavior without a separate agent CLI or harness tool loop.

## Configuration

Common `agent.config` keys are:

| Key | Behavior |
| --- | --- |
| `model` | Provider model; falls back to `OPENAI_MODEL_NAME` |
| `api_base` | OpenAI-compatible base URL; falls back to `OPENAI_BASE_URL` |
| `api_key` | Credential; falls back to `OPENAI_API_KEY` |
| `temperature`, `top_p` | Sampling controls |
| `max_tokens` | Explicit output budget; can be auto-resolved from `/models` when omitted |
| `stream` | Streaming request mode |
| `request_timeout` | Request/idle timeout input |
| `stream_total_timeout` | Total streaming budget |
| `max_retries` | Retry count for retryable provider failures |
| `system_prompt` | Optional system message |
| `capture_logprobs` / `top_logprobs` | Provider logprob capture |
| `logprobs_format` | Accepts `float` or `int16`; persistence retains both raw and compact evidence |

The provider client uses `trust_env=False`, so inherited `ALL_PROXY`, `HTTP_PROXY`, and `HTTPS_PROXY` values do not silently redirect the request. Configure the intended provider URL explicitly.

## Request and answer flow

The harness builds system/user messages, sends a streaming or non-streaming chat-completion request, captures provider content/reasoning/usage, and extracts a final answer candidate. If content is empty but a separate reasoning field exists, answer extraction can fall back to that reasoning text.

When `max_tokens` is omitted, the harness can query the provider's model metadata and derive a budget. Explicit config remains the most predictable choice for a release-critical run.

## Retries and timeouts

Retryable provider failures use exponential backoff with jitter up to `max_retries`. Non-retryable failures are preserved as errors.

When the total streaming budget is exhausted, DirectLLM returns `answer=None`, `finish_reason: timeout`, and `directllm_timeout_scored_zero=true`. The scorer records zero, so the sample is `valid_scored` and checkpoint-complete. This differs from provider validation errors and context overflow, which remain rerunnable failures.

## Logprob capture

Raw records are passed to `ResultStore`, which writes the float sidecar and derives a compact Int16 sidecar. Both the default `logprobs_format: int16` and explicit `float` use this persistence boundary.

DirectLLM no longer replaces raw records with Int16 values before persistence. This fixes double quantization in the default mode; historical damaged sidecars cannot recover missing candidate data without their original raw evidence.

`stream_total_timeout` starts before sending a streaming request, includes the response-header wait and all SSE reads, and also bounds the unsupported-`stream_options` fallback. Expiry cancels the request and closes its connection. Partial text, reasoning, usage and logprobs already observed remain saved, while the final answer follows the existing timeout-as-zero contract. Client setup and cleanup are outside the request budget. Non-streaming requests continue to use `request_timeout`.

## Example

```yaml
agent:
  name: direct_llm
  config:
    model: ${OPENAI_MODEL_NAME}
    api_base: ${OPENAI_BASE_URL}
    api_key: ${OPENAI_API_KEY}
    temperature: 0
    max_tokens: 4096
    stream: true
    capture_logprobs: true
    top_logprobs: 20
    logprobs_format: float
```

Validate and run through the project CLI:

```bash
python -m alphadiana.cli validate path/to/config.yaml
python -m alphadiana.cli run path/to/config.yaml
```

Use a checked-in benchmark config or the benchmark runbook for a concrete command. A successful config validation proves schema compatibility, not provider reachability or support; inspect the real task record and raw run log.

## Artifacts to inspect

- task record: `score_status`, `finish_reason`, token usage, and logprob metadata;
- float/Int16 sidecar references when enabled;
- request/response envelope and normalized trajectory;
- raw run log for retry, timeout, or provider failures.

## Related pages

- [Harnesses Overview](./)
- [Observability & Proxies](../architecture/observability.md)
- [Scoring & Results](../architecture/scoring-and-results.md)
