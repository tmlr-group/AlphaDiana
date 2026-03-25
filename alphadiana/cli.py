"""CLI entry point for AlphaDiana."""

from __future__ import annotations

import os
import sys

import logging

import click

logger = logging.getLogger(__name__)

_PROXY_VARS = ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY",
               "all_proxy", "http_proxy", "https_proxy")


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
    from alphadiana.config.experiment_config import ExperimentConfig, deep_merge, parse_override
    from alphadiana.runner.runner import Runner

    overrides: dict = {}
    for ov in override:
        overrides = deep_merge(overrides, parse_override(ov))

    if redo_all:
        overrides = deep_merge(overrides, {"redo_all": True})
    config = ExperimentConfig.from_yaml(config_yaml, overrides=overrides or None)

    # Validate config before running.
    from alphadiana.config.validator import ConfigValidator
    validator = ConfigValidator()
    errors = validator.validate(config)
    if errors:
        click.echo("Config validation failed:", err=True)
        for error in errors:
            click.echo(f"  - {error}", err=True)
        sys.exit(1)

    _warn_proxy()

    # Pre-flight: verify ROCK services are reachable for openclaw runs.
    if config.agent_name == "openclaw":
        from alphadiana.utils.rock_ports import resolve_rock_ports_from_env, check_rock_services
        ports = resolve_rock_ports_from_env()
        click.echo(f"Pre-flight: checking ROCK services (admin={ports.admin_port}, proxy={ports.proxy_port}, redis={ports.redis_port})...")
        results = check_rock_services(ports, timeout=5.0)
        failures = {k: v for k, v in results.items() if v is not True and k != "docker"}
        if failures:
            click.echo("Pre-flight FAILED — ROCK services not reachable:", err=True)
            for svc, err in failures.items():
                click.echo(f"  ✗ {svc}: {err}", err=True)
            click.echo("\nRun 'alphadiana env' to see full status and setup instructions.", err=True)
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
    from alphadiana.config.experiment_config import ExperimentConfig, deep_merge, parse_override
    from alphadiana.config.validator import ConfigValidator

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
    else:
        click.echo("Config is valid.")


@main.command()
@click.argument("results_dir", type=click.Path(exists=True))
def report(results_dir: str):
    """Generate reports from existing result files in a directory."""
    from alphadiana.results.report import ReportGenerator
    from alphadiana.results.result_store import ResultStore

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
    from alphadiana.config.experiment_config import ExperimentConfig, deep_merge, parse_override
    from alphadiana.runner.batch_runner import BatchRunner

    overrides: dict = {}
    for ov in override:
        overrides = deep_merge(overrides, parse_override(ov))

    configs = [
        ExperimentConfig.from_yaml(p, overrides=overrides or None)
        for p in config_yamls
    ]

    runner = BatchRunner(configs, parallel=parallel)
    summaries = runner.run()

    for summary in summaries:
        if summary is None:
            click.echo("  [FAILED]")
        else:
            click.echo(f"  {summary.run_id}: accuracy={summary.accuracy:.4f}")


@main.command()
def env():
    """Check ROCK environment status and service connectivity.

    Verifies that all required ROCK services (Redis, Ray, Admin, Proxy)
    are reachable. Run this before 'alphadiana run' with OpenClaw configs
    to catch connection issues early.

    If services are not running, prints the commands needed to start them.
    """
    from alphadiana.utils.rock_ports import (
        resolve_rock_ports_from_env,
        check_rock_services,
        _find_rock_ports_env_file,
    )

    ports = resolve_rock_ports_from_env()
    ports_file = _find_rock_ports_env_file()

    click.echo("ROCK Environment Status")
    click.echo("=" * 50)
    click.echo(f"  Ports file:  {ports_file or 'NOT FOUND'}")
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
    if all_ok:
        click.echo("All services healthy. Ready for OpenClaw evaluation.")
    else:
        click.echo("Some services are unreachable.")
        click.echo()
        click.echo("To start services, run:")
        click.echo("  bash scripts/quickstart.sh")
        click.echo()
        click.echo("Or start manually:")
        click.echo(f"  # Redis")
        click.echo(f"  docker run -d --name redis-stack -p {ports.redis_port}:6379 redis/redis-stack-server:latest")
        click.echo(f"  # Ray")
        click.echo(f"  cd ref/ROCK && ray start --head --port={ports.ray_port} --dashboard-port={ports.ray_dashboard_port} --disable-usage-stats")
        click.echo(f"  # Admin")
        click.echo(f"  cd ref/ROCK && python -m rock.admin.main --env local-proxy --role admin --port {ports.admin_port} &")
        click.echo(f"  # Proxy")
        click.echo(f"  cd ref/ROCK && python -m rock.admin.main --env local-proxy --role proxy --port {ports.proxy_port} &")
        sys.exit(1)


LOCALHOST = "127.0.0.1"


@main.command("list-benchmarks")
def list_benchmarks():
    """List all registered benchmarks."""
    # Import benchmark modules to trigger registration.
    import alphadiana.benchmark.aime  # noqa: F401
    import alphadiana.benchmark.custom  # noqa: F401

    from alphadiana.benchmark.registry import BenchmarkRegistry

    benchmarks = BenchmarkRegistry.list()
    if benchmarks:
        click.echo("Registered benchmarks:")
        for name in benchmarks:
            click.echo(f"  - {name}")
    else:
        click.echo("No benchmarks registered.")


if __name__ == "__main__":
    main()
