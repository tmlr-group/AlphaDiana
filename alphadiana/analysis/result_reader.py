"""Documented compatibility import; canonical owner: analysis.io.result_reader."""
from alphadiana.analysis.io.result_reader import (
    RunBundle,
    load_jsonl_records,
    load_run_bundle,
    resolve_run_relative_path,
)

__all__ = ['RunBundle', 'load_jsonl_records', 'load_run_bundle', 'resolve_run_relative_path']
