"""Compatibility exports; canonical implementation: analysis.run_summary."""
from alphadiana.analysis.run_summary import (
    RunSummary,
    _get_category,
    _manifest_category,
    ReportGenerator,
)

__all__ = ['RunSummary', 'ReportGenerator']
