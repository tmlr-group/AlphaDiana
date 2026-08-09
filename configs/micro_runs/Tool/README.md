# Tool axis

This directory preserves runnable Tool-axis launch definitions. It is a partial
release set, not a claim that every paper Tool cell was executed or reported.

The current files span AIME 2026 and GPQA-Diamond, Qwen3.5-27B and Kimi-K2.6,
and OpenClaw, OpenCode, and ZeroClaw. That Cartesian-looking file layout is a
configuration inventory only. Paper completeness must be established from the
reported experiment table and its run artifacts, not by counting YAML files.

Each cell keeps native tools available while avoiding explicit instructions to
use them. A matched Tool ablation also needs its corresponding tool-off
condition, identical model and sampling controls, and auditable result records.
Those matched pairs are not fully represented by this directory alone.
