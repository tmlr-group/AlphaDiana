---
name: analyze-insights
description: Mine mechanism-level insights from persisted AlphaDiana harness results. Use when analyzing benchmark artifacts for hidden variables, decisive falsifier measurements, model-harness behavior, trajectory/cost/error mechanisms, denominator-scoped support, deterministic case anchors, or paper-ready narrative replacements.
---

# Analyze Insights

## Workflow

1. Define the research object as a model-harness composite.
2. Build the denominator ledger before metrics.
3. Formulate the falsifier before computing measurements.
4. Use deterministic parsers and reviewable snippets.
5. Name the mechanism only after denominator checks.
6. Write the narrative replacement with caveats.

## Standard Command

```bash
python scripts/analyze_behavior_insights.py --results-dir results --output-dir results/phase15_behavior_insights
```

## Required Checks

- Inspect `results/phase15_behavior_insights/corpus_inventory.json`.
- Inspect `results/phase15_behavior_insights/insight_claims.json`.
- Inspect `results/phase15_behavior_insights/case_anchors.json`.
- Run `rg -n "/data0|/data2|/home/|sk-[A-Za-z0-9_-]{8,}|api[_-]?key" results/phase15_behavior_insights context/phase15-insight-analysis`.

## Do Not

- Do not launch live provider calls.
- Do not use LLM-assisted primary labeling.
- Do not make raw accuracy-only claims.
- Do not commit absolute paths.
