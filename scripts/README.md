# Operator workflows

Run these commands from the repository root. Source `scripts/activate.sh` when using the environment created by setup. `scripts/rock_env.sh` remains the shared ROCK environment entry point.

| Directory | Purpose | Starting point |
| --- | --- | --- |
| `setup/` | Create the environment and start native dependencies | `bash scripts/setup/quickstart.sh` |
| `validation/` | Preflight, smoke tests, and evaluator audits | `python scripts/validation/validate_api_matrix.py --help` |

Setup continues to store local environment state in `scripts/.alphadiana_env` and `scripts/.rock_ports.env`. Runtime analysis implementation belongs to `alphadiana/analysis/`; these directories contain setup and validation workflows.
