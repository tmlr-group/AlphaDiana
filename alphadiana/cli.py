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
            "inside ROCK sandboxes. Run 'source dev/rock_env.sh' to unset them.",
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
    _warn_proxy()
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
def validate(config_yaml: str):
    """Validate a YAML config file without running an experiment."""
    from alphadiana.config.experiment_config import ExperimentConfig
    from alphadiana.config.validator import ConfigValidator

    config = ExperimentConfig.from_yaml(config_yaml)
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


@main.command("list-benchmarks")
def list_benchmarks():
    """List all registered benchmarks."""
    # Import benchmark modules to trigger registration.
    import alphadiana.benchmark.aime  # noqa: F401
    import alphadiana.benchmark.math_bench  # noqa: F401
    import alphadiana.benchmark.hle  # noqa: F401
    import alphadiana.benchmark.frontier_math  # noqa: F401
    import alphadiana.benchmark.terminal_bench  # noqa: F401
    import alphadiana.benchmark.osworld  # noqa: F401

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
