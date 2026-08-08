# Website Directory Consolidation Design

## Decision

Move the complete Docusaurus project into one top-level `website/` directory. The
directory owns the documentation content, React pages and components, static
assets, Docusaurus configuration, sidebar configuration, TypeScript configuration,
and Node dependency manifests.

```text
website/
├── docs/
├── src/
├── static/
├── docusaurus.config.ts
├── sidebars.ts
├── package.json
├── package-lock.json
└── tsconfig.json
```

The Python package, experiment configs, runtime scripts, tests, README, license,
and installer remain at the repository root.

## Rationale and alternatives

Keeping the current split is conventional for a standalone Docusaurus repository,
but it makes the root of this mixed Python/website repository look like several
unrelated products. Moving only `src/` and `static/` below the existing `docs/`
content tree would mix executable website implementation with Markdown content
and blur Docusaurus plugin boundaries. A dedicated `website/` project root keeps
those boundaries explicit while reducing root-level clutter.

## Compatibility

The generated routes remain unchanged: documentation stays below `/docs/`, the
custom homepage stays at `/`, and static images remain below `/img/`. Website
commands run from `website/`; repository documentation must show that working
directory explicitly. Relative Markdown links in the root README must be updated
from `docs/...` to `website/docs/...`.

## Verification

- `cd website && npm run typecheck`
- `cd website && npm run build`
- scan tracked files for stale root-level website paths and commands
- run the Python test suite to show the packaging-only move did not affect runtime
  behavior
