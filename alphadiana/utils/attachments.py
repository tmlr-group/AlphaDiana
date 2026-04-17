"""Utilities for benchmark task attachments."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any


def iter_binary_attachments(attachments: dict[str, Any]) -> list[tuple[str, bytes, str]]:
    """Return non-mime attachments as sorted ``(key, bytes, mime)`` tuples."""
    items: list[tuple[str, bytes, str]] = []
    for key in sorted(k for k in attachments if not k.endswith("_mime")):
        value = attachments.get(key)
        if not isinstance(value, (bytes, bytearray)):
            continue

        mime_value = attachments.get(f"{key}_mime", b"application/octet-stream")
        if isinstance(mime_value, bytes):
            mime = mime_value.decode(errors="ignore").strip()
        else:
            mime = str(mime_value).strip()
        if not mime:
            mime = "application/octet-stream"

        items.append((key, bytes(value), mime))
    return items


def build_openai_multimodal_user_content(text: str, attachments: dict[str, Any]) -> Any:
    """Build OpenAI-compatible multimodal user content."""
    items = iter_binary_attachments(attachments)
    if not items:
        return text

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for _, payload, mime in items:
        b64 = base64.b64encode(payload).decode("ascii")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{b64}"},
        })
    return content


def attachment_suffix_for_mime(mime: str) -> str:
    """Infer a stable filename suffix for an attachment MIME type."""
    normalized = mime.strip().lower()
    if normalized == "image/jpeg":
        return ".jpg"
    if normalized == "image/svg+xml":
        return ".svg"
    return mimetypes.guess_extension(normalized) or ".bin"


def write_attachments(directory: Path, attachments: dict[str, Any]) -> list[Path]:
    """Write task attachments into ``directory`` and return their paths."""
    items = iter_binary_attachments(attachments)
    if not items:
        return []

    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for key, payload, mime in items:
        path = directory / f"{key}{attachment_suffix_for_mime(mime)}"
        path.write_bytes(payload)
        paths.append(path)
    return paths
