"""Registry for scorer implementations."""

from __future__ import annotations

from typing import Dict, Type

from alphadiana.scorer.base import Scorer


class ScorerRegistry:
    """Central registry that maps scorer names to their implementations."""

    _registry: Dict[str, Type[Scorer]] = {}

    @classmethod
    def register(cls, name: str, scorer_cls: Type[Scorer]) -> None:
        """Register a scorer class under the given name."""
        cls._registry[name] = scorer_cls

    @classmethod
    def get(cls, name: str) -> Type[Scorer]:
        """Retrieve a scorer class by name."""
        if name not in cls._registry:
            available = ", ".join(sorted(cls._registry.keys())) or "(none)"
            raise KeyError(
                f"Scorer '{name}' is not registered. Available: {available}"
            )
        return cls._registry[name]

    @classmethod
    def list(cls) -> list[str]:
        """Return the names of all registered scorers."""
        return sorted(cls._registry.keys())


def register_scorer(name: str):
    """Decorator to register a Scorer implementation."""

    def decorator(cls: Type[Scorer]) -> Type[Scorer]:
        ScorerRegistry.register(name, cls)
        return cls

    return decorator
