---
name: prepare-contribution
description: Drafts, checks, creates, and updates AlphaDiana GitHub issues and pull requests using repository templates, duplicate searches, and evidence from the codebase. Use when asked to file, write, polish, validate, open, or update a bug report, feature proposal, engineering issue, PR title, PR description, or contribution-readiness check.
---

# Prepare AlphaDiana Contributions

When reused from a parent repository such as AlphaHebe, target the user's named
repository and its guidance, base/head, commands, and contracts. Skill location
does not select the GitHub destination or authorize changes to dependencies.

Produce a submission-ready Issue or PR grounded in repository evidence. Draft by default. Create, edit, close, or otherwise mutate a GitHub artifact only when the user explicitly requests that action; an explicit request is sufficient confirmation.

## 1. Load the source of truth

1. Read `AGENTS.md` and any nearer repository guidance.
2. Read the target repository's matching file under `.github/ISSUE_TEMPLATE/` or `.github/PULL_REQUEST_TEMPLATE.md`. If absent, say so; for PRs use the template shipped alongside these skills, and for issues use problem, scope, proposed change, acceptance criteria, validation, and related work. Never invent a missing template.
3. Inspect the relevant code, documentation, configuration, tests, or branch diff.
4. When updating an existing artifact, read its current title, body, discussion, labels, and linked work before proposing changes.

Do not replace missing facts with plausible details. Mark them as missing.

## 2. Search before writing

Search open and closed issues and PRs using distinctive component names, error text, or intended outcomes. Prefer repository-scoped GitHub search or `gh issue list` and `gh pr list` when available.

- Link likely duplicates or overlapping work.
- Do not call two reports duplicates based on title alone; compare behavior, scope, and acceptance criteria.
- For a PR, find the governing issue or explain why a separate issue is unnecessary.
- Record the query or state when search access is unavailable.

## 3. Choose the contribution type

- Use the bug form for reproducible incorrect behavior.
- Use the feature form for features, refactors, or scoped engineering work.
- Use the PR template for an implemented change or a branch that is ready for review.

If the request mixes unrelated work, recommend separate issues or PRs.

## 4. Build the contribution

For an Issue:

- Identify the affected component and current behavior from code or documentation.
- Reduce bug reports to numbered reproduction steps and record the exact error.
- Turn proposals into an observable outcome, explicit non-goals, and verifiable acceptance criteria.
- Link related issues and PRs found during the search.
- Include versions, configuration, logs, or links only when known and sanitized.

For a PR:

- Determine the actual base branch; use `origin/main` only when it is the real base.
- Inspect committed and uncommitted changes, changed tests, and user-facing artifacts.
- Explain the motivation and design rather than paraphrasing filenames.
- Record exact validation commands and their observed results. Write `Not run — <reason>` for skipped checks.
- Link the governing issue with `Closes #...` or explain why no issue is needed.
- Call out API, schema, configuration, dependency, runtime, and migration risks.
- Follow every PR-template section and mark a checklist item complete only when its statement is true.

Ask before running checks that require GPUs, large downloads, credentials, or long external jobs. Run proportionate local checks when the user asks to make the contribution ready.

## 5. Check readiness

Reject or flag drafts that have any of these gaps:

- no concrete problem or motivation;
- no reproduction for a bug;
- no acceptance criteria for a proposal;
- no linked issue or explanation in a PR;
- no duplicate or related-work search, unless search access was unavailable and this is disclosed;
- test claims without command evidence;
- hidden breaking changes, unclear non-goals, or unrelated changes;
- secrets, private data, or machine-specific paths.

## 6. Return or publish

Return, in order:

1. a concise title;
2. a Markdown body matching the repository template;
3. `Missing information` with only unresolved facts, omitted when empty;
4. `Readiness` as `Ready` or `Not ready`, with short reasons.

When checking an existing draft, list concrete gaps first, then provide a corrected draft. Preserve the author's intent and avoid unnecessary rewriting.

When the user explicitly requests creation or an update:

1. verify the repository and target issue or PR;
2. apply only the prepared title/body/metadata changes;
3. return the resulting URL and summarize any fields that remain incomplete.
