from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from alphadiana.utils.rollout_campaign import (
    DEFAULT_MANIFEST_PATH,
    collect_preflight_failures,
    expand_campaign,
    filter_runs,
    preflight_campaign,
    render_preflight_report,
    render_run_command,
    render_validate_command,
    summarize_runs,
)

_WAVE_ORDER = {
    "wave_a_mainline": 0,
    "wave_b_official": 1,
    "wave_c_high_risk": 2,
    "wave_d_blocked": 3,
}
_RISK_ORDER = {
    "mainline": 0,
    "official_checkout": 1,
    "high_risk": 2,
    "blocked_but_requested": 3,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "summary":
        return _cmd_summary(args)
    if args.command == "preflight":
        return _cmd_preflight(args)
    if args.command == "commands":
        return _cmd_commands(args)
    if args.command == "materialize":
        return _cmd_materialize(args)
    parser.error(f"unknown command: {args.command}")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Campaign manifest path.",
    )
    shared.add_argument("--model", help="Filter by model id.")
    shared.add_argument("--benchmark", help="Filter by benchmark id.")
    shared.add_argument("--harness", help="Filter by harness id.")
    shared.add_argument("--wave", help="Filter by wave id.")
    shared.add_argument("--risk", help="Filter by risk id.")

    parser = argparse.ArgumentParser(
        description="Inspect and materialize the staged full-rollout campaign.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("summary", parents=[shared], help="Show the selected rollout matrix.")

    preflight_parser = subparsers.add_parser(
        "preflight",
        parents=[shared],
        help="Run rollout preflight checks.",
    )
    preflight_parser.add_argument("--probe-vllm", action="store_true", help="Probe /models on each vLLM endpoint.")
    preflight_parser.add_argument("--check-docker", action="store_true", help="Check Docker availability and required images.")
    preflight_parser.add_argument("--check-rock", action="store_true", help="Check ROCK admin/proxy/redis health.")
    preflight_parser.add_argument(
        "--skip-config-validation",
        action="store_true",
        help="Skip AlphaDiana config validation.",
    )

    commands_parser = subparsers.add_parser(
        "commands",
        parents=[shared],
        help="Print validate/run commands for the selected runs.",
    )
    commands_parser.add_argument(
        "--kind",
        choices=("validate", "run", "both"),
        default="both",
        help="Which command blocks to print.",
    )
    commands_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of shell blocks.",
    )

    materialize_parser = subparsers.add_parser(
        "materialize",
        parents=[shared],
        help="Write runnable shell scripts for the selected runs.",
    )
    materialize_parser.add_argument(
        "--kind",
        choices=("validate", "run", "both"),
        default="run",
        help="Which command scripts to write.",
    )
    materialize_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Output directory for generated scripts. Defaults under generated/rollout_campaigns/.",
    )

    return parser


def _selected_runs(args: argparse.Namespace):
    runs = expand_campaign(args.manifest)
    return _sort_runs(
        filter_runs(
            runs,
            model=args.model,
            benchmark=args.benchmark,
            harness=args.harness,
            wave=args.wave,
            risk=args.risk,
        )
    )


def _sort_runs(runs):
    return sorted(
        runs,
        key=lambda run: (
            _WAVE_ORDER.get(run.wave, 999),
            _RISK_ORDER.get(run.risk, 999),
            run.path.benchmark,
            run.path.harness,
            run.model.id,
        ),
    )


def _cmd_summary(args: argparse.Namespace) -> int:
    runs = _selected_runs(args)
    summary = summarize_runs(runs)

    print(f"Manifest: {args.manifest}")
    print(f"Selected runs: {summary['total_runs']}")

    if not runs:
        return 1

    print("")
    print("By wave:")
    for wave, count in sorted(summary["waves"].items(), key=lambda item: (_WAVE_ORDER.get(item[0], 999), item[0])):
        print(f"  {wave}: {count}")

    print("")
    print("By risk:")
    for risk, count in sorted(summary["risks"].items(), key=lambda item: (_RISK_ORDER.get(item[0], 999), item[0])):
        print(f"  {risk}: {count}")

    print("")
    print("Runs:")
    for run in runs:
        print(
            "  "
            f"{run.run_id} | {run.path.benchmark} | {run.path.harness} | {run.model.id} | "
            f"{run.wave} | {run.risk} | {run.path.backend}"
        )
    return 0


def _cmd_preflight(args: argparse.Namespace) -> int:
    results = preflight_campaign(
        args.manifest,
        probe_vllm=args.probe_vllm,
        check_docker=args.check_docker,
        check_rock=args.check_rock,
        validate_configs=not args.skip_config_validation,
    )
    print(render_preflight_report(results))
    failures = collect_preflight_failures(results)
    return 1 if failures else 0


def _cmd_commands(args: argparse.Namespace) -> int:
    runs = _selected_runs(args)
    if not runs:
        print("No runs matched the requested filters.")
        return 1

    if args.json:
        payload = []
        for run in runs:
            item = {
                "run_id": run.run_id,
                "benchmark": run.path.benchmark,
                "harness": run.path.harness,
                "model": run.model.id,
                "wave": run.wave,
                "risk": run.risk,
                "backend": run.path.backend,
            }
            if args.kind in {"validate", "both"}:
                item["validate"] = render_validate_command(run)
            if args.kind in {"run", "both"}:
                item["run"] = render_run_command(run)
            payload.append(item)
        print(json.dumps(payload, indent=2))
        return 0

    for index, run in enumerate(runs):
        if index:
            print("")
        print(f"# {run.run_id}")
        print(f"# benchmark={run.path.benchmark} harness={run.path.harness} model={run.model.id}")
        print(f"# wave={run.wave} risk={run.risk} backend={run.path.backend}")
        if run.risk_notes:
            print(f"# notes={'; '.join(run.risk_notes)}")
        if args.kind in {"validate", "both"}:
            validate_command = render_validate_command(run)
            if validate_command:
                print("")
                print("# validate")
                print(validate_command)
        if args.kind in {"run", "both"}:
            print("")
            print("# run")
            print(render_run_command(run))
    return 0


def _cmd_materialize(args: argparse.Namespace) -> int:
    runs = _selected_runs(args)
    if not runs:
        print("No runs matched the requested filters.")
        return 1

    campaign_id = runs[0].campaign_id
    output_dir = args.output_dir or Path("generated") / "rollout_campaigns" / campaign_id
    output_dir.mkdir(parents=True, exist_ok=True)

    created_paths: list[Path] = []
    for run in runs:
        created_paths.extend(_write_scripts_for_run(output_dir, run, kind=args.kind))

    for path in created_paths:
        print(path)
    return 0


def _write_scripts_for_run(output_dir: Path, run, *, kind: str) -> list[Path]:
    created: list[Path] = []

    if kind in {"validate", "both"}:
        validate_command = render_validate_command(run)
        if validate_command:
            validate_path = output_dir / f"validate__{run.run_id}.sh"
            validate_path.write_text(_script_body(run, validate_command, label="validate"), encoding="utf-8")
            validate_path.chmod(0o755)
            created.append(validate_path)

    if kind in {"run", "both"}:
        run_path = output_dir / f"run__{run.run_id}.sh"
        run_path.write_text(_script_body(run, render_run_command(run), label="run"), encoding="utf-8")
        run_path.chmod(0o755)
        created.append(run_path)

    return created


def _script_body(run, command: str, *, label: str) -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n\n"
        f"# run_id: {run.run_id}\n"
        f"# label: {label}\n"
        f"# benchmark: {run.path.benchmark}\n"
        f"# harness: {run.path.harness}\n"
        f"# model: {run.model.id}\n"
        f"# wave: {run.wave}\n"
        f"# risk: {run.risk}\n\n"
        f"{command}\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
