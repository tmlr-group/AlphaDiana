# Analysis module map

Start with `analyzer.py` for the public saved-run analysis API. It reads persisted
records, computes statistics, optionally requests model annotations, and exports
reports and figures.

| Owner | Responsibility |
| --- | --- |
| `io/` | Load and normalize saved records, traces, statuses, and logprob artifacts |
| `run_summary.py` | Select latest samples and compute shared statistics and reliability |
| `entropy.py`, `resource_usage.py` | Measure entropy and execution resources |
| `annotations/` | Model-based action segmentation, failure annotations, and cached judging |
| `actions/` | Build action features, cluster trajectories, select cases, and render action reports |
| `insights/` | Assemble the comparison corpus, measurements, cases, and insight reports |
| `figures.py`, `reports.py`, `report.py` | Export analysis figures and reports |
| `scripts/` | Supported `python -m` analysis entry points |

`action_protocol.py` and `action_prompt.md` remain the single shared action
contract. The human audit also hashes these files. Default action annotations use
LLM segmentation of the full trajectory; explicit fixed-target audit inputs do
not change that default.

Older flat modules forward to their canonical owners to preserve Python imports.
Add implementation changes to the owners above, rather than the forwarding files.

Related workflows live outside this package:

- `scripts/action_audit/`: human annotation and agreement checks.
- `scripts/paper_analysis/`: archived paper inputs, plotting scripts, and figures.
- `alphadiana/dashboard/`: Dashboard backend and frontend.
