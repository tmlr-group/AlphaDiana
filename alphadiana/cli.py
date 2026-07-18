"""CLI entry point for AlphaDiana."""

from __future__ import annotations

import os
import sys

import logging

import click

logger = logging.getLogger(__name__)

_PROXY_VARS = ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
               "all_proxy", "http_proxy", "https_proxy")


def _ownership_failures(results: dict[str, bool | str]) -> dict[str, str]:
    return {svc: str(status) for svc, status in results.items() if status is not True}


def _warn_proxy() -> bool:
    """Check for proxy environment variables and warn if present.

    Returns True if any proxy variable is set, False otherwise.
    Suggests sourcing rock_env.sh to clean the environment.
    """
    found = {k: os.environ[k] for k in _PROXY_VARS if k in os.environ}
    if found:
        names = ", ".join(sorted(found))
        logger.warning(
            "Proxy variables detected: %s. This may cause network issues "
            "inside ROCK sandboxes. Run 'source scripts/rock_env.sh' to unset them.",
            names,
        )
        return True
    return False


def _preflight_terminal_bench2(config) -> None:
    if getattr(config, "benchmark_name", "") != "terminal_bench2":
        return
    from alphadiana.benchmarks.terminal_bench2.benchmark import TerminalBench2Benchmark

    tasks = TerminalBench2Benchmark().load_tasks(config.benchmark_config)
    click.echo(f"Terminal-Bench-2 tasks loaded: {len(tasks)}")


@click.group()
def main():
    """AlphaDiana - Evaluation system for foundation models and agent systems."""
    pass


@main.command()
@click.argument("config_yaml", type=click.Path(exists=True))
@click.option(
    "--override", "-o",
    multiple=True,
    help="Override config values, e.g. -o agent.config.temperature=0.5",
)
@click.option("--redo-all", is_flag=True, default=False, help="Ignore checkpoint and redo all tasks.")
def run(config_yaml: str, override: tuple[str, ...], redo_all: bool):
    """Run an evaluation experiment from a YAML config file."""
    from alphadiana.engine.config.experiment_config import ExperimentConfig, deep_merge, parse_override
    from alphadiana.engine.runner import Runner, _is_gateway_autodeploy_agent

    overrides: dict = {}
    for ov in override:
        overrides = deep_merge(overrides, parse_override(ov))

    if redo_all:
        overrides = deep_merge(overrides, {"redo_all": True})
    config = ExperimentConfig.from_yaml(config_yaml, overrides=overrides or None)

    # Validate config before running.
    from alphadiana.engine.config.validator import ConfigValidator
    validator = ConfigValidator()
    errors = validator.validate(config)
    if errors:
        click.echo("Config validation failed:", err=True)
        for error in errors:
            click.echo(f"  - {error}", err=True)
        sys.exit(1)
    _emit_config_warnings(validator, config, err=True)
    try:
        _preflight_terminal_bench2(config)
    except Exception as exc:
        click.echo(f"Terminal-Bench-2 preflight failed: {exc}", err=True)
        sys.exit(1)

    _warn_proxy()

    # Pre-flight: verify ROCK services only for ROCK-backed runs.
    uses_rock_runtime = (
        config.sandbox_name == "rock"
        or _is_gateway_autodeploy_agent(config)
    )
    if uses_rock_runtime:
        from alphadiana.utils.rock_ports import (
            check_rock_service_ownership,
            check_rock_services,
            resolve_rock_ports_from_env,
        )
        ports = resolve_rock_ports_from_env()
        click.echo(f"Pre-flight: checking ROCK services (admin={ports.admin_port}, proxy={ports.proxy_port}, redis={ports.redis_port})...")
        results = check_rock_services(ports, timeout=5.0)
        failures = {k: v for k, v in results.items() if v is not True and k != "docker"}
        ownership = check_rock_service_ownership(ports)
        ownership_failures = _ownership_failures(ownership)
        if failures:
            click.echo("Pre-flight FAILED — ROCK services not reachable:", err=True)
            for svc, err in failures.items():
                click.echo(f"  ✗ {svc}: {err}", err=True)
            click.echo("\nRun 'alphadiana env' to see full status and setup instructions.", err=True)
            sys.exit(1)
        if ownership_failures:
            click.echo("Pre-flight FAILED — configured ROCK ports belong to another checkout:", err=True)
            for svc, err in ownership_failures.items():
                click.echo(f"  ✗ {svc}: {err}", err=True)
            click.echo(
                "\nFix: regenerate isolated ports with "
                "'python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env' "
                "and restart ROCK services from this checkout.",
                err=True,
            )
            sys.exit(1)
        click.echo("Pre-flight passed: admin ✓  proxy ✓  redis ✓")

    runner = Runner(config)

    try:
        runner.setup()
        summary = runner.run()
        click.echo(f"\nRun completed: {summary.run_id}")
        click.echo(f"  Accuracy:   {summary.accuracy:.4f}")
        click.echo(f"  Mean Score: {summary.mean_score:.4f}")
        click.echo(f"  Pass@{summary.num_samples}:    {summary.pass_at_k:.4f}")
        click.echo(f"  Avg@{summary.num_samples}:     {summary.avg_at_k:.4f}")
        click.echo(f"  Tasks:      {summary.completed}/{summary.total_tasks} completed")
        if config.benchmark_name == "decodingtrust" or config.scorer_name == "decodingtrust":
            click.echo(
                "  (DTAP headline metrics below; Accuracy above is AlphaDiana's "
                "blended score, not DTAP task success)"
            )
            click.echo(f"  DT Valid Records: {summary.dt_valid_records}")
            click.echo(
                "  DT Task Success (utility): "
                f"{summary.dt_task_success_count}/{summary.dt_task_success_denominator} = "
                f"{summary.dt_task_success_rate:.4f}"
            )
            click.echo(
                "  DT Attack Success (ASR): "
                f"{summary.dt_attack_success_count}/{summary.dt_attack_success_denominator} = "
                f"{summary.dt_attack_success_rate:.4f}"
            )
        if config.strict_report and summary.strict_report_failed:
            click.echo(
                "Strict report failed: " + ", ".join(summary.strict_report_issues),
                err=True,
            )
            sys.exit(1)
    except Exception as exc:
        logger.exception("Run failed")
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    finally:
        runner.teardown()


@main.command()
@click.argument("config_yaml", type=click.Path(exists=True))
@click.option(
    "--override", "-o",
    multiple=True,
    help="Override config values, e.g. -o agent.config.temperature=0.5",
)
def validate(config_yaml: str, override: tuple[str, ...]):
    """Validate a YAML config file without running an experiment."""
    from alphadiana.engine.config.experiment_config import ExperimentConfig, deep_merge, parse_override
    from alphadiana.engine.config.validator import ConfigValidator

    overrides: dict = {}
    for ov in override:
        overrides = deep_merge(overrides, parse_override(ov))

    config = ExperimentConfig.from_yaml(config_yaml, overrides=overrides or None)
    validator = ConfigValidator()
    errors = validator.validate(config)

    if errors:
        click.echo("Config validation failed:")
        for error in errors:
            click.echo(f"  - {error}")
        sys.exit(1)
    _emit_config_warnings(validator, config)
    try:
        _preflight_terminal_bench2(config)
    except Exception as exc:
        click.echo(f"Terminal-Bench-2 preflight failed: {exc}")
        sys.exit(1)
    click.echo("Config is valid.")


@main.command()
@click.argument("results_dir", type=click.Path(exists=True))
def report(results_dir: str):
    """Generate reports from existing result files in a directory."""
    from alphadiana.analysis.report import ReportGenerator
    from alphadiana.analysis.io.result_store import ResultStore

    jsonl_files = [
        f for f in os.listdir(results_dir) if f.endswith(".jsonl")
    ]

    if not jsonl_files:
        click.echo("No .jsonl result files found in the directory.")
        return

    report_gen = ReportGenerator()
    for jsonl_file in sorted(jsonl_files):
        run_id = jsonl_file.replace(".jsonl", "")
        store = ResultStore(output_dir=results_dir, run_id=run_id)
        results = store.load()
        if not results:
            click.echo(f"Skipping {jsonl_file} (empty)")
            continue

        summary = report_gen.generate(store)
        markdown = report_gen.to_markdown(summary)
        click.echo(markdown)
        if summary.strict_report_failed:
            click.echo(
                "WARNING: " + ", ".join(summary.strict_report_issues)
            )
        click.echo("")


@main.command()
@click.argument("config_yamls", nargs=-1, required=True, type=click.Path(exists=True))
@click.option("--parallel", is_flag=True, help="Run experiments in parallel.")
@click.option(
    "--override", "-o",
    multiple=True,
    help="Override config values applied to all configs.",
)
def batch(config_yamls: tuple[str, ...], parallel: bool, override: tuple[str, ...]):
    """Run multiple experiment configs sequentially or in parallel."""
    from alphadiana.engine.config.experiment_config import ExperimentConfig, deep_merge, parse_override
    from alphadiana.engine.config.validator import ConfigValidator
    from alphadiana.engine.batch_runner import BatchRunner

    overrides: dict = {}
    for ov in override:
        overrides = deep_merge(overrides, parse_override(ov))

    validator = ConfigValidator()
    configs = [
        ExperimentConfig.from_yaml(p, overrides=overrides or None)
        for p in config_yamls
    ]
    validation_errors: list[str] = []
    validation_warnings: list[str] = []
    for config in configs:
        errors = validator.validate(config)
        validation_errors.extend(f"{config.run_id}: {error}" for error in errors)
        validation_warnings.extend(
            f"{config.run_id}: {warning}" for warning in validator.warnings(config)
        )
    if validation_errors:
        raise click.ClickException(
            "Config validation failed:\n" + "\n".join(f"  - {error}" for error in validation_errors)
        )
    if validation_warnings:
        click.echo("Config validation warnings:")
        for warning in validation_warnings:
            click.echo(f"  - {warning}")

    runner = BatchRunner(configs, parallel=parallel)
    summaries = runner.run()

    for summary in summaries:
        if summary is None:
            click.echo("  [FAILED]")
        else:
            click.echo(f"  {summary.run_id}: accuracy={summary.accuracy:.4f}")


def _emit_config_warnings(validator, config, *, err: bool = False) -> None:
    warnings = validator.warnings(config)
    if not warnings:
        return
    click.echo("Config validation warnings:", err=err)
    for warning in warnings:
        click.echo(f"  - {warning}", err=err)


@main.command()
def env():
    """Check ROCK environment status and service connectivity.

    Verifies that all required ROCK services (Redis, Ray, Admin, Proxy)
    are reachable. Run this before 'alphadiana run' with OpenClaw configs
    to catch connection issues early.

    If services are not running, prints the commands needed to start them.
    """
    from alphadiana.utils.rock_ports import (
        check_rock_service_ownership,
        resolve_rock_ports_from_env,
        check_rock_services,
        default_rock_redis_container,
        _find_rock_ports_env_file,
    )

    ports = resolve_rock_ports_from_env()
    ports_file = _find_rock_ports_env_file()
    rock_root = os.environ.get("ALPHADIANA_ROCK_ROOT", "ref/ROCK")
    redis_container = os.environ.get("ROCK_REDIS_CONTAINER", default_rock_redis_container())

    click.echo("ROCK Environment Status")
    click.echo("=" * 50)
    click.echo(f"  Ports file:  {ports_file or 'NOT FOUND'}")
    click.echo(f"  ROCK root:   {rock_root}")
    click.echo(f"  Admin:       {ports.base_url}")
    click.echo(f"  Proxy:       {ports.proxy_root_url}")
    click.echo(f"  Redis:       {LOCALHOST}:{ports.redis_port}")
    click.echo(f"  Ray:         {LOCALHOST}:{ports.ray_port}")
    click.echo()

    click.echo("Service Health Checks")
    click.echo("-" * 50)
    results = check_rock_services(ports)
    all_ok = True
    for service, status in results.items():
        if status is True:
            click.echo(f"  ✓ {service}")
        else:
            click.echo(f"  ✗ {service}: {status}")
            all_ok = False

    click.echo()
    click.echo("Ownership Checks")
    click.echo("-" * 50)
    ownership = check_rock_service_ownership(ports)
    ownership_failures = _ownership_failures(ownership)
    for service, status in ownership.items():
        if status is True:
            click.echo(f"  ✓ {service}")
        else:
            click.echo(f"  ✗ {service}: {status}")
            all_ok = False

    click.echo()
    if all_ok:
        click.echo("All services healthy. Ready for OpenClaw evaluation.")
    else:
        click.echo("Some services are unreachable.")
        click.echo()
        if ownership_failures:
            click.echo("Isolation fix:")
            click.echo("  python scripts/find_rock_ports.py --write-env scripts/.rock_ports.env")
            click.echo("  source scripts/activate.sh")
            click.echo("  bash scripts/start_openclaw.sh   # or bash scripts/start_zeroclaw.sh")
            click.echo()
        click.echo("To start services, run:")
        click.echo("  bash scripts/start_openclaw.sh   # or bash scripts/start_zeroclaw.sh")
        click.echo("  # both paths run scripts/security_guard.py --check first")
        click.echo()
        click.echo("Or start manually (remember to run scripts/security_guard.py --check first):")
        click.echo(f"  # Redis")
        click.echo(
            f"  docker run -d --name {redis_container} -p 127.0.0.1:{ports.redis_port}:6379 "
            "redis/redis-stack-server:latest"
        )
        click.echo(f"  # Ray")
        click.echo(
            f"  cd {rock_root} && ray start --head --port={ports.ray_port} "
            f"--dashboard-port={ports.ray_dashboard_port} --disable-usage-stats"
        )
        click.echo(f"  # Admin")
        click.echo(
            f"  cd {rock_root} && python -m rock.admin.main --env local-proxy --role admin "
            f"--port {ports.admin_port} &"
        )
        click.echo(f"  # Proxy")
        click.echo(
            f"  cd {rock_root} && python -m rock.admin.main --env local-proxy --role proxy "
            f"--port {ports.proxy_port} &"
        )
        sys.exit(1)


LOCALHOST = "127.0.0.1"


@main.command("list-benchmarks")
def list_benchmarks():
    """List all registered benchmarks."""
    # Import benchmark modules to trigger registration.
    import alphadiana.benchmarks.aime.benchmark  # noqa: F401
    import alphadiana.benchmarks.custom.benchmark  # noqa: F401
    import alphadiana.benchmarks.decodingtrust.benchmark  # noqa: F401
    import alphadiana.benchmarks.gpqa.benchmark  # noqa: F401
    import alphadiana.benchmarks.hle.benchmark  # noqa: F401
    import alphadiana.benchmarks.imo.benchmark  # noqa: F401
    import alphadiana.benchmarks.mmmu_pro.benchmark  # noqa: F401
    import alphadiana.benchmarks.swe_bench.benchmark  # noqa: F401
    import alphadiana.benchmarks.external_benchmark.benchmark  # noqa: F401
    import alphadiana.benchmarks.external_benchmark.qjl  # noqa: F401
    import alphadiana.benchmarks.swebench_pro.benchmark  # noqa: F401
    import alphadiana.benchmarks.terminal_bench2.benchmark  # noqa: F401

    from alphadiana.benchmarks.registry import BenchmarkRegistry

    benchmarks = BenchmarkRegistry.list()
    if benchmarks:
        click.echo("Registered benchmarks:")
        for name in benchmarks:
            click.echo(f"  - {name}")
    else:
        click.echo("No benchmarks registered.")


if __name__ == "__main__":
    main()
