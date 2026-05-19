"""Sandbox registry for discovering sandboxes by name."""

from __future__ import annotations

from typing import Type

from alphadiana.sandbox.base import Sandbox


class SandboxRegistry:
    """Global registry for sandbox classes."""

    _registry: dict[str, Type[Sandbox]] = {}

    @classmethod
    def register(cls, name: str, sandbox_cls: Type[Sandbox]) -> None:
        cls._registry[name] = sandbox_cls

    @classmethod
    def get(cls, name: str) -> Type[Sandbox]:
        if name not in cls._registry:
            raise KeyError(f"Sandbox '{name}' not found. Available: {cls.list()}")
        return cls._registry[name]

    @classmethod
    def list(cls) -> list[str]:
        return sorted(cls._registry.keys())


def register_sandbox(name: str):
    """Decorator to register a Sandbox implementation."""
    def decorator(cls):
        SandboxRegistry.register(name, cls)
        return cls
    return decorator
