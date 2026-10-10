"""Compatibility imports for the Dashboard application."""
from alphadiana.dashboard.backend.job_manager import (
    logger,
    _SECRET_FIELD_NAMES,
    _is_secret_field,
    _mask_secret_fields,
    _current_job_id,
    _DualWriter,
    _JobLogCapture,
    JobManager,
)
