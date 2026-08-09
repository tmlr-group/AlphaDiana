# Memory extension (not a paper micro table)

Memory is a runnable AlphaDiana framework extension. It is **not** one of the
paper's reported micro ablations: the current paper tables cover Tool exposure
and Skill loading only. Do not cite this directory as paper-result coverage.

Within this separate extension, the release provides a complete 3-harness ×
3-scope AIME/Qwen reference matrix.

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
