"""Compatibility entrypoint for built-in component registration.

The CLI and composition API share the canonical registry loader.
"""

from alphadiana.registry_loader import load_builtin_components


def register_builtin_components() -> None:
    """Register every built-in through the shared loader."""
    load_builtin_components()
