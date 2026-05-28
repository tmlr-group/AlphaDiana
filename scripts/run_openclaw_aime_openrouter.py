"""Generate local OpenClaw/OpenRouter configs for AIME 2024 and optionally run them."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

from alphadiana.utils.rock_ports import resolve_rock_ports_from_env


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_CONFIG = REPO_ROOT / "configs/examples/openclaw_aime2024.yaml"
DEPLOY_TEMPLATE = REPO_ROOT / "alphadiana/harness/openclaw/deploy/rock_agent_config.yaml"
GENERATED_DIR = REPO_ROOT / "dev/generated"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL_NAME = "qwen/qwen-2.5-7b-instruct"


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _resolve_proxy_api_base() -> str:
    env_proxy_url = os.environ.get("ROCK_PROXY_URL", "").strip()
    if env_proxy_url:
        return env_proxy_url.rstrip("/")
    return resolve_rock_ports_from_env().proxy_api_url


def _build_eval_config(
    template_path: Path,
    sandbox_id: str,
    author: str,
    sandbox_ids: list[str] | None = None,
) -> dict:
    if not template_path.exists():
        raise FileNotFoundError(f"Eval template file not found: {template_path}")
    data = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    proxy_api_base = _resolve_proxy_api_base()

    if sandbox_ids and len(sandbox_ids) > 1:
        # Multiple sandbox IDs: use gateway_pool for concurrent isolation.
        # Each concurrent slot routes to a different pre-deployed gateway,
        # preventing workspace-state.json.tmp contention.
        data["agent"]["config"].pop("api_base", None)
        data["agent"]["config"]["gateway_pool"] = [
            f"{proxy_api_base}/sandboxes/{sid}/proxy/v1"
            for sid in sandbox_ids
        ]
        data["max_concurrent"] = len(sandbox_ids)
        data["run_id"] = f"openclaw-qwen-2.5-7b-instruct-openrouter-aime2024-concurrent{len(sandbox_ids)}"
    else:
        # Single sandbox ID: sequential mode, no contention possible.
        data["agent"]["config"]["api_base"] = (
            f"{proxy_api_base}/sandboxes/{sandbox_id}/proxy/v1"
        )
        data["run_id"] = "openclaw-qwen-2.5-7b-instruct-openrouter-aime2024"

    data.setdefault("metadata", {})
    data["metadata"]["author"] = author
    data["agent"]["version"] = "2026.3.7"
    return data


def _build_deploy_config(template_path: Path, api_key: str) -> dict:
    data = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    data.setdefault("env", {})
    data["env"]["OPENAI_BASE_URL"] = OPENROUTER_BASE_URL
    data["env"]["OPENAI_API_KEY"] = api_key
    data["env"]["OPENAI_MODEL_NAME"] = MODEL_NAME
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sandbox-id",
        default=os.environ.get("OPENCLAW_SANDBOX_ID", ""),
        help="Single ROCK sandbox id (sequential mode). Defaults to OPENCLAW_SANDBOX_ID.",
    )
    parser.add_argument(
        "--sandbox-ids",
        default="",
        help=(
            "Comma-separated ROCK sandbox IDs for concurrent mode (e.g. id1,id2,id3). "
            "Enables gateway_pool so each concurrent slot uses a separate workspace. "
            "Deploy multiple sandboxes first, then pass all their IDs here."
        ),
    )
    parser.add_argument(
        "--author",
        default=os.environ.get("USER", "your_name"),
        help="Metadata author field.",
    )
    parser.add_argument(
        "--run",
        action="store_true",
        help="Run `alphadiana run` on the generated config after writing it.",
    )
    args = parser.parse_args()

    env_values = _load_dotenv(REPO_ROOT / ".env")
    api_key = env_values.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Missing OPENROUTER_API_KEY in .env or environment.", file=sys.stderr)
        return 1

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    deploy_config = _build_deploy_config(DEPLOY_TEMPLATE, api_key)

    deploy_path = GENERATED_DIR / "rock_agent_config.openrouter_qwen_2_5_7b.resolved.yaml"

    _write_yaml(deploy_path, deploy_config)

    print(f"Generated ROCK agent config: {deploy_path}")
    print(
        "Deploy command: "
        f"python alphadiana/harness/openclaw/deploy/deploy.py --agent-config {deploy_path}"
    )

    # Resolve sandbox IDs: --sandbox-ids takes precedence over --sandbox-id
    sandbox_ids_raw = args.sandbox_ids.strip()
    sandbox_ids: list[str] = [s.strip() for s in sandbox_ids_raw.split(",") if s.strip()] if sandbox_ids_raw else []
    primary_sandbox_id = sandbox_ids[0] if sandbox_ids else args.sandbox_id

    if sandbox_ids and len(sandbox_ids) > 1:
        print(f"Concurrent mode: {len(sandbox_ids)} sandboxes → gateway_pool + max_concurrent={len(sandbox_ids)}")

    eval_path: Path | None = None
    if primary_sandbox_id:
        eval_config = _build_eval_config(
            TEMPLATE_CONFIG, primary_sandbox_id, args.author,
            sandbox_ids=sandbox_ids if len(sandbox_ids) > 1 else None,
        )
        suffix = f"_concurrent{len(sandbox_ids)}" if len(sandbox_ids) > 1 else ""
        eval_path = GENERATED_DIR / f"openclaw_qwen25_7b_openrouter_aime2024{suffix}.resolved.yaml"
        _write_yaml(eval_path, eval_config)
        print(f"Generated eval config: {eval_path}")
        print(f"Run command: alphadiana run {eval_path}")
    else:
        print(
            "Eval config not generated yet. Re-run with:\n"
            "  --sandbox-id <ID>               (sequential)\n"
            "  --sandbox-ids <ID1,ID2,ID3>     (concurrent, 3 isolated gateways)"
        )

    if not args.run:
        return 0

    if eval_path is None:
        print("Cannot run evaluation without a sandbox id.", file=sys.stderr)
        return 1

    result = subprocess.run(["alphadiana", "run", str(eval_path)], cwd=REPO_ROOT)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
