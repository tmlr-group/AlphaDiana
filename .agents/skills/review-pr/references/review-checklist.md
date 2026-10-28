# AlphaDiana PR Review Checklist

Use this checklist selectively. Spend most review effort on correctness and contracts; do not turn every item into boilerplate output.

## Correctness and failure paths

- Check branch conditions, boundary values, empty inputs, partial results, and exception paths.
- Verify state transitions, retries, idempotency, cancellation, timeouts, and resource cleanup.
- Check async or distributed code for ownership, ordering, races, duplicate work, and stale state.
- Confirm validation occurs before side effects and that failures do not leave corrupt artifacts.
- Trace serialization, copying, mutation, and object lifetime assumptions.

## Contracts and compatibility

- Preserve public Python APIs, CLI behavior, configuration keys/defaults, and serialized records unless a migration is explicit.
- Check imports and dependency direction across repository domains.
- For schema changes, inspect exporters, packaged JSON schemas, old-record compatibility, and round trips.
- For optional dependencies, ensure base installs still import and extras remain correctly declared.
- Read supported Python versions from the target repository's packaging and CI configuration.
- Treat external harnesses and pinned dependencies as external contracts; verify adapters rather than assuming upstream internals.

## Tests and CI

- Require a regression test for a fixed defect when practical.
- Ensure tests fail for the intended behavior and were not weakened to pass.
- Cover success, failure, cleanup, compatibility, and boundary paths according to risk.
- Prefer targeted tests first, then consider the repository CI commands from `AGENTS.md`.
- For schema or packaging changes, check schema export and installed-wheel resources.
- Record environment-dependent GPU, service, or integration tests that were not run.

## Security and operational risk

- Check shell, file, network, and deserialization inputs for injection or unsafe trust boundaries.
- Look for credential leakage, private paths, unsafe logging, destructive defaults, and unbounded resource use.
- Verify new retries, workers, caches, or queues have limits and cleanup behavior.
- Check that observability does not change semantics or expose sensitive data.

## Documentation and contribution quality

- Update documentation, examples, configuration, and migration notes for user-visible changes.
- Confirm the PR description states motivation, scope, non-goals, validation, and risks.
- Verify checked boxes and validation claims against the diff and observed command output.
- Check whether the linked issue and related-work search match the implemented scope.
- Keep unrelated refactors out of the PR or explain why they are inseparable.

## False-positive filter

Before reporting a finding, answer all of these:

1. What exact changed behavior introduces the problem?
2. What concrete input or state reaches it?
3. What user, runtime, or data impact follows?
4. What line should the author change?
5. Did the same problem already exist in the base revision?
6. Does nearby code or a test disprove the concern?

If the evidence remains uncertain, ask a focused question or omit the finding.
