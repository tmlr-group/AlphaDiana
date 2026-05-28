#!/usr/bin/env python3
"""Count-abs / relative success-failure action matrix for Gemma4-31B.

Audited source:
  analyze_tools/data/six_action_statistics_gemma/action_counts_by_outcome.csv

Audit performed 2026-05-07:
  - Each (benchmark, harness, outcome) action-count sum equals event_total.
  - Unknown outcomes are included on the failure side, matching the requested
    sc/(sc+fc) calculation.
  - Scope: HLE, GPQA, and AIMEPass4 across DirectLLM/OpenClaw/OpenCode/ZeroClaw.
"""

from __future__ import annotations

from pathlib import Path

import plot_count_abs_rel_qwen as base


raw_table = r"""
| Benchmark | Harness | Outcome | Events | Problem Framing | Plan Formation | Solution Execution | Tool Grounding | Result Auditing | Answer Delivery |
|---|---|---|---|---|---|---|---|---|---|
| AIMEPass4 | DirectLLM | failure | 259 | 0 (0.00%) | 10 (3.86%) | 207 (79.92%) | 0 (0.00%) | 32 (12.36%) | 10 (3.86%) |
| AIMEPass4 | DirectLLM | success | 1401 | 6 (0.43%) | 49 (3.50%) | 1226 (87.51%) | 0 (0.00%) | 8 (0.57%) | 112 (7.99%) |
| AIMEPass4 | OpenClaw | failure | 31 | 0 (0.00%) | 3 (9.68%) | 7 (22.58%) | 19 (61.29%) | 0 (0.00%) | 2 (6.45%) |
| AIMEPass4 | OpenClaw | success | 1384 | 5 (0.36%) | 52 (3.76%) | 1076 (77.75%) | 134 (9.68%) | 3 (0.22%) | 114 (8.24%) |
| AIMEPass4 | OpenClaw | unknown | 34 | 0 (0.00%) | 0 (0.00%) | 4 (11.76%) | 29 (85.29%) | 0 (0.00%) | 1 (2.94%) |
| AIMEPass4 | OpenCode | failure | 33 | 0 (0.00%) | 7 (21.21%) | 10 (30.30%) | 14 (42.42%) | 0 (0.00%) | 2 (6.06%) |
| AIMEPass4 | OpenCode | success | 2155 | 14 (0.65%) | 142 (6.59%) | 1622 (75.27%) | 133 (6.17%) | 12 (0.56%) | 232 (10.77%) |
| AIMEPass4 | OpenCode | unknown | 834 | 7 (0.84%) | 48 (5.76%) | 630 (75.54%) | 78 (9.35%) | 68 (8.15%) | 3 (0.36%) |
| AIMEPass4 | ZeroClaw | failure | 376 | 0 (0.00%) | 31 (8.24%) | 314 (83.51%) | 0 (0.00%) | 28 (7.45%) | 3 (0.80%) |
| AIMEPass4 | ZeroClaw | success | 1470 | 4 (0.27%) | 103 (7.01%) | 1231 (83.74%) | 0 (0.00%) | 16 (1.09%) | 116 (7.89%) |
| GPQA | DirectLLM | failure | 250 | 18 (7.20%) | 22 (8.80%) | 175 (70.00%) | 0 (0.00%) | 7 (2.80%) | 28 (11.20%) |
| GPQA | DirectLLM | success | 1407 | 64 (4.55%) | 62 (4.41%) | 1106 (78.61%) | 0 (0.00%) | 10 (0.71%) | 165 (11.73%) |
| GPQA | OpenClaw | failure | 315 | 9 (2.86%) | 13 (4.13%) | 165 (52.38%) | 90 (28.57%) | 11 (3.49%) | 27 (8.57%) |
| GPQA | OpenClaw | success | 1483 | 66 (4.45%) | 70 (4.72%) | 1032 (69.59%) | 131 (8.83%) | 15 (1.01%) | 169 (11.40%) |
| GPQA | OpenClaw | unknown | 21 | 0 (0.00%) | 0 (0.00%) | 10 (47.62%) | 9 (42.86%) | 0 (0.00%) | 2 (9.52%) |
| GPQA | OpenCode | failure | 501 | 15 (2.99%) | 16 (3.19%) | 343 (68.46%) | 49 (9.78%) | 36 (7.19%) | 42 (8.38%) |
| GPQA | OpenCode | success | 2139 | 56 (2.62%) | 104 (4.86%) | 1454 (67.98%) | 143 (6.69%) | 34 (1.59%) | 348 (16.27%) |
| GPQA | ZeroClaw | failure | 346 | 9 (2.60%) | 17 (4.91%) | 206 (59.54%) | 0 (0.00%) | 91 (26.30%) | 23 (6.65%) |
| GPQA | ZeroClaw | success | 1706 | 60 (3.52%) | 91 (5.33%) | 1271 (74.50%) | 0 (0.00%) | 113 (6.62%) | 171 (10.02%) |
| HLE | DirectLLM | failure | 6278 | 166 (2.64%) | 227 (3.62%) | 5396 (85.95%) | 0 (0.00%) | 97 (1.55%) | 392 (6.24%) |
| HLE | DirectLLM | success | 1326 | 55 (4.15%) | 89 (6.71%) | 982 (74.06%) | 0 (0.00%) | 35 (2.64%) | 165 (12.44%) |
| HLE | OpenClaw | failure | 4621 | 107 (2.32%) | 180 (3.90%) | 2038 (44.10%) | 1827 (39.54%) | 100 (2.16%) | 369 (7.99%) |
| HLE | OpenClaw | success | 1369 | 35 (2.56%) | 46 (3.36%) | 781 (57.05%) | 335 (24.47%) | 29 (2.12%) | 143 (10.45%) |
| HLE | OpenCode | failure | 66430 | 630 (0.95%) | 10532 (15.85%) | 32782 (49.35%) | 5108 (7.69%) | 16580 (24.96%) | 798 (1.20%) |
| HLE | OpenCode | success | 2623 | 34 (1.30%) | 162 (6.18%) | 1544 (58.86%) | 555 (21.16%) | 44 (1.68%) | 284 (10.83%) |
| HLE | ZeroClaw | failure | 8594 | 96 (1.12%) | 253 (2.94%) | 6330 (73.66%) | 0 (0.00%) | 1532 (17.83%) | 383 (4.46%) |
| HLE | ZeroClaw | success | 1830 | 40 (2.19%) | 83 (4.54%) | 1105 (60.38%) | 0 (0.00%) | 430 (23.50%) | 172 (9.40%) |
"""


if __name__ == "__main__":
    base.raw_table = raw_table
    base.MODEL_NAME = "Gemma4-31B"
    base.OUTPUT_PATH = Path("analyze_tools/figures/count_abs_rel_gemma.pdf")
    base.CMAP_COLORS = ["#F8FBFF", "#DDEBFA", "#A9CDEC", "#477FB8"]
    base.plot()
