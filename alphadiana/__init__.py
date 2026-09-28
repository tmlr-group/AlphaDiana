"""AlphaDiana: composable execution, measurement, and evidence analysis."""

# Preserve the existing scorer package namespace for downstream imports.
from alphadiana import scorer
from alphadiana.api import (
    AgentSpec,
    DatasetSpec,
    EnvironmentSpec,
    EvaluationRunner,
    ScorerSpec,
    agent,
    analyzer,
    dataset,
    environment,
    runner,
)
from alphadiana.scorer import Scorer, ScoreResult

__version__ = "0.1.0"
__all__ = [
    "AgentSpec",
    "DatasetSpec",
    "EnvironmentSpec",
    "EvaluationRunner",
    "ScoreResult",
    "Scorer",
    "ScorerSpec",
    "agent",
    "analyzer",
    "dataset",
    "environment",
    "runner",
    "scorer",
]
