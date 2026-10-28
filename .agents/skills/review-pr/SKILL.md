---
name: review-pr
description: Reviews AlphaDiana pull requests and local change sets using base-branch comparison, repository contracts, and evidence-first findings. Use when asked to review a PR URL or number, assess a branch or diff before merge, find correctness or compatibility problems, evaluate tests, or check whether an AlphaDiana change is safe to merge.
---

# Review AlphaDiana Pull Requests

When reused from a parent repository such as AlphaHebe, target the user's named
repository and its guidance, base/head, commands, and contracts. Skill location
does not select the GitHub destination or authorize changes to dependencies.

Find actionable defects introduced by the change. Review the implementation independently: treat the PR description, checkboxes, CI status, and prior AI reviews as claims to verify, not substitutes for reading the code. Prioritize correctness and compatibility over summaries, formatting, and generic advice.

Read [references/review-checklist.md](references/review-checklist.md) before reviewing the diff.

## 1. Establish the review target

1. Read `AGENTS.md`, the PR description, linked issue, and relevant nested guidance.
2. Determine the actual base and head revisions. Do not assume that the local checkout or `main` is current.
3. Obtain the PR diff and changed-file list. Use GitHub metadata, a `.diff` URL, or local Git refs according to available tools.
4. Compare the stated scope, validation, and checklist with the actual diff. Record unavailable metadata or checks instead of silently guessing.

Fetching refs is allowed during a requested review. Do not check out branches, edit files, publish comments, or approve/reject the PR unless the user explicitly requests that action.

## 2. Understand the change

- Read every changed hunk, then inspect the complete changed functions and relevant callers.
- Compare modified behavior with the base revision to distinguish introduced defects from pre-existing code.
- Trace contracts across the target repository's actual domain boundaries (in Diana: `engine`, `harness`, `benchmarks`, and `scorer`) when boundaries are affected.
- Inspect changed and nearby tests. Check whether assertions can fail for the intended reason.
- Treat generated files and vendored code as evidence, not the primary review surface.
- Check for an overlapping open PR when the change appears to duplicate active work; report overlap as coordination context, not a code defect.

For large PRs, review by risk-bearing behavior rather than file order.

## 3. Validate candidate findings

Report a finding only when all are true:

- the change introduces or exposes the problem;
- a concrete input, state, or execution path reaches it;
- the impact is observable and relevant;
- the cited line is in the diff, or the base-branch cross-check is clearly identified;
- the proposed fix addresses the cause without relying on speculation.

Trace suspicious behavior before reporting it. Do not invent findings to make the review look complete. Do not report style preferences unless they create a defect or violate an enforced repository rule.

## 4. Run proportionate checks

Start with targeted tests and static checks for changed files. Expand toward the commands in `AGENTS.md` when feasible. Ask before GPU, credentialed, network-heavy, or long-running validation.

Distinguish clearly among:

- checks run and passed;
- checks run and failed;
- checks not run, with the reason.

A passing test suite does not replace code-path review.

## 5. Write the review

Start with findings ordered by severity. Use this shape for each finding:

```text
[P1] Short imperative title — path/to/file.py:123
Impact: Concrete failure scenario and affected behavior.
Evidence: Why this change causes the failure, including relevant contract or base-branch evidence.
Suggested fix: Smallest practical correction or test.
```

Use `P0` for catastrophic release-blocking failures, `P1` for high-impact merge blockers, `P2` for real defects with limited impact, and `P3` only for minor actionable problems. Avoid padding with low-value `P3` findings.

After findings, include:

- `Questions` only for uncertainties that materially affect correctness;
- `Validation` with commands actually run and residual gaps;
- `Summary` in no more than three sentences.

If no qualifying findings exist, say `No actionable findings.` and still state untested or residual risks.
