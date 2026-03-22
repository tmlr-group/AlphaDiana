# OpenClaw 控制面韧性修复（2026-03-22）

## 1. 目标与范围

本次修复目标：

- 解决 ROCK 控制面 Redis 单点故障导致所有 sandbox 集体假死的问题
- 修复错误分类逻辑将控制面故障伪装成 proxy_timeout 的误导
- 修复 sandbox 复用时缺少存活探测导致持续空流的问题

适用配置：

- Agent: openclaw
- Sandbox backend: ROCK
- 多用户共享机器环境

---

## 2. 问题现象与根因定位

### 2.1 现象

- 单沙盒顺序跑 30 题，在 aime_65 正文请求成功后，收集 artifact 时出现 `sandbox d8fd... not started`
- 后续 aime_66 开始所有请求返回空内容 + `post proxy failed`
- 日志中错误被归类为 `proxy_timeout`，误导排障方向

### 2.2 根因分层

#### 根因 1：共享 Redis 跨用户误杀

7+ 个不同用户的 ROCK admin 实例都连同一个 Redis (port 6379)。每个 admin 都运行 `_check_job_background` 每 180 秒扫描所有 `alive:*` 键。任何 admin 都可能清理其他用户的 sandbox。

证据：

- Docker event 显示控制面 Redis 容器 `redis-weikai` 在 `2026-03-22 11:16:28` die，`11:16:30` destroy，`11:17:41` create，`11:17:43` start
- 故障窗口正好是 Redis 控制面消失的窗口
- 对应的 SandboxActor pid=1062830 并没有在 11:16 死，它一直活到 11:36 才因为本地 raylet 故障退出
- 这说明 11:16 不是 actor 死亡，而是控制面丢了这个 sandbox 的 alive 记录

#### 根因 2：Redis 容器无自动重启策略

所有启动脚本中 `docker run` 均未设置 `--restart` 策略（`RestartPolicy=no`）。Redis 一旦退出，ROCK 不会自愈，整批 sandbox 瞬间假死。

#### 根因 3：错误分类过粗

`classify_error()` 将所有 `post proxy failed` 统一归类为 `proxy_timeout`，导致控制面故障被伪装成超时问题，排障持续被带偏。

#### 根因 4：sandbox 复用无存活探测

`OpenClawRuntimeManager.ensure_ready()` 只检查 `sandbox_id` 是否在 `_started_sandboxes` 集合中就直接复用，不做 liveness check。sandbox 被 ROCK 判死后，盲目复用导致持续空流。

### 2.3 故障放大链路

```
Redis 控制面掉线
  -> proxy 读 alive:* 失败/读空
  -> sandbox_proxy_service.py:573 抛 "sandbox not started"
  -> sandbox_proxy_service.py:722 记为 "is not alive"
  -> openclaw_runtime.py:190 ensure_ready() 盲目复用已死 sandbox
  -> openclaw.py:122 classify_error() 将 "post proxy failed" 归为 proxy_timeout
  -> 控制面故障被伪装成"空流/超时"
```

---

## 3. 修复方案（按阶段）

### 阶段 A：Redis 隔离（此前已完成）

#### A1. 独立 Redis 实例

文件：`dev/.rock_ports.env`

- Redis 端口从公共 6379 改为独立 6391
- 容器名改为 `redis-weikai-isolated`
- Admin/Proxy/Ray 全部使用独立端口

效果：

- 其他用户的 admin 在 6379 上扫 `alive:*` 时，扫不到你的键，也杀不了你的 sandbox
- 你的 admin 只看得到自己的 sandbox

### 阶段 B：Redis 容器自动重启

#### B1. 所有启动脚本加 `--restart unless-stopped`

文件：
- `dev/quickstart.sh:155`
- `dev/setup_alphadiana_rock.sh:241`
- `repro/openclaw_reasoning/setup.sh:123`

改动：

```bash
# Before
docker run -d --name "${ROCK_REDIS_CONTAINER}" -p "${ROCK_REDIS_PORT}:6379" redis/redis-stack-server:latest

# After
docker run -d --restart unless-stopped --name "${ROCK_REDIS_CONTAINER}" -p "${ROCK_REDIS_PORT}:6379" redis/redis-stack-server:latest
```

效果：

- Redis 异常退出后 Docker 自动重启，不影响已运行的容器
- 仅对新创建的容器生效（`docker start` 路径不受影响）

### 阶段 C：错误分类细化

#### C1. classify_error() 区分 control_plane_unavailable

文件：`alphadiana/agent/openclaw.py:140-142`

改动：

```python
# Before
if "post proxy failed" in msg_lower:
    return "proxy_timeout"

# After
if "post proxy failed" in msg_lower:
    if "not started" in msg_lower or "not alive" in msg_lower:
        return "control_plane_unavailable"
    return "proxy_timeout"
```

效果：

- 原有 `proxy_timeout` 路径完全不变
- 包含 `not started` 或 `not alive` 的 `post proxy failed` 错误被正确分类为 `control_plane_unavailable`
- 下游两处 handler 新增对应日志：`"ROCK control plane unavailable ... Check Redis container health and connectivity."`

### 阶段 D：sandbox 复用前存活探测

#### D1. ensure_ready() 加 liveness probe

文件：`alphadiana/agent/openclaw_runtime.py:190-216`

新增方法：

```python
def _probe_gateway_alive(self, sandbox) -> bool:
    # GET /v1/models with 5s timeout
    # 复用 _is_ready_probe_status() 判断（200/404/405 均视为存活）
```

改动 `ensure_ready()`：

```python
# Before
if sandbox_id in self._started_sandboxes:
    return self.runtime_info(sandbox)  # 盲目复用

# After
if sandbox_id in self._started_sandboxes:
    if self._probe_gateway_alive(sandbox):
        return self.runtime_info(sandbox)  # 探活成功，安全复用
    self._started_sandboxes.discard(sandbox_id)  # 探活失败，移除缓存
    # 继续走完整部署流程
```

效果：

- 探活成功：和原来一样复用，仅增加一次 5s 超时的 HTTP GET
- 探活失败：自动移除缓存并重新部署，避免盲目复用已死 sandbox 导致持续空流

---

## 4. Changelog（面向提交）

Commit: `537569b`

- `alphadiana/agent/openclaw.py`
  - `classify_error()` 新增 `control_plane_unavailable` 分类（`not started` / `not alive`）
  - 两处错误 handler 新增 `control_plane_unavailable` 日志

- `alphadiana/agent/openclaw_runtime.py`
  - 新增 `_probe_gateway_alive()` 方法：GET `/v1/models` 做 5s 超时探活
  - `ensure_ready()` 复用前调用探活，失败则移除缓存走重新部署

- `dev/quickstart.sh`
  - Redis `docker run` 加 `--restart unless-stopped`

- `dev/setup_alphadiana_rock.sh`
  - Redis `docker run` 加 `--restart unless-stopped`

- `repro/openclaw_reasoning/setup.sh`
  - Redis `docker run` 加 `--restart unless-stopped`

- `dev/.rock_ports.env`
  - 保留独立 Redis 隔离配置（port 6391, 容器名 redis-weikai-isolated）

---

## 5. 防护层总结

修复后共有 4 层防护：

| 层级 | 机制 | 防护目标 |
|------|------|----------|
| 1 | 独立 Redis (port 6391) | 根本避免跨用户 `_check_job_background` 误杀 |
| 2 | `--restart unless-stopped` | Redis 异常退出后自动重启 |
| 3 | `_probe_gateway_alive()` | 复用 sandbox 前探活，死了就重建 |
| 4 | `control_plane_unavailable` 分类 | 控制面故障不再被伪装成 proxy_timeout |

---

## 6. 已知边界

- ROCK 内部的 `sandbox_proxy_service.py` 不在本仓库中，proxy 侧将 Redis 读失败翻译成 `sandbox not started` 的逻辑需要和 ROCK 团队沟通修复
- `_probe_gateway_alive()` 使用 5s 超时的 HTTP GET，在极端网络延迟下可能误判存活 sandbox 为死亡，但这只会触发一次额外的重新部署，不会丢失数据
- `--restart unless-stopped` 仅对新创建的容器生效；已有容器需要 `docker update --restart unless-stopped <container>` 手动更新

---

## 7. 验收标准

1. Redis 容器异常退出后能自动重启（可通过 `docker kill` 测试）
2. sandbox 被 ROCK 判死后，`ensure_ready()` 能检测到并重新部署，而非持续空流
3. 控制面故障日志中出现 `control_plane_unavailable` 而非 `proxy_timeout`
4. 所有修改向后兼容，原有成功路径行为完全不变
