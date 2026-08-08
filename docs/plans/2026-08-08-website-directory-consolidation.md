# Website Directory Consolidation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate every Docusaurus-owned source file under `website/` without changing generated routes or the Python runtime.

**Architecture:** `website/` becomes the Node/Docusaurus project root and contains `docs/`, `src/`, `static/`, configuration, and lockfiles. Root documentation links and website commands are updated to point at the new location.

**Tech Stack:** Docusaurus 3.9, React 19, TypeScript 5.6, Markdown/MDX, npm.

---

### Task 1: Move the Docusaurus project

**Files:**
- Move: `docs/` to `website/docs/`
- Move: `src/` to `website/src/`
- Move: `static/` to `website/static/`
- Move: `docusaurus.config.ts`, `sidebars.ts`, `package.json`, `package-lock.json`, and `tsconfig.json` to `website/`

**Steps:**

1. Move each tracked path without changing its content.
2. Confirm `git diff --summary` recognizes the operation as renames.
3. Confirm no Docusaurus-owned source directory remains at repository root.

### Task 2: Update repository-facing paths and commands

**Files:**
- Modify: `README.md`
- Modify: `website/docs/dashboard.md`
- Modify: config README files that link to documentation source paths

**Steps:**

1. Change root-relative documentation links to `website/docs/...`.
2. Change website build instructions to `cd website` followed by npm commands.
3. Update the documented repository tree.
4. Scan tracked files for stale references to the old root layout.

### Task 3: Verify the migration

**Steps:**

1. Run `npm run typecheck` from `website/`; expect exit code 0.
2. Run `npm run build` from `website/`; expect exit code 0 with no broken links.
3. Run `PYTHONPATH=. pytest tests -q` from repository root; expect all tests to pass.
4. Run `git diff --check` and inspect the final tracked tree.
5. Commit the verified directory migration separately from the design record.
