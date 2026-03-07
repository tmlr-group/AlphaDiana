"""CLI entry point for AlphaDiana."""

from __future__ import annotations

import os
import sys

import click


@click.group()
def main():
    """AlphaDiana - Evaluation system for foundation models and agent systems."""
    pass


@main.command()
@click.argument("config_yaml", type=click.Path(exists=True))
def run(config_yaml: str):
    """Run an evaluation experiment from a YAML config file."""
    from alphadiana.config.experiment_config import ExperimentConfig
    from alphadiana.runner.runner import Runner

    config = ExperimentConfig.from_yaml(config_yaml)
    runner = Runner(config)

    try:
        runner.setup()
        summary = runner.run()
        click.echo(f"\nRun completed: {summary.run_id}")
        click.echo(f"  Accuracy:   {summary.accuracy:.4f}")
        click.echo(f"  Mean Score: {summary.mean_score:.4f}")
        click.echo(f"  Tasks:      {summary.completed}/{summary.total_tasks} completed")
    except Exception as exc:
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
    from alphadiana.config.experiment_config import ExperimentConfig
    from alphadiana.results.report import ReportGenerator, RunSummary
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

        # Create a minimal config for report generation.
        config = ExperimentConfig(
            run_id=run_id,
            agent_name="unknown",
            agent_version="unknown",
            benchmark_name="unknown",
            scorer_name="unknown",
        )
        summary = report_gen.generate(store, config)
        markdown = report_gen.to_markdown(summary)
        click.echo(markdown)
        click.echo("")


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
