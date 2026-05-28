"""Generate local OpenClaw/OpenRouter configs for HMMT and optionally run them."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_CONFIG = REPO_ROOT / "configs/examples/openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.yaml"
DEPLOY_TEMPLATE = REPO_ROOT / "alphadiana/harness/openclaw/deploy/rock_agent_config.yaml"
GENERATED_DIR = REPO_ROOT / "dev/generated"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MODEL_NAME = "openai/gpt-oss-120b:free"


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        values[key] = value
    return values


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _build_eval_config(template_path: Path, sandbox_id: str, author: str) -> dict:
    data = yaml.safe_load(template_path.read_text(encoding="utf-8"))
    api_base = data["agent"]["config"]["api_base"]
    data["agent"]["config"]["api_base"] = api_base.replace("<SANDBOX_ID>", sandbox_id)
    data.setdefault("metadata", {})
    data["metadata"]["author"] = author
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

    if not args.sandbox_id:
        print("Missing sandbox id. Pass --sandbox-id or set OPENCLAW_SANDBOX_ID.", file=sys.stderr)
        return 1

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    eval_config = _build_eval_config(TEMPLATE_CONFIG, args.sandbox_id, args.author)
    deploy_config = _build_deploy_config(DEPLOY_TEMPLATE, api_key)

    eval_path = GENERATED_DIR / "openclaw_hmmt_nov2025_openrouter_gpt_oss_120b.local.yaml"
    deploy_path = GENERATED_DIR / "rock_agent_config.openrouter_gpt_oss_120b.local.yaml"

    _write_yaml(eval_path, eval_config)
    _write_yaml(deploy_path, deploy_config)

    print(f"Generated eval config: {eval_path}")
    print(f"Generated ROCK agent config: {deploy_path}")
    print(f"OpenClaw config template: {REPO_ROOT / 'alphadiana/harness/openclaw/deploy/openclaw.json'}")
    print(f"Run command: alphadiana run {eval_path}")

    if not args.run:
        return 0

    result = subprocess.run(["alphadiana", "run", str(eval_path)], cwd=REPO_ROOT)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
