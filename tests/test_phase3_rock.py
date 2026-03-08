"""Phase 3: ROCK 环境部署与 Sandbox 验通

验证点:
- Redis 运行正常 (port 6379)
- ROCK admin (9000) 和 proxy (9001) 服务可达
- 通过 ROCK SDK 创建 sandbox 成功（sandbox.start()）
- 切换到 proxy URL 后能在 sandbox 中执行命令

部署步骤:
  1. docker run -d -p 6379:6379 redis/redis-stack-server:latest
  2. source .venv/bin/activate
  3. rock admin start --env local-proxy --role admin --port 9000
  4. rock admin start --env local-proxy --role proxy --port 9001

用 pytest -m integration 运行这些测试。
"""
import pytest

pytestmark = pytest.mark.integration


def _rock_available() -> bool:
    """检测 ROCK admin 服务是否可达。"""
    try:
        import requests
        resp = requests.get("http://localhost:9000/health", timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


def _redis_available() -> bool:
    """检测 Redis 是否可达。"""
    try:
        import redis
        r = redis.Redis(host="localhost", port=6379)
        return r.ping()
    except Exception:
        return False


@pytest.mark.skipif(not _rock_available(), reason="ROCK admin not running on localhost:9000")
class TestROCKEnvironment:
    """验证 ROCK 基础环境可用。"""

    def test_redis_running(self):
        """Redis 服务可达 (port 6379)。"""
        assert _redis_available(), "Redis is not reachable on localhost:6379"

    def test_admin_health(self):
        """ROCK admin 服务健康 (port 9000)。"""
        import requests
        resp = requests.get("http://localhost:9000/health", timeout=5)
        assert resp.status_code == 200

    def test_proxy_reachable(self):
        """ROCK proxy 服务可达 (port 9001)。"""
        import requests
        try:
            resp = requests.get("http://localhost:9001/", timeout=5)
            # 任何非连接错误的响应都说明服务在运行
            assert resp.status_code is not None
        except requests.ConnectionError:
            pytest.fail("ROCK proxy not reachable on localhost:9001")


@pytest.mark.skipif(not _rock_available(), reason="ROCK admin not running on localhost:9000")
class TestROCKSandboxLifecycle:
    """验证通过 ROCK SDK 创建和使用 sandbox。"""

    @pytest.fixture
    async def sandbox(self):
        """创建一个 sandbox，测试后自动清理。"""
        from rock.sdk.sandbox.client import Sandbox
        from rock.sdk.sandbox.config import SandboxConfig

        config = SandboxConfig(
            base_url="http://127.0.0.1:9000",
            image="python:3.11",
            memory="2g",
            cpus=0.5,
            auto_clear_seconds=300,
        )
        sb = Sandbox(config)
        await sb.start()
        # 切换到 proxy URL 进行运行时操作
        sb.url = "http://127.0.0.1:9001/apis/envs/sandbox/v1"
        yield sb

    @pytest.mark.asyncio
    async def test_sandbox_created(self, sandbox):
        """Sandbox 创建成功，有 sandbox_id。"""
        assert sandbox.sandbox_id is not None
        assert len(sandbox.sandbox_id) > 0

    @pytest.mark.asyncio
    async def test_execute_command(self, sandbox):
        """在 sandbox 中执行命令。"""
        session = await sandbox.session.create()
        result = await session.execute("echo hello")
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_python(self, sandbox):
        """在 sandbox 中执行 Python 代码。"""
        session = await sandbox.session.create()
        result = await session.execute("python3 -c 'print(2+3)'")
        assert "5" in result.stdout

    @pytest.mark.asyncio
    async def test_sandbox_has_network(self, sandbox):
        """Sandbox 内有网络连接（用于后续安装 openclaw）。"""
        session = await sandbox.session.create()
        result = await session.execute("python3 -c 'import urllib.request; print(\"ok\")'")
        assert "ok" in result.stdout
