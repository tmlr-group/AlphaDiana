# Memory extension (not a paper micro table)

Memory is a runnable AlphaDiana framework extension. It is **not** one of the
paper's reported micro ablations: the current paper tables cover Tool exposure
and Skill loading only. Do not cite this directory as paper-result coverage.

Within this separate extension, the release provides only the uniform
3-harness × 3-scope AIME/Qwen reference matrix. Historical Kimi and GPQA
one-scope configs are intentionally excluded from the public release.

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

## OpenClaw embedding service

The three OpenClaw cells use the native `memory-lancedb` plugin and therefore
need an OpenAI-compatible embedding endpoint in addition to the generation
endpoint. Export these values before `alphadiana run`:

```bash
export MEMORY_EMBEDDING_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:PORT/v1
export MEMORY_EMBEDDING_API_KEY=sk-EMPTY
export MEMORY_EMBEDDING_MODEL=<embedding-model-id>
```

The configured embedding model must return 1024-dimensional vectors and accept
the OpenAI `dimensions: 1024` request field. OpenCode memory uses native session
continuation, while ZeroClaw uses its built-in SQLite memory, so those six cells
do not require this separate endpoint.
