from pathlib import Path

from alphadiana.utils.rock_ports import default_checkout_env_name


REPO = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (REPO / rel_path).read_text(encoding="utf-8")


def test_default_checkout_env_name_is_checkout_specific():
    first = default_checkout_env_name(Path("/tmp/AlphaDiana"))
    second = default_checkout_env_name(Path("/tmp/AlphaDiana-copy"))

    assert first.startswith("alphadiana-dev-")
    assert second.startswith("alphadiana-dev-copy-")
    assert first != second


def test_start_openclaw_uses_repo_parent_for_project_root():
    content = _read("scripts/start_openclaw.sh")
    assert 'PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"' in content
    assert '${SCRIPT_DIR%/dev}' not in content


def test_cleanup_rock_ports_uses_repo_parent_for_project_root():
    content = _read("scripts/cleanup_rock_ports.sh")
    assert 'PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"' in content
    assert '${SCRIPT_DIR%/dev}' not in content


def test_dashboard_run_prefers_scripts_state_files():
    content = _read("alphadiana/dashboard/run.sh")
    assert 'LOCAL_ENV_FILE="${PROJECT_ROOT}/scripts/.alphadiana_env"' in content
    assert '$PROJECT_ROOT/scripts/.rock_ports.env' in content
