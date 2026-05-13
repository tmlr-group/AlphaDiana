"""GPU argument helpers for Podman runtimes."""

from __future__ import annotations

from collections.abc import Sequence


def nvidia_cdi_args(
    devices: str | Sequence[str] | None = "all",
    *,
    disable_selinux_label: bool = True,
) -> list[str]:
    """Return Podman arguments for NVIDIA CDI device injection."""
    if devices in (None, "", [], ()):
        return []
    if isinstance(devices, str):
        device_values = [devices]
    else:
        device_values = [str(device) for device in devices if str(device)]

    args: list[str] = []
    for device in device_values:
        value = device if device.startswith("nvidia.com/gpu=") else f"nvidia.com/gpu={device}"
        args.extend(["--device", value])
    if args and disable_selinux_label:
        args.extend(["--security-opt", "label=disable"])
    return args
