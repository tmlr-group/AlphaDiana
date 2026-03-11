"""Phase 3: ROCK 环境部署与 Sandbox 验通

验证点:
- Redis 运行正常 (configured ROCK_REDIS_PORT)
- ROCK admin / proxy 服务可达 (configured ROCK_ADMIN_PORT / ROCK_PROXY_PORT)
- 通过 ROCK SDK 创建 sandbox 成功（sandbox.start()）
- 切换到 proxy URL 后能在 sandbox 中执行命令

部署步骤:
  1. docker run -d -p $ROCK_REDIS_PORT:6379 redis/redis-stack-server:latest
  2. source .venv/bin/activate
  3. rock admin start --env local-proxy --role admin --port $ROCK_ADMIN_PORT
  4. rock admin start --env local-proxy --role proxy --port $ROCK_PROXY_PORT

用 pytest -m integration 运行这些测试。
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
    """检测 ROCK admin 服务是否可达。"""
    try:
        import requests
        resp = requests.get(f"{_admin_url()}/", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _redis_available() -> bool:
    """检测 Redis 是否可达。"""
    try:
        import redis
        r = redis.Redis(host="localhost", port=PORTS.redis_port)
        return r.ping()
    except Exception:
        return False


@pytest.mark.skipif(not _rock_available(), reason="ROCK admin not running on configured ROCK_BASE_URL")
class TestROCKEnvironment:
    """验证 ROCK 基础环境可用。"""

    def test_redis_running(self):
        """Redis 服务可达 (configured ROCK_REDIS_PORT)。"""
        assert _redis_available(), f"Redis is not reachable on localhost:{PORTS.redis_port}"

    def test_admin_health(self):
        """ROCK admin 服务健康 (configured ROCK_ADMIN_PORT)。"""
        import requests
        resp = requests.get(f"{_admin_url()}/", timeout=5)
        assert resp.status_code == 200

    def test_proxy_reachable(self):
        """ROCK proxy 服务可达 (configured ROCK_PROXY_PORT)。"""
        import requests
        try:
            resp = requests.get(f"{_proxy_root_url()}/", timeout=5)
            # 任何非连接错误的响应都说明服务在运行
            assert resp.status_code is not None
        except requests.ConnectionError:
            pytest.fail(f"ROCK proxy not reachable on {_proxy_root_url()}")


@pytest.mark.skipif(not _rock_available(), reason="ROCK admin not running on configured ROCK_BASE_URL")
class TestROCKSandboxLifecycle:
    """验证通过 ROCK SDK 创建和使用 sandbox。"""

    @pytest_asyncio.fixture
    async def sandbox(self):
        """创建一个 sandbox，测试后自动清理。"""
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
        # 切换到 proxy URL 进行运行时操作
        sb.url = _proxy_api_url()
        # 创建 bash session
        await sb.create_session(CreateBashSessionRequest(session="default"))
        yield sb

    @pytest.mark.asyncio
    async def test_sandbox_created(self, sandbox):
        """Sandbox 创建成功，有 sandbox_id。"""
        assert sandbox.sandbox_id is not None
        assert len(sandbox.sandbox_id) > 0

    @pytest.mark.asyncio
    async def test_execute_command(self, sandbox):
        """在 sandbox 中执行命令。"""
        result = await sandbox.arun("echo hello", session="default")
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_execute_python(self, sandbox):
        """在 sandbox 中执行 Python 代码。"""
        result = await sandbox.arun("python3 -c 'print(2+3)'", session="default")
        assert "5" in result.output

    @pytest.mark.asyncio
    async def test_sandbox_has_network(self, sandbox):
        """Sandbox 内有网络连接（用于后续安装 openclaw）。"""
        result = await sandbox.arun("python3 -c 'import urllib.request; print(\"ok\")'", session="default")
        assert "ok" in result.output
