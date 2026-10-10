# Operator workflows

Run these commands from the repository root. Source `scripts/activate.sh` when
using the environment created by setup. `scripts/rock_env.sh` remains the shared
ROCK environment entry point.

| Directory | Purpose | Starting point |
| --- | --- | --- |
| `setup/` | Create the environment and start native dependencies | `bash scripts/setup/quickstart.sh` |
| `validation/` | Preflight, smoke tests, and evaluator audits | `python scripts/validation/validate_api_matrix.py --help` |
| `action_audit/` | Human action annotation and agreement | `scripts/action_audit/RUNBOOK.md` |
| `paper_analysis/` | Reproduce archived paper plots | `scripts/paper_analysis/README.md` |

Setup continues to store local environment state in `scripts/.alphadiana_env` and
`scripts/.rock_ports.env`. Runtime analysis implementation belongs to
`alphadiana/analysis/`; these directories contain operator and reproduction
workflows.
