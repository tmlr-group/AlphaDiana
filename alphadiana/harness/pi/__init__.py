"""Native Pi harness and its optional native-token training contract."""

from importlib import import_module


def __getattr__(name: str):
    if name in __all__:
        return getattr(import_module(".training", __name__), name)
    raise AttributeError(name)


__all__ = [
    "BATCH_SCHEMA",
    "CaptureConfig",
    "REQUEST_SCHEMA",
    "SCHEMA",
    "Backend",
    "ModelIdentity",
    "NativeCapture",
    "PiTrainingCollector",
    "PolicyIdentity",
    "SamplingPlan",
    "TokenCaptureEndpoint",
    "TokenTurn",
    "TrainingBatch",
    "TrainingCapture",
    "TrainingCollectionError",
    "TrainingSample",
    "capture_config_from_dict",
    "validate_training_config",
]
