import logging
import re

logger = logging.getLogger("alphadiana.versioning")


def resolve_agent_version(agent_name: str, version: str) -> str:
    if not version:
        raise ValueError(f"Agent '{agent_name}' requires a pinned version tag")
    if not re.search(r"[0-9]", version):
        raise ValueError(
            f"Version '{version}' for agent '{agent_name}' does not contain digits"
        )
    if not version.startswith("v"):
        logger.warning(
            "Version '%s' for agent '%s' does not start with 'v' "
            "(recommended format: v1.2.3)",
            version, agent_name,
        )
    return version
