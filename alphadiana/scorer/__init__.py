from alphadiana.scorer.base import Scorer, ScoreResult
from alphadiana.registry import ScorerRegistry

__all__ = ["ScoreResult", "Scorer", "ScorerRegistry"]


def create(metric: str, **config):
    """Describe a scorer without shadowing this established package namespace."""
    from alphadiana.api import scorer

    return scorer(metric, **config)
