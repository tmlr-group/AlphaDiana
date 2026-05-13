from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "alphadiana"
RUNTIME_ROOT = SOURCE_ROOT / "container_runtime"

DIRECT_PODMAN_PATTERNS = [
    re.compile(r"\[\s*['\"]podman['\"]"),
    re.compile(r"\(\s*['\"]podman['\"]\s*,"),
    re.compile(r"['\"]podman\s+(run|exec|cp|logs|port|stop|rm)\b"),
]


def _is_allowed(path: Path) -> bool:
    rel = path.relative_to(PROJECT_ROOT)
    return rel.parts[:2] == ("alphadiana", "container_runtime")


def _find_direct_podman_calls(path: Path, text: str) -> list[str]:
    if _is_allowed(path):
        return []
    findings: list[str] = []
    for pattern in DIRECT_PODMAN_PATTERNS:
        if pattern.search(text):
            findings.append(pattern.pattern)
    return findings


def test_static_guard_detects_direct_podman_literals_outside_runtime() -> None:
    fake_path = PROJECT_ROOT / "alphadiana" / "agent" / "example.py"

    assert _find_direct_podman_calls(fake_path, 'subprocess.run(["podman", "run"])')
    assert _find_direct_podman_calls(fake_path, 'cmd = "podman exec cid true"')
    assert not _find_direct_podman_calls(fake_path, 'subprocess.run(["docker", "run"])')
    assert not _find_direct_podman_calls(RUNTIME_ROOT / "podman_cli.py", '["podman", "run"]')


def test_no_direct_podman_subprocess_usage_outside_runtime() -> None:
    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        findings = _find_direct_podman_calls(path, text)
        if findings:
            rel = path.relative_to(PROJECT_ROOT)
            violations.append(f"{rel}: {', '.join(findings)}")

    assert violations == []
