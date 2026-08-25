# Pi + Harbor for Terminal-Bench 2

`terminal_bench2_pi_harbor` integrates an exact local Pi runtime with Harbor's
Terminal-Bench 2 execution and verifier lifecycle. Harbor remains authoritative
for task selection, the disposable Docker task environment, timeouts, and the
official verifier reward. AlphaDiana owns orchestration, persistence, reporting,
and optional post-run observations.

## Execution boundary

This adapter is the evaluation layer of the Pi Meta-Harness adaptation. The
optimizer owns candidate generation and best-frontier selection; AlphaDiana
does not silently turn the run into the original Meta-Harness protocol. See
the [evaluation contract](../architecture/meta-harness-evaluation.md) for the
search-space, parent-policy, split, and proposer-workflow deviations.

Each AlphaDiana work item `(task, sample_index)` gets a unique Harbor job:

```text
AlphaDiana Runner
  -> harbor run --include-task-name <task> --n-attempts 1
    -> fresh Terminal-Bench 2 Docker task container
      -> read-only local Node + Pi runtime mounts
      -> explicit audit extension + explicit candidate extension
      -> official Harbor verifier
  -> completed Harbor result.json and agent artifacts
  -> terminal_bench2 primary scorer
  -> optional AlphaDiana metrics over deep-copied result snapshots
```

Pi runs with `--no-session`, `--no-extensions`, `--no-skills`,
`--no-prompt-templates`, `--no-themes`, and `--no-context-files`. Only the two
explicit extension files are loaded. Models configuration is copied into the
task container. AlphaDiana itself and its metric plugins are not installed in
the agent runtime.

The local Node executable and Pi installation are mounted read-only. The task
container, HOME, processes, and workspace are created by Harbor for that one
trial. Unique job names include a fresh solve-attempt nonce, so neither another
sample nor an AlphaDiana retry can attach to an earlier job. The checked-in
command uses `--no-delete` so completed evidence survives;
this preserves artifacts, not live agent state, and preserved containers are
never reused by this adapter.

This is practical task-container isolation, not a formal security guarantee.
The container receives the configured model credential and may have network
access. Host Docker, Harbor, bind mounts, and the chosen task image remain part
of the trusted computing base.

## Configuration

Start from `configs/macro_runs/terminal_bench2_pi_harbor.yaml`:

```yaml
agent:
  name: terminal_bench2_pi_harbor
  version: '1.0'
  config:
    harbor_bin: harbor
    jobs_dir: "${ALPHADIANA_PI_HARBOR_JOBS_DIR}"
    pi_install_path: "${META_HARNESS_PI_INSTALL}"
    node_bin_path: "${META_HARNESS_NODE_BIN}"
    models_path: "${PI_MODELS_PATH}"
    candidate_path: "${PI_EXTENSION_PATH}"
    pi_version: "<frozen Pi build identifier>"
    pi_model: "luna-local/gpt-5.6-luna"
    harbor_model: "luna-local/gpt-5.6-luna"
    reasoning: max
    dataset: terminal-bench@2.0
benchmark:
  name: terminal_bench2
  config:
    tasks_dir: "${TERMINAL_BENCH2_DIR}"
    harbor_task_index: true
scorer:
  name: terminal_bench2
metrics:
  - name: answer_length
    config:
      source: final_answer
```

Required setup:

```bash
# The command shape is validated against Harbor 0.13.1.
uv tool install 'harbor==0.13.1'

export TERMINAL_BENCH2_DIR=/path/to/local/terminal-bench/tasks
export META_HARNESS_PI_INSTALL=/path/to/exact/pi-install
export META_HARNESS_NODE_BIN=/path/to/exact/node
export PI_MODELS_PATH=/path/to/models.json
export PI_EXTENSION_PATH=/path/to/candidate.ts
export ALPHADIANA_PI_HARBOR_JOBS_DIR=/path/to/harbor-jobs
export OPENAI_API_KEY=<model-endpoint-key>

alphadiana validate configs/macro_runs/terminal_bench2_pi_harbor.yaml
alphadiana run configs/macro_runs/terminal_bench2_pi_harbor.yaml
```

If `candidate_path` is omitted or an environment placeholder resolves to an
empty string, the adapter loads the included no-op Pi baseline. `audit_path`
and `compose_path` also have checked-in defaults. Do not configure an
AlphaDiana `sandbox`: this adapter already owns the Harbor task container.

With `harbor_task_index: true`, the local `tasks_dir` only lets AlphaDiana
enumerate the same task names and instructions that Harbor selects from
`terminal-bench@2.0`. AlphaDiana deliberately does not interpret local image,
timeout, or verifier configuration in this mode. For every returned trial, the
adapter rejects a mismatch between AlphaDiana's expected task name and Harbor's
`task_name`. Category filtering is unavailable because the Harbor snapshot does
not expose that metadata in the task index.

## Score and observations

Harbor's numeric `verifier_result.rewards.reward` is imported as the response
used by the existing `terminal_bench2` scorer. If a completed Harbor trial has
no finite numeric reward, the adapter supplies protocol score zero. This is the
reference Meta-Harness TB2 rule: every trial directory remains in the flat
denominator, including errored trials and trials without `verifier_result`.
The adapter does not edit Harbor's `result.json` or fabricate `reward.txt`;
`verifier_reward_observed=false`, `harbor_verifier_status`, and
`reward_normalization` preserve the distinction. The answer shown to the model
is never used as the correctness label.

The `answer_length` demo instead reads Pi's final visible assistant text from
`pi.jsonl`. Thinking blocks are excluded. It records Unicode code points as the
primary value and also records UTF-8 bytes, whitespace-delimited words, and
line count as metadata.

Metrics execute only after the primary scorer. Each metric gets deep-copied
task, response, and score snapshots. A metric exception is persisted under
`evaluation_metric_errors`; it cannot change the official score or turn a
valid trial into a failed task. Numeric observations are summarized separately
in CLI, Markdown reports, and dashboard responses. They are aggregated from
any record carrying the observation, independently of score validity.

## Evidence and failure semantics

AlphaDiana references completed Harbor artifacts by absolute path, byte size,
SHA-256, and the `read_only_post_run` access contract. Typical evidence is:

- `result.json`;
- `agent/pi.jsonl`;
- `agent/request-audit.jsonl`;
- `agent/mechanism-firings.jsonl`;
- `verifier/reward.txt`, when produced;
- `verifier/test-stdout.txt`, `trial.log`, and `exception.txt`, when produced.

The manifest also fingerprints the candidate and audit extensions, models
configuration (hash only), compose overlay, Node binary, Pi entrypoint, and Pi
package lock when present. This makes the exact evaluation inputs reviewable
without copying the credential-bearing models file into AlphaDiana results.

The request audit is revalidated after Harbor exits. Every captured provider
request must use the configured model ID and reasoning effort. Malformed or
mismatched provenance and explicit provider errors are stored as error records
with completed artifacts preserved. Harbor runtime exceptions and nonzero exits
remain observable; when they end without a numeric reward, their primary TB2
outcome is nevertheless zero under the reference flat-denominator rule.

Harbor's `AgentTimeoutError` remains a scored TB2 outcome (normally zero), even
if no provider request completed. A verifier timeout may happen after an agent
timeout; Harbor 0.13.1 keeps the first exception in `exception_info`, so the
adapter also records `harbor_verifier_timeout_observed` from `trial.log`.

## Validation levels

The focused integration tests simulate frozen Harbor directories and do not
call a model. They verify import, scoring, provider failure handling, timeout
handling, request provenance, metric isolation, result persistence, report
aggregation, dashboard aggregation, and config parsing.

A real smoke additionally requires a working Docker daemon, Harbor's dataset
access, the exact local Pi/Node files, the model configuration, and endpoint
credentials. It incurs model cost; run one selected task before scaling out.
