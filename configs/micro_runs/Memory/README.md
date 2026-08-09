# Memory axis

Memory is the complete micro reference matrix in this release.

## Canonical 9-cell matrix

The canonical matrix fixes AIME 2026 and Qwen3.5-27B, then crosses all three
harnesses with all three memory scopes:

| Harness | `intra_task` | `cross_sample` | `cross_task` |
| --- | :---: | :---: | :---: |
| OpenClaw | ✓ | ✓ | ✓ |
| OpenCode | ✓ | ✓ | ✓ |
| ZeroClaw | ✓ | ✓ | ✓ |

Each canonical filename is
`aime2026_{openclaw|opencode|zeroclaw}_qwen35_27b.yaml` inside its scope folder.

## Supplemental intra-task cells

`intra_task/` also contains six older Kimi/GPQA launch definitions for
OpenClaw and ZeroClaw. They are retained for reproducibility, but they are not
part of the canonical 9-cell matrix and do not form a complete cross-harness
matrix.
