"""Helpers for parsing Podman published port output."""

from __future__ import annotations

import re
from dataclasses import dataclass


_PORT_LINE_RE = re.compile(
    r"^(?:(?P<container_port>\d+)/(?:tcp|udp)\s*->\s*)?"
    r"(?P<host>\[[^\]]+\]|[^:]+):(?P<host_port>\d+)$"
)
_CONTAINER_PORT_RE = re.compile(r"^(?P<port>\d+)/(?P<protocol>tcp|udp)")


@dataclass(frozen=True)
class PublishedPort:
    """One host-published container port."""

    container_port: int | None
    protocol: str
    host: str
    host_port: int

    @property
    def loopback_host(self) -> str:
        if self.host in {"0.0.0.0", "::", "[::]"}:
            return "127.0.0.1"
        return self.host.strip("[]")

    @property
    def api_base(self) -> str:
        return f"http://{self.loopback_host}:{self.host_port}"


def parse_podman_port_output(output: str) -> list[PublishedPort]:
    """Parse output from ``podman port`` into structured host mappings."""
    ports: list[PublishedPort] = []
    current_port: int | None = None
    current_protocol = "tcp"

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "->" in line:
            left, right = [part.strip() for part in line.split("->", 1)]
            match = _CONTAINER_PORT_RE.match(left)
            if match:
                current_port = int(match.group("port"))
                current_protocol = match.group("protocol")
            parsed = _parse_host_port(right, current_port, current_protocol)
        else:
            parsed = _parse_host_port(line, current_port, current_protocol)
        if parsed:
            ports.append(parsed)
    return ports


def first_published_port(
    output: str,
    *,
    container_port: int | None = None,
    protocol: str = "tcp",
) -> PublishedPort | None:
    """Return the first parsed port matching the optional container port."""
    for published in parse_podman_port_output(output):
        if (
            container_port is not None
            and published.container_port is not None
            and published.container_port != container_port
        ):
            continue
        if published.protocol != protocol:
            continue
        return published
    return None


def _parse_host_port(
    text: str,
    container_port: int | None,
    protocol: str,
) -> PublishedPort | None:
    match = _PORT_LINE_RE.match(text)
    if not match:
        return None
    parsed_container = match.group("container_port")
    if parsed_container:
        container_port = int(parsed_container)
    return PublishedPort(
        container_port=container_port,
        protocol=protocol,
        host=match.group("host"),
        host_port=int(match.group("host_port")),
    )
