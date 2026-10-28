---
name: review-fix-loop
description: Repeatedly review and repair an existing pull request until the same final HEAD passes three consecutive substantive reviews with no actionable findings. Use when asked to harden, repeatedly review, or autonomously finish a PR, or to write a durable goal prompt for that workflow. Do not use for a one-off review that should only report findings without changing code.
---

# Iterative PR Review and Fix

When reused from a parent repository such as AlphaHebe, target the user's named
repository and its guidance, base/head, commands, and contracts. Skill location
does not select the GitHub destination or authorize changes to dependencies.

Run a review/fix state machine over one existing pull request, or express the same state machine as a durable goal prompt when the user requests a prompt instead of execution.

## Establish authority and state

1. Resolve the repository, PR, base commit, head branch, local worktree, remote head, current reviews/comments/checks, and whether the PR is open and ready.
2. Read repository instructions and the complete local `review-pr` and `write-clear-code` skills when they exist. Use `review-pr` for every review round and `write-clear-code` for every repair. If either is unavailable, follow the equivalent repository review and clear-code conventions without inventing a dependency.
3. Preserve unrelated user changes. Do not merge, close, rebase, force-push, or rewrite history unless separately authorized.
4. Keep a noncommitted ledger outside the repository. Record each round's base, HEAD, lens, inspected surfaces, findings, fixes, tests, and clean-streak value.

## Review the whole change

Review the complete `base...HEAD` diff, not only the newest commit. Trace changed contracts into their real callers and consumers. Inspect tests and documentation alongside implementation.

Every round must consider:

- correctness, malformed inputs, boundary validation, failure and cancellation paths;
- identity, ordering, retries, partial success, concurrency, and repeated-call state;
- downstream callers, adapters, schemas, persistence, configuration, and public APIs;
- compatibility with adjacent modules and plausible follow-up features;
- security, privacy, resource cleanup, observability, performance regressions, and dependency scope;
- whether tests prove the intended behavior rather than merely exercise lines;
- whether PR text and committed artifacts accurately describe the final implementation.

Report only concrete, actionable findings supported by code or observable behavior. Do not create speculative requirements or expand the PR into unrelated refactoring. A latent issue is actionable when the PR creates or exposes it in a realistic downstream path.

## Fix every actionable finding

When a round finds any issue:

1. Set the clean streak to zero.
2. Repair it using the repository's `write-clear-code` guidance. Prefer the smallest change that establishes the correct invariant at the canonical boundary.
3. Add or strengthen regression tests that fail for the original defect and verify externally visible behavior.
4. Run proportionate targeted validation, then lint/format/type/schema checks required by the repository.
5. Commit an atomic, clearly named change and push it to the PR branch when the user authorized PR modification.
6. Refresh the remote PR state and begin a new review from the resulting HEAD. A successful test run after a fix is not a clean review round.

Never hide, waive, or merely document an actionable code problem to preserve the streak. If a problem truly cannot be solved within authority or scope, stop and report the exact blocker and evidence.

## Count clean reviews strictly

Stop only after three consecutive independent substantive review rounds find no actionable issue on the exact same final HEAD.

Use distinct lenses by default:

1. local correctness, invariants, and failure paths;
2. downstream integration, compatibility, and future consumers;
3. adversarial maintenance, portability, dependencies, artifacts, documentation, and release readiness.

Reset the streak to zero when any of the following occurs:

- a finding is discovered;
- code, tests, committed documentation, or artifacts change;
- a commit is added or the local/remote HEAD changes;
- the base changes in a way that affects the review;
- a new review comment, check failure, or unresolved relevant feedback appears;
- validation fails for a PR-caused reason;
- the worktree contains unexplained changes.

Do not count superficial rereads, test reruns, or formatting checks as independent reviews. Do not make empty commits to separate rounds. Reconcile stale PR metadata before beginning the final three-round streak; if a later review finds it stale, fix it and reset.

## Final acceptance gates

After the three clean rounds, verify without changing HEAD:

- local HEAD equals the PR remote head and both worktrees are clean;
- the base and PR state are still the reviewed ones;
- targeted and broad relevant tests pass on the final HEAD;
- repository lint, format, type, schema, compile, and minimum-version checks pass where applicable;
- dependency manifests and submodules changed only when intended;
- no credentials, private data, machine-specific paths, or unrelated generated files entered the diff;
- all relevant comments are addressed and the PR description reflects actual final evidence;
- the PR remains unmerged unless the user separately requested merging.

Return the PR URL, final HEAD, findings and repair commits, the three clean-round summaries, validation evidence, unresolved external limitations, and PR status.

## Goal-prompt mode

When asked to write a goal prompt, encode this entire state machine in the prompt. Include the target PR/worktree if known, authority boundaries, required repository skills, ledger requirement, strict reset rules, validation expectations, external-mutation limits, and verifiable completion gates. Make clear that planning or one successful review is not completion.
