---
sidebar_position: 5
---

# Runtime Follow-ups for the Code Team

This is the consolidated implementation request from a bounded local
documentation-release smoke. The documentation work does not require these
changes to merge. Address the items together so operators do not need a series
of one-off runtime consultations.

## Scope and evidence

The real-API diagnostic covered DirectLLM plus ZeroClaw and OpenCode in
rootless Podman against one local small model and one AIME task. All three
reached scored result records and captured logprobs; answer correctness varied
and is not part of this runtime assessment. ZeroClaw and OpenCode preserved the
effective thinking request override. DirectLLM did not preserve equivalent
evidence, so its diagnostic proves transport/logprob execution only, not
effective thinking mode.

The findings below came from the failed setup attempts that preceded the
passing container path. Exact run IDs, counts, raw logs, and task artifacts are
local release evidence and are deliberately not published by this website-only
branch.

## P0: make the ZeroClaw bridge port real

### Reproduction

1. Occupy host port `8080`.
2. Run the ZeroClaw Podman AIME config with host networking and
   `-o agent.config.bridge_port=18080`.
3. The runtime reserves/probes `18080`, but `/tmp/zeroclaw-gateway.log` reports
   `OSError: [Errno 98] Address already in use` while binding `0.0.0.0:8080`.

### Root cause

- `alphadiana/harness/zeroclaw/runtime.py` reads `bridge_port` and uses it for
  `ports`, `exposed_port`, and the published API URL.
- `_runtime_env()` does not pass the port to the bridge process.
- `alphadiana/harness/zeroclaw/deploy/zeroclaw_bridge.py` sets `PORT = 8080`
  unconditionally.

### Requested change

Add a single documented environment variable, for example
`ZEROCLAW_BRIDGE_PORT`, populate it from `self._bridge_port`, parse and validate
it in the bridge, and use the same value for process bind, Podman exposure,
published URL, and health probe. The documentation change already adds
`bridge_port` to `configs/schema.yaml`; the runtime implementation, validation,
and ZeroClaw harness key table remain to be completed.

### Acceptance tests

- Unit test: `_runtime_env()` contains the configured port.
- Bridge test: a non-default valid port binds and serves `/v1/models`.
- Validation test: non-integer and out-of-range ports fail with an actionable
  error before a container starts.
- Integration test: with host `8080` occupied, a host-network Podman smoke on a
  free alternate port reaches readiness and cleans up the listener.

## P0: keep local Podman traffic out of outbound proxies

### Reproduction

1. Export loopback `http_proxy`/`https_proxy` values and a `NO_PROXY` list that
   does not include `host.containers.internal`.
2. Use rootless Podman, `slirp4netns:allow_host_loopback=true`, and logprob
   capture so the agent calls the host proxy through
   `host.containers.internal`.
3. The bridge returns `HTTP Error 502: Bad Gateway`; with proxy variables
   removed, the same task passes and reaches local vLLM.

### Root cause

`alphadiana/engine/container_runtime/proxy_env.py:podman_proxy_env()` can append
local hosts, but ZeroClaw calls it without `no_proxy_hosts`. The rewritten proxy
environment therefore does not reliably bypass the outbound proxy for the
Podman host alias. Upper- and lower-case proxy variables can also diverge.

### Requested change

Centralize the policy in `podman_proxy_env()` or its callers:

- append the selected host alias, provider host, loopback names, and any
  logprob-proxy advertise host to both `NO_PROXY` and `no_proxy`;
- preserve existing entries without duplicates;
- define deterministic behavior when upper- and lower-case proxy variables
  disagree;
- do not clear a user's outbound proxy globally.

ZeroClaw and OpenClaw both use this helper without supplying the local hosts;
fix those callers consistently. OpenCode follows a different policy and
actively removes proxy variables from its CLI environment. Review that path
separately and define when a local provider should bypass proxies versus when a
remote provider needs the user's outbound proxy; do not mechanically replace
its behavior with the same helper.

### Acceptance tests

- Table-driven unit tests cover empty, upper-only, lower-only, conflicting, and
  already-populated proxy environments.
- Rootless ZeroClaw and OpenClaw integration tests prove their containers can
  reach a host logprob proxy while an intentionally failing outbound proxy is
  exported.
- Separate OpenCode tests cover local-provider bypass and a remote provider
  that requires the user's outbound proxy.
- The suite confirms external provider proxy settings remain available for
  non-local hosts.

## P1: fail fast on incompatible image tags

### Reproduction

Two pre-existing local tags looked plausible but were incompatible:

- one had a task-specific entrypoint, so the detached agent container exited
  before runtime files could be copied;
- one contained the agent binary but no Python, so launching the Python bridge
  failed after the binary-only install check.

### Requested change

Before waiting for gateway readiness, validate the complete runtime contract:

- the container stays alive under the runtime's command override;
- `/bin/sh`, Python, the harness binary, and required Python modules exist;
- the image architecture and agent version are recorded;
- startup errors include image ID/digest, configured tag, entrypoint/CMD, failed
  preflight command, and the process log tail, with credentials redacted.

For ZeroClaw, expand `install_commands` beyond `zeroclaw --version` to include
the interpreter used by `run_command` and an import check. Prefer immutable
digests in released smoke manifests while retaining human-readable tags.

### Acceptance tests

- Fixture images for an exiting entrypoint, missing Python, missing agent
  binary, and healthy runtime each fail/pass at the expected stage.
- Every failure is immediate, preserves sanitized diagnostics, removes the
  owned container, and leaves no listener behind.

## P1: make pilot launch status fail closed

`scripts/run_podman_scale_readiness.sh:run_config()` records the child exit code
but always returns success. A CLI run also permits task error records unless
`strict_report` is enabled. This is useful for evidence collection but unsafe
for a readiness gate.

Add an explicit `gate` or `all` scope that runs every pilot cell, invokes the
auditor after collection, and returns the aggregate audit status. Keep `pilot`
available as a collection-only scope, but label that behavior in its output.
The gate must return nonzero when any child command failed, an expected task
JSON or required provenance/artifact is missing, or the audit reports a
non-`valid_scored` row. Its final summary must distinguish setup failure, task
error, `score=0`, and `score=1`; a wrong scored answer must not be classified as
infrastructure failure.

Acceptance requires separate fixtures for a child command failure, a passing
scored row, a scored-zero row, an invalid task row, a missing result, and
missing provenance/artifact. Assert that all cells are attempted, the status
TSV and audit report are still written, and only a complete set of
`valid_scored` rows (regardless of score 0/1) produces a zero gate exit status.

## P1: align OpenClaw image and matrix versions

`alphadiana/harness/openclaw/deploy/Dockerfile` installs
`openclaw@2026.3.7`, while every standard scale-readiness OpenClaw config
declares `agent.version: 2026.3.20`. Config validation does not verify the
container binary, so a matrix can appear well-described while running a
different version.

Choose and pin one supported OpenClaw version in the image and matrix configs.
Record the image digest plus `openclaw --version` in the run manifest, and fail
preflight when it disagrees with `agent.version`. Acceptance requires a unit
test for version parsing and a Podman preflight test for matching and
mismatching images.

## Delivery checklist

- Update runtime code, schema, harness docs, and readiness runbook together.
- Add unit and rootless-Podman integration coverage for every item above.
- Preserve the current result-list format and inspect `data[0]` in tests.
- Preserve raw logs and sanitized intermediate artifacts on failure.
- Do not change Docker/ROCK defaults or make a global Podman support claim as
  part of this request.
