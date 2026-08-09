# Tool axis — paper Table 2

These configs reproduce the Tool experiment reported in Table 2 of the
[AlphaDiana paper](https://openreview.net/forum?id=4vARlk9o95). The paper fixes
two harnesses, two models, and two benchmarks, then compares two conditions:

- **Full Harness**: the harness exposes its native tool registry.
- **Minimal Harness**: `T_filter` strips the tool registry and applies the
  matching harness-specific prompt transformation.

OpenClaw is not part of Table 2 and therefore has no Tool config here.

## Paper matrix

| Harness | Model | GPQA-Diamond | AIME 2026 |
|---|---|---:|---:|
| ZeroClaw | Qwen3.5-27B | Full + Minimal | Full + Minimal |
| OpenCode | Qwen3.5-27B | Full + Minimal | Full + Minimal |
| ZeroClaw | Kimi-K2.6 | Full + Minimal | Full + Minimal |
| OpenCode | Kimi-K2.6 | Full + Minimal | Full + Minimal |

That is 8 reference cells and 16 runnable YAML files. Filenames end in
`_tool_full.yaml` or `_tool_minimal.yaml`.

## Minimal-harness proxy

Minimal configs use `TOOL_FILTER_BASE_URL`, not `OPENAI_BASE_URL`. Start one
proxy for the harness being evaluated; the upstream remains the model endpoint:

```bash
python -m alphadiana.harness.proxies.tool_filter_proxy \
  --upstream "$OPENAI_BASE_URL" --api-key "$OPENAI_API_KEY" --port 9050 \
  --block '.*' --harness-strip zeroclaw
export TOOL_FILTER_BASE_URL=http://HOST_REACHABLE_FROM_SANDBOX:9050/v1
```

Use `--harness-strip opencode` for OpenCode. Keep `--block '.*'`: prompt
stripping alone does not remove advertised tools. For ZeroClaw + Kimi, also
pass `--rename-reasoning`. The proxy is part of the intervention: pointing a
Minimal config directly at the provider would silently run the Full condition.
