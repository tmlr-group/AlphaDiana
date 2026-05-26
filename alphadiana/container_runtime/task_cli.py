"""Small command shim for Podman-backed task-container helper scripts."""

from __future__ import annotations

import argparse
import sys

from alphadiana.container_runtime.podman_cli import PodmanCLI, PodmanError


def _write_streams(stdout: str, stderr: str) -> None:
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m alphadiana.container_runtime.task_cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    exec_parser = subparsers.add_parser("exec")
    exec_parser.add_argument("container_id")
    exec_parser.add_argument("argv", nargs=argparse.REMAINDER)

    cp_parser = subparsers.add_parser("cp")
    cp_parser.add_argument("src")
    cp_parser.add_argument("dst")

    args = parser.parse_args(argv)
    runtime = PodmanCLI()
    try:
        if args.command == "exec":
            if not args.argv:
                parser.error("exec requires a command after the container id")
            result = runtime.exec(args.container_id, args.argv, check=False)
            _write_streams(result.stdout, result.stderr)
            return int(result.returncode)
        if args.command == "cp":
            result = runtime.cp(args.src, args.dst, check=False)
            _write_streams(result.stdout, result.stderr)
            return int(result.returncode)
    except PodmanError as exc:
        _write_streams(exc.result.stdout, exc.result.stderr)
        return int(exc.result.returncode or 1)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
