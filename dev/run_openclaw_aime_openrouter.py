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
DEPLOY_TEMPLATE = REPO_ROOT / "openclaw_deploy/rock_agent_config.yaml"
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


def _build_eval_config(template_path: Path, sandbox_id: str, author: str) -> dict:
    if not template_path.exists():
        raise FileNotFoundError(f"Eval template file not found: {template_path}")
    data = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    proxy_api_base = _resolve_proxy_api_base()
    data["agent"]["config"]["api_base"] = (
        f"{proxy_api_base}/sandboxes/{sandbox_id}/proxy/v1"
    )
    data.setdefault("metadata", {})
    data["metadata"]["author"] = author
    data["run_id"] = "openclaw-qwen-2.5-7b-instruct-openrouter-aime2024"
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
        help="ROCK sandbox id. Defaults to OPENCLAW_SANDBOX_ID.",
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
        f"python openclaw_deploy/deploy.py --agent-config {deploy_path}"
    )
    eval_path: Path | None = None
    if args.sandbox_id:
        eval_config = _build_eval_config(TEMPLATE_CONFIG, args.sandbox_id, args.author)
        eval_path = GENERATED_DIR / "openclaw_qwen25_7b_openrouter_aime2024.resolved.yaml"
        _write_yaml(eval_path, eval_config)
        print(f"Generated eval config: {eval_path}")
        print(f"Run command: alphadiana run {eval_path}")
    else:
        print("Eval config not generated yet. Re-run with --sandbox-id <SANDBOX_ID> after deployment.")

    if not args.run:
        return 0

    if eval_path is None:
        print("Cannot run evaluation without a sandbox id.", file=sys.stderr)
        return 1

    result = subprocess.run(["alphadiana", "run", str(eval_path)], cwd=REPO_ROOT)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
