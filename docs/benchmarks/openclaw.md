# OpenClaw Benchmark Reliability Runbook

This note summarizes the current recommended OpenClaw settings for long
local-vLLM benchmark runs and the result contract reviewers should enforce.

## Recommended Defaults

Use fresh-per-task predeployed ROCK sandboxes for full benchmark runs:

```yaml
max_concurrent: 1
task_retries: 2
task_retry_on_recoverable_only: true

agent:
  name: openclaw
  config:
    max_tokens: 131072
    request_timeout: 9300
    stream_idle_timeout: 9300
    stream_total_timeout: 9000

    num_sandboxes: 1
    standby_sandboxes: 1
    reuse_predeployed_sandboxes: false
    predeployed_lease_probe: true

    capture_logprobs: true
    top_logprobs: 20
```

For `max_concurrent: 2`, use two active fresh sandboxes and two standby
sandboxes:

```yaml
max_concurrent: 2
agent:
  config:
    num_sandboxes: 2
    standby_sandboxes: 2
    reuse_predeployed_sandboxes: false
```

Keep concurrency low unless the local vLLM queue is healthy. On the current
local `Qwen/Qwen3.5-27B` host, long thinking-on samples can run near
`30 tokens/sec`; a `131072` token cap can therefore need more than an hour for a
single task after agent/tool overhead.

## Why Fresh Per Task

`reuse_predeployed_sandboxes: false` gives every task a fresh OpenClaw
gateway/session. After a task writes its result, the runner closes that sandbox
and warms a replacement standby sandbox. This prevents stale OpenClaw chat
history from leaking into later tasks.

The older reuse mode still exists with `reuse_predeployed_sandboxes: true`, but
it is not the recommended full-run mode. If reuse is enabled, runner reset must
clear OpenClaw session state under the known OpenClaw home directories between
tasks.

## Heartbeat Policy

Do not implement liveness checks by sending heartbeat prompts into the OpenClaw
model session. Heartbeats should be passive operator logs, watchdog status
files, or shell monitor output outside the model conversation.

If a wrapper needs an operational heartbeat while a long run is active, write it
to a separate monitor log such as:

```text
logs/<run_id>.monitor.log
```

Keep the benchmark shell log under:

```text
logs/<run_id>.log
```

## Result Validity Contract

A normal scored OpenClaw result is valid only when all of these are true:

- `score_status == "valid_scored"`
- `metadata.received_done is True`
- `metadata.session_tainted is not True`
- trajectory/raw output do not contain `Read HEARTBEAT.md`, `HEARTBEAT.md`, or
  `HEARTBEAT_OK`
- when logprobs are enabled, both float and int16 logprob sidecars exist

The runner now rejects OpenClaw responses before scoring when the integrity
guard sees any of these conditions:

- `metadata.received_done is False`
- `metadata.session_tainted is True`
- `finish_reason == "incomplete"`
- heartbeat markers in trajectory, request/response payloads, or raw output

Rejected responses are written with `score_status=runtime_error`. The partial
raw output, trajectory, response JSON, sandbox artifacts, and logprob sidecars
are still preserved when they were available.

## Retry Semantics

Use:

```yaml
task_retries: 2
task_retry_on_recoverable_only: true
```

This retries task failures only when the runner has evidence that the sandbox or
gateway died, such as connection refused, sandbox not alive, or control-plane
unavailable. It does not blindly retry ordinary scored-wrong answers, output-cap
answers, or expected task timeouts.

Checkpoint resume is scorer-aware. `python -m alphadiana.cli run <config>`
skips only latest records that are completed for the configured scorer. Latest
`runtime_error` records remain checkpoint-rerunnable.

## Existing Mixed Runs

Runs that contain records produced before the fresh-per-task and integrity-guard
fixes should be treated as audit evidence, not final accuracy evidence. For a
formal benchmark number, start a new run ID from current code.

If compute must be reused, append correction records for any latest sample with
`received_done=False`, heartbeat traces, or `session_tainted=True` so checkpoint
resume reruns those samples. A clean run is still preferred because stale-session
taint can be hard to detect perfectly after the fact.

## Operator Checklist

Before launch:

- run `python scripts/security_guard.py --check`
- confirm the intended `OPENAI_BASE_URL`, `OPENAI_MODEL_NAME`, and local ROCK
  ports after `source scripts/activate.sh`
- use a fresh run ID for final accuracy
- run `python -m alphadiana.cli validate <config>`

During launch:

- write the foreground command through `tee logs/<run_id>.log`
- write wrapper heartbeats to `logs/<run_id>.monitor.log`, not the benchmark log
- monitor vLLM `num_requests_running`, `num_requests_waiting`, and token counters

After launch:

- inspect `results/<run_id>.jsonl`
- inspect `results/<run_id>/tasks/*.json`; remember task files are sample lists
- verify the result validity contract above before reporting accuracy
