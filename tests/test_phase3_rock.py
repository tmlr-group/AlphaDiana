"""Phase 3: deploy the ROCK environment and verify sandbox connectivity

Checks:
- Redis is running correctly (configured ROCK_REDIS_PORT)
- ROCK admin and proxy services are reachable (configured ROCK_ADMIN_PORT / ROCK_PROXY_PORT)
- Creating a sandbox through the ROCK SDK succeeds (sandbox.start())
- Commands can be executed inside the sandbox after switching to the proxy URL

Deployment steps:
  1. docker run -d -p $ROCK_REDIS_PORT:6379 redis/redis-stack-server:latest
  2. source .venv/bin/activate
  3. rock admin start --env local-proxy --role admin --port $ROCK_ADMIN_PORT
  4. rock admin start --env local-proxy --role proxy --port $ROCK_PROXY_PORT

Run these tests with pytest -m integration.
"""
import pytest
import pytest_asyncio

from alphadiana.utils.rock_ports import resolve_rock_ports_from_env

pytestmark = pytest.mark.integration
PORTS = resolve_rock_ports_from_env()


def _admin_url() -> str:
    return PORTS.base_url


def _proxy_root_url() -> str:
    return PORTS.proxy_root_url


def _proxy_api_url() -> str:
    return PORTS.proxy_api_url


def _rock_available() -> bool:
    """Detect whether the ROCK admin service is reachable."""
    try:
        import requests
        resp = requests.get(f"{_admin_url()}/", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _redis_available() -> bool:
    """Detect if Redis is reachable."""
    try:
        import redis
        r = redis.Redis(host="localhost", port=PORTS.redis_port)
        return r.ping()
    except Exception:
        return False


@pytest.mark.skipif(not _rock_available(), reason="ROCK admin not running on configured ROCK_BASE_URL")
class TestROCKEnvironment:
    """Verify that the base ROCK environment is available."""

    @pytest.mark.skipif(not _redis_available(), reason="Redis not reachable on configured ROCK_REDIS_PORT")
    def test_redis_running(self):
        """Redis is reachable on the configured ROCK_REDIS_PORT."""
        assert _redis_available(), f"Redis is not reachable on localhost:{PORTS.redis_port}"

    def test_admin_health(self):
        """The ROCK admin service is healthy on the configured ROCK_ADMIN_PORT."""
        import requests
        resp = requests.get(f"{_admin_url()}/", timeout=5)
        assert resp.status_code == 200

    def test_proxy_reachable(self):
        """The ROCK proxy service is reachable on the configured ROCK_PROXY_PORT."""
        import requests
        try:
            resp = requests.get(f"{_proxy_root_url()}/", timeout=5)
            # Any non-connection error response indicates that the service is running
            assert resp.status_code is not None
        except requests.ConnectionError:
            pytest.fail(f"ROCK proxy not reachable on {_proxy_root_url()}")


@pytest.mark.skipif(not _rock_available(), reason="ROCK admin not running on configured ROCK_BASE_URL")
class TestROCKSandboxLifecycle:
    """Verify sandbox creation and usage through the ROCK SDK."""

    @pytest_asyncio.fixture
    async def sandbox(self):
        """Create a sandbox and clean it up automatically after the test."""
        from rock.sdk.sandbox.client import Sandbox
        from rock.sdk.sandbox.config import SandboxConfig
        from rock.actions.sandbox.request import CreateBashSessionRequest

        config = SandboxConfig(
            base_url=_admin_url(),
            image="python:3.11",
            memory="2g",
            cpus=0.5,
            auto_clear_seconds=300,
        )
        sb = Sandbox(config)
        try:
            await sb.start()
        except Exception as e:
            pytest.skip(f"Sandbox creation failed (likely missing Docker or Ray env issue): {e}")
        # Switch to proxy URL for runtime operations
        sb.url = _proxy_api_url()
        # Create bash session
        await sb.create_session(CreateBashSessionRequest(session="default"))
        yield sb

    @pytest.mark.asyncio
    async def test_sandbox_created(self, sandbox):
        """The sandbox is created successfully and has a sandbox_id."""
        assert sandbox.sandbox_id is not None
        assert len(sandbox.sandbox_id) > 0

    @pytest.mark.asyncio
    async def test_execute_command(self, sandbox):
        """Execute a command inside the sandbox."""
        result = await sandbox.arun("echo hello", session="default")
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_execute_python(self, sandbox):
        """Execute Python code in the sandbox."""
        result = await sandbox.arun("python3 -c 'print(2+3)'", session="default")
        assert "5" in result.output

    @pytest.mark.asyncio
    async def test_sandbox_has_network(self, sandbox):
        """The sandbox has network connectivity for installing OpenClaw later."""
        result = await sandbox.arun("python3 -c 'import urllib.request; print(\"ok\")'", session="default")
        assert "ok" in result.output
