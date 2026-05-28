"""Agent registry for discovering agents by name."""

from __future__ import annotations

from typing import Type

from alphadiana.agent.base import Agent


class AgentRegistry:
    """Global registry for agent classes."""

    _registry: dict[str, Type[Agent]] = {}

    @classmethod
    def register(cls, name: str, agent_cls: Type[Agent]) -> None:
        cls._registry[name] = agent_cls

    @classmethod
    def get(cls, name: str) -> Type[Agent]:
        if name not in cls._registry:
            raise KeyError(f"Agent '{name}' not found. Available: {cls.list()}")
        return cls._registry[name]

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._registry.keys())


def register_agent(name: str):
    """Decorator to register an Agent implementation."""
    def decorator(cls):
        AgentRegistry.register(name, cls)
        return cls
    return decorator
