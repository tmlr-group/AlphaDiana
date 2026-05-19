# AlphaDiana Podman Integration Plan

## 0. Decision and direction

AlphaDiana 的正式 container / sandbox runtime **统一切到 Podman**，但不要假设所有 agent × benchmark 都是同一种架构。

更准确的目标是：

```text
统一 container lifecycle：podman run / exec / cp / logs / port / stop / rm
保留 benchmark-specific orchestration：SWE、TB2、external_benchmark 仍有各自 task container 语义
移除主路径 ROCK：不再依赖 ROCK admin/proxy、Redis、Ray、ref/ROCK
移除 Docker daemon：不再直接依赖 docker CLI / Docker socket
结果 metadata 统一写 container_engine=podman
```

关键原则：

```text
不要做“大一统 agent runner”
要做“统一 Podman runtime + 不同 benchmark adapter”
```

推荐迁移路径：

```text
新增 Podman sandbox path / Podman runtime
        ↓
与原始 sandbox path 做成对对比
        ↓
确认 score / artifact / failure mode 基本一致
        ↓
Podman 成为默认 container / sandbox runtime
        ↓
ROCK / 原始 sandbox 保留为 baseline、legacy fallback、历史实验复现路径
```

本阶段约束：

```text
保留 ROCK 代码，不急于物理删除
不破坏已有实验结果的对齐关系
不大规模 rename 历史 docker / rock 文件名
验证期 Podman 可以显式 opt-in；验证通过后主路径默认 Podman
legacy path 只用于 baseline / fallback / 历史复现
```

Podman path 的目标：

```text
不依赖 Docker daemon
不依赖 ROCK admin / proxy / Redis / Ray 作为主运行路径
由 podman 管理 container lifecycle: run / exec / cp / logs / port / stop / rm
结果 metadata 明确记录 runtime 来源，便于和原始 sandbox 对齐比较
```

---

## 1. Terms

| Term | Meaning |
|---|---|
| Podman | 新目标容器运行时，负责 image pull/build、container run/exec/cp/logs/port/stop/rm。 |
| Podman sandbox path | AlphaDiana 新增 sandbox backend；本文不把它简称为 Pod，避免和 Kubernetes Pod 混淆。 |
| Podman-only runtime | 迁移完成后的主路径：所有需要 container 的 cell 都由 Podman 管生命周期。 |
| Original sandbox path | 当前已经用于实验的原始 sandbox 路径，主要包括 ROCK 相关路径和已有 container path。 |
| ROCK | AlphaDiana 现有 sandbox / deployment control plane；迁移后保留为 baseline、legacy fallback 和历史复现路径。 |
| Gateway agent | 需要在 sandbox 内启动 HTTP gateway 的 agent，例如 OpenClaw / ZeroClaw。 |
| Gateway API base | gateway 暴露给 host runner / scorer 的地址，例如 `http://127.0.0.1:<port>/v1`。 |
| CLI | Command-Line Interface，命令行接口，例如 `podman run`。 |
| API | Application Programming Interface，程序接口。 |
| SDK | Software Development Kit，软件开发包；例如 Docker SDK for Python。 |
| OCI image | Open Container Initiative 镜像格式；Podman 可以运行 Docker/OCI 镜像。 |
| CDI | Container Device Interface；Podman GPU 路径建议用 NVIDIA CDI，而不是 Docker 专用的 `--gpus`。 |
| SWE-bench | Software Engineering benchmark；迁移期可能仍有 Docker API client 兼容需求。 |
| TerminalBench2 / TB2 | 终端交互类 benchmark；TB2 是 TerminalBench2 的缩写。 |
| external_benchmark | GPU task-container benchmark；Podman 路径需要 CDI GPU 支持。 |
| Standard reasoning benchmarks | AIME / GPQA / HLE / IMO / MMMU-Pro 等不需要 benchmark task container 的推理任务。 |
| Containerized benchmarks | SWE / TB2 / external_benchmark 等 benchmark 本身需要 task container 的任务。 |
| PodmanAgentRuntime | 用于 standard reasoning benchmarks：agent 自己需要 gateway/controller/bridge container，benchmark 本身不需要 task container。 |
| PodmanTaskRuntime | 用于 SWE / TB2 / external_benchmark：benchmark 先创建 task container，agent 被安装或注入到 task container 或 controller container 中。 |
| A/B validation | 同一任务分别跑原始 sandbox 和 Podman sandbox，然后对比结果、产物和失败模式。 |

---

## 2. Phased plan

| Phase | Goal | Main change | Gate |
|---|---|---|---|
| 1. Add Podman path | 新增能力，不影响旧实验 | 注册 `sandbox.name: podman`；实现 lifecycle、exec、file I/O、port、logs、cleanup | Podman smoke pass；原始 sandbox path 不变 |
| 2. Gateway trial | OpenClaw / ZeroClaw 可在 Podman 内启动 | `install_agent()` / `run_agent()` 走 Podman session；用 `podman port` 暴露 gateway | gateway healthcheck pass；`gateway_api_base` 稳定 |
| 3. Container workflow trial | SWE-bench、TerminalBench2、OpenCode/controller container path 接入 Podman | Docker CLI subprocess 收敛到 `podman_cli.py`；SWE-bench 可先走 Podman socket 兼容 Docker API client | 不需要 Docker daemon；不依赖 ROCK proxy/admin |
| 4. Paired comparison | 验证 Podman 与原始 sandbox 是否对齐 | 同一 task set、model、prompt/config、scorer version 成对运行 | score、artifact、failure type 无系统性偏差 |
| 5. Promote default | 验证通过后切默认路径 | 新配置默认 `sandbox.name: podman`；原始 sandbox 保留 fallback | 新实验默认 Podman；旧实验仍可复现 |
| 6. Later cleanup | 稳定后再整理命名 | 可选 rename `*_docker` / `rock_*`，不急于做 | 历史 config/result 仍可读 |

---

## 3. Runtime architecture

新增一个很薄的 Podman runtime 层，不把 Podman 命令散落到 agent / scorer / runner。

```text
alphadiana/container_runtime/
  __init__.py
  podman_cli.py       # podman pull/build/run/exec/cp/logs/port/stop/rm
  podman_socket.py    # Podman socket / DOCKER_HOST compatibility for docker-py/SWE harness
  podman_api.py       # Podman socket / Docker API compatibility helper；可作为 podman_socket.py 的兼容别名或上层 helper
  gpu.py              # NVIDIA CDI args
  ports.py            # podman port -> 127.0.0.1:<port>

alphadiana/sandbox/podman.py
  PodmanSandbox
  PodmanSession
```

`PodmanSession` 覆盖现有 sandbox session 能力：

```text
execute
upload / download / read_text
reset / close
metadata
published_port / published_base
execute_long_running
install_agent / run_agent
```

再把不同架构收敛到两个 runtime contract：

```text
PodmanAgentRuntime
  用于 standard reasoning benchmarks：AIME / GPQA / HLE / IMO / MMMU-Pro
  agent 自己需要一个 gateway/controller/bridge container
  benchmark 本身不需要 task container

PodmanTaskRuntime
  用于 SWE / TB2 / external_benchmark
  benchmark 先创建 task container
  agent 被安装或注入到 task container 或 controller container 中
  scorer 从 task container / artifacts 取结果
```

直观结构：

```text
AlphaDiana Runner
  │
  ├─ Standard reasoning benchmarks
  │    ├─ Direct       -> native provider call, no container
  │    ├─ OpenClaw    -> PodmanAgentRuntime(gateway)
  │    ├─ OpenCode    -> PodmanAgentRuntime(controller)
  │    └─ ZeroClaw    -> PodmanAgentRuntime(bridge/cli)
  │
  └─ Containerized benchmarks
       ├─ SWE-bench / SWE-bench Pro -> PodmanTaskRuntime(repo task container)
       ├─ Terminal-Bench2           -> PodmanTaskRuntime(terminal task container)
       └─ external_benchmark                 -> PodmanTaskRuntime(GPU task container)
```

Direct × SWE / TB2 按当前口径使用外部仓库，AlphaDiana 主路径里直接记为 `-`。

设计约束：

```text
podman_cli.py 是唯一发 podman subprocess 的地方
Podman path 不经过 ROCK proxy
原始 sandbox path 保留，不删除
对比验证期间 Podman 可以 opt-in；主路径稳定后默认 Podman
```

---

## 4. Config shape

验证期显式启用 Podman：

```yaml
sandbox:
  name: podman
  config:
    image: tmlrgroup/alphadiana:v1
    memory: 2g
    cpus: 0.5
    workdir: /workspace
    publish:
      "8080/tcp": "127.0.0.1::"

agent:
  config:
    podman_gateway_config_path: openclaw_deploy/podman_gateway.yaml
    reuse_predeployed_sandboxes: true
```

旧配置处理：

```text
rock_* 字段继续允许 legacy path 使用
podman_* 字段只服务 Podman path
validator 对 rock_* + podman 混用给 warning，不破坏旧配置
controller_mode=docker -> controller_mode=podman
每个 run 记录 sandbox_backend、container_engine、image digest、runtime config digest
```

迁移完成后，主路径 validator 应该拒绝新的 ROCK / Docker 主路径配置，只允许 legacy/deprecated/docs/test fixture 中保留。

SWE-bench 等仍依赖 Docker API client 的位置，先通过 Podman socket 兼容：

```bash
systemctl --user start podman.socket
export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
```

这不是使用 Docker daemon，而是让现有 Docker API client 连接 Podman system service。

---

## 5. Gateway model

新增：

```text
openclaw_deploy/podman_gateway.yaml
```

最小语义：

```yaml
workdir: /workspace
install_cmd: "openclaw --version || npm install -g openclaw"
run_cmd: >-
  OPENCLAW_CONFIG_PATH=/tmp/alphadiana-runtime/openclaw.json
  OPENCLAW_HOME=/tmp/oc_home
  openclaw gateway --host 0.0.0.0 --port 8080
env:
  OPENAI_BASE_URL: "${OPENAI_BASE_URL}"
  OPENAI_API_KEY: "${OPENAI_API_KEY}"
  OPENAI_MODEL_NAME: "${OPENAI_MODEL_NAME}"
  OPENCLAW_GATEWAY_TOKEN: "${OPENCLAW_GATEWAY_TOKEN}"
```

Runtime sequence：

```text
podman run -d --publish 127.0.0.1::8080 <image>
podman cp <config_dir> <container>:/tmp/alphadiana-runtime
podman exec <container> bash -lc <install_cmd>
podman exec -d <container> bash -lc <run_cmd>
podman port <container> 8080/tcp -> gateway_api_base
```

ZeroClaw bridge 同理：在 Podman sandbox container 内启动，通过 published port 暴露给 host runner / scorer。

ROCK auto-deploy 替换语义：

```text
before:
  rock_image + rock_agent_config_path
  ROCK admin/proxy creates sandbox
  api_base comes from ROCK proxy URL

after:
  podman_image + podman_gateway_config_path
  PodmanSandbox creates container
  api_base comes from podman port 8080/tcp
```

---

## 6. Benchmark-specific implementation strategy

### Step 1: 先做 Podman primitive

所有 Podman 命令只允许从这里发出：

```text
alphadiana/container_runtime/podman_cli.py
```

最小接口：

```text
run(), exec(), cp_to(), cp_from(), logs(), port(), build(), pull(), stop_rm()
```

这样历史文件即使暂时还叫 `*_docker.py`，里面也不再直接拼 `docker` 命令。

---

### Step 2: 保留 benchmark-specific wrapper，但替换底层 engine

不要一次性重写所有 agent。先把旧 wrapper 迁移为 Podman-backed wrapper。

```text
external_benchmark_docker.py          -> internal Podman; later rename external_benchmark_podman.py
swebench_docker.py           -> internal Podman; later rename swebench_podman.py
terminal_bench2_common.py    -> internal Podman target/container operations
swebench_container.py        -> docker-py path 接 Podman socket，后续再纯 Podman 化
```

短期可以保留旧名字，避免 config / test / artifact contract 一次性爆炸；但 metadata 必须写：

```json
{
  "container_engine": "podman"
}
```

---

### Step 3: ROCK auto-deploy 改成 Podman agent runtime

重点替换：

```text
alphadiana/runner/runner.py
alphadiana/agent/openclaw_runtime.py
alphadiana/agent/zeroclaw_runtime.py
alphadiana/sandbox/rock.py
alphadiana/utils/rock_ports.py
alphadiana/utils/rock_runtime.py
```

---

### Step 4: SWE / TB2 / external_benchmark 走 PodmanTaskRuntime

这三类 benchmark 不应该强行改成 standard reasoning flow。它们本来就需要 task container。

```text
SWE:
  build/pull repo image -> podman run task container -> inject agent -> collect patch -> scorer

TB2:
  podman run task image -> agent operates via exec/in-container runtime -> run verifier -> collect logs

external_benchmark:
  podman run GPU task container -> agent optimizes kernel -> collect artifacts -> scorer
```

external_benchmark GPU 不再用 Docker `--gpus`，统一用 Podman CDI：

```text
--device nvidia.com/gpu=all
--security-opt=label=disable
```

---

## 7. Original implementation matrix

Legend：

```text
D-Native      = direct_llm.py，native provider API，无 sandbox
OC-ROCK       = openclaw.py + openclaw_runtime.py + ROCK auto-deploy
OCode-DC      = opencode.py + controller_mode=docker
ZC-ROCK       = zeroclaw.py + zeroclaw_runtime.py + ROCK bridge
*-SWE-DK      = swebench_container.py / docker-py / SWE official image path
*-SP-DK       = swebench_docker.py(agent_type=*) / Docker CLI / SWE-Pro eval script
*-TB2-DK      = terminal_bench2_* / Docker target container
*-MOLT-DK     = external_benchmark_docker.py(agent_type=*) / Docker GPU container
-             = AlphaDiana 主路径不做；使用外部仓库或不支持
```

| Agent \ Benchmark | AIME | GPQA | HLE | IMO | MMMU-Pro | SWE-bench | SWE-bench Pro | TB2 | external_benchmark |
|---|---|---|---|---|---|---|---|---|---|
| Direct | D-Native | D-Native | D-Native | D-Native | D-Native | - | - | - | - |
| OpenClaw | OC-ROCK | OC-ROCK | OC-ROCK | OC-ROCK | OC-ROCK | OC-SWE-DK | OC-SP-DK | OC-TB2-DK | OC-MOLT-DK |
| OpenCode | OCode-DC | OCode-DC | OCode-DC | OCode-DC | OCode-DC | OCode-SWE-DK | OCode-SP-DK | OCode-TB2-DK | OCode-MOLT-DK |
| ZeroClaw | ZC-ROCK | ZC-ROCK | ZC-ROCK | ZC-ROCK | ZC-ROCK | ZC-SWE-DK | ZC-SP-DK | ZC-TB2-DK | ZC-MOLT-DK |

这个矩阵说明：原实现不是一个架构，而是至少四类历史路径：

```text
native provider path
ROCK gateway/bridge path
Docker controller path
benchmark-specific Docker task-container path
```

---

## 8. New implementation matrix

Legend：

```text
D-Native      = 保持 native provider API，无 container
P-Agent(*)    = PodmanAgentRuntime：agent gateway/controller/bridge container
P-Task(*)     = PodmanTaskRuntime：benchmark task container + agent injection/runtime
P-GPU(*)      = PodmanTaskRuntime + NVIDIA CDI GPU
-             = AlphaDiana 主路径不做；使用外部仓库或不支持
```

| Agent \ Benchmark | AIME | GPQA | HLE | IMO | MMMU-Pro | SWE-bench | SWE-bench Pro | TB2 | external_benchmark |
|---|---|---|---|---|---|---|---|---|---|
| Direct | D-Native | D-Native | D-Native | D-Native | D-Native | - | - | - | - |
| OpenClaw | P-Agent(OC) | P-Agent(OC) | P-Agent(OC) | P-Agent(OC) | P-Agent(OC) | P-Task(OC) | P-Task(OC) | P-Task(OC) | P-GPU(OC) |
| OpenCode | P-Agent(OCode) | P-Agent(OCode) | P-Agent(OCode) | P-Agent(OCode) | P-Agent(OCode) | P-Task(OCode) | P-Task(OCode) | P-Task(OCode) | P-GPU(OCode) |
| ZeroClaw | P-Agent(ZC) | P-Agent(ZC) | P-Agent(ZC) | P-Agent(ZC) | P-Agent(ZC) | P-Task(ZC) | P-Task(ZC) | P-Task(ZC) | P-GPU(ZC) |

理想状态不是“每个 cell 都跑同一个 Python class”，而是：

```text
所有需要 container 的 cell 都只通过 Podman runtime 管生命周期
所有 benchmark 差异都留在 adapter 层
agent 逻辑、scorer 逻辑、artifact contract 尽量不变
```

---

## 9. Workflow after migration

```text
                 ┌──────────────────────────────┐
                 │ configs/*.yaml                │
                 │ agent + benchmark + podman_*  │
                 └───────────────┬──────────────┘
                                 ▼
                 ┌──────────────────────────────┐
                 │ Runner / Validator            │
                 │ reject main-path ROCK/Docker   │
                 └───────────────┬──────────────┘
                                 ▼
                 ┌──────────────────────────────┐
                 │ Runtime selector              │
                 └───────┬────────────────┬─────┘
                         │                │
                         ▼                ▼
        ┌────────────────────────┐   ┌────────────────────────┐
        │ PodmanAgentRuntime      │   │ PodmanTaskRuntime       │
        │ AIME/GPQA/HLE/IMO/MMMU  │   │ SWE/TB2/external_benchmark       │
        └────────────┬───────────┘   └────────────┬───────────┘
                     │                            │
                     ▼                            ▼
        ┌────────────────────────┐   ┌────────────────────────┐
        │ podman run agent image  │   │ podman run task image   │
        │ gateway/controller      │   │ inject/run agent        │
        │ podman port -> api_base │   │ exec/cp/logs/artifacts  │
        └────────────┬───────────┘   └────────────┬───────────┘
                     │                            │
                     └──────────────┬─────────────┘
                                    ▼
                 ┌──────────────────────────────┐
                 │ Scorer + ResultStore          │
                 │ container_engine=podman       │
                 │ artifact contract preserved   │
                 └──────────────────────────────┘
```

Podman 主路径阶段：

```text
configs/*.yaml
sandbox.name: podman
        │
        ▼
ExperimentConfig + Validator
        │
        ▼
Runner
create/reuse Podman sandbox
        │
        ▼
PodmanSession
podman run/cp/exec/port/logs
        │
 ┌──────┴────────────────────┐
 ▼                           ▼
Gateway agents               Container workflows
OpenClaw / ZeroClaw          SWE-bench / TB2 / OpenCode
 │                           │
 ▼                           ▼
127.0.0.1:<port>/v1          scorer / artifact collect
 │                           │
 └────────────┬──────────────┘
              ▼
ResultStore
sandbox_backend=podman
container_engine=podman
```

SWE-bench compatibility path：

```text
SWE-bench harness / Docker API client
        ▼
DOCKER_HOST=unix://$XDG_RUNTIME_DIR/podman/podman.sock
        ▼
Podman system service
        ▼
Podman containers/images
```

---

## 10. A/B validation workflow

```text
Same task set
Same model / prompt / agent config
Same image / dependency lock
Same scorer version
        │
        ▼
Paired runner
        │
 ┌──────┴─────────────────────┐
 ▼                            ▼
Original sandbox path         Podman sandbox path
ROCK / existing runtime       sandbox.name: podman
 │                            │
 ▼                            ▼
Result A                      Result B
score / pass-fail             score / pass-fail
logs / artifacts              logs / artifacts
failure type                  failure type
metadata                      metadata
 │                            │
 └────────────┬───────────────┘
              ▼
Alignment comparator
score parity
artifact shape parity
failure category parity
runtime-only delta analysis
              │
              ▼
If consistent:
Podman becomes default path
Original sandbox remains fallback
```

A/B validation 条件：

```text
Same task set
Same model / prompt / agent config
Same image / dependency lock
Same scorer version
same task + same model + same agent config + same scorer version
score / pass-fail consistent
key artifacts present and schema-compatible
failure category consistent or explainable
metadata can separate Podman run from baseline run
```

---

## 11. Migration order

```text
1. 新增 container_runtime/podman_cli.py + sandbox/podman.py
2. 新增 Podman runtime core：podman_cli.py、podman_socket.py / podman_api.py、gpu.py、ports.py
3. 注册 sandbox.name: podman，实现 PodmanSandbox / PodmanSession
4. Runner: ROCK auto/predeploy -> PodmanAgentRuntime
5. OpenClaw / ZeroClaw standard reasoning：ROCK gateway/bridge -> Podman gateway/bridge
6. OpenCode standard reasoning：controller_mode=docker -> controller_mode=podman
7. Gateway agents：OpenClaw / ZeroClaw 的 install、run、port resolution 走 Podman session
8. SWE-bench Verified：swebench_container 改 Podman-backed
9. TB2：terminal_bench2_* 改 Podman target/in-container runtime
10. external_benchmark：external_benchmark_docker 改 Podman CDI GPU runtime
11. SWE-bench Pro：swebench_docker + scorer path 改 Podman-backed
12. Container workflows：SWE-bench、TerminalBench2、OpenCode/controller container path 接入 Podman
13. A/B metadata：每条结果记录 sandbox_backend、container_engine、image_digest、runtime_config_digest、container_id
14. Paired validation：同任务分别跑原始 sandbox 和 Podman sandbox
15. Promote default：只对已验证通过的路径切默认；原始 sandbox 保留
16. docs/configs/tests：保留 legacy alias，主路径全部写 podman
17. 稳定后再 rename *_docker.py -> *_podman.py
```

---

## 12. Files and code areas to touch

宏观代码面：

```text
container runtime:
  新增 Podman CLI / API socket / GPU / port helpers

sandbox layer:
  新增 PodmanSandbox / PodmanSession；registry 注册 podman

runner / config:
  支持 sandbox.name: podman；保留 legacy ROCK 配置

agent runtimes:
  OpenClaw / ZeroClaw gateway install/run/port 改走 Podman session

container workflows:
  SWE-bench、TerminalBench2、OpenCode/controller container path 接入 Podman

tests / docs / scripts:
  Podman smoke、Podman-vs-original 对齐测试、setup docs

legacy:
  ROCK 代码保留；只从 Podman workflow 中解耦
```

详细文件表：

| Area | Files | Change |
|---|---|---|
| Podman core | `alphadiana/container_runtime/*.py`, `alphadiana/sandbox/podman.py` | 新增 Podman lifecycle、port、socket、GPU、file I/O |
| Runner | `alphadiana/runner/runner.py` | ROCK auto/predeploy -> Podman auto/predeploy |
| Config | `configs/schema.yaml`, `alphadiana/config/validator.py`, `alphadiana/config/experiment_config.py` | `rock_*` deprecated；新增 `podman_*`；`controller_mode=docker` -> `podman` |
| Standard reasoning | `openclaw_runtime.py`, `zeroclaw_runtime.py`, `opencode.py` | gateway/controller/bridge 改走 PodmanAgentRuntime |
| SWE-bench | `sandbox/swebench_container.py`, `agent/openclaw_container_runtime.py`, `agent/opencode_container_runtime.py`, `agent/zeroclaw.py`, `scorer/swe_bench.py`, `utils/swebench.py` | official SWE task container 改 Podman-backed |
| SWE-bench Pro | `agent/swebench_docker.py`, `scorer/swebench_pro.py` | Docker CLI/SWE-Pro eval path 改 Podman-backed |
| Terminal-Bench2 | `terminal_bench2_common.py`, `terminal_bench2_openclaw.py`, `terminal_bench2_opencode.py`, `terminal_bench2_zeroclaw.py`, `terminal_bench2_docker.py`, `terminal_bench2_incontainer.py` | task container + controller/in-container runtime 改 Podman |
| external_benchmark | `agent/external_benchmark_docker.py`, `scorer/external_benchmark.py` | GPU Docker container 改 Podman CDI container |
| Legacy cleanup | `utils/rock_ports.py`, `utils/rock_runtime.py`, `scripts/rock_env.sh`, `scripts/find_rock_ports.py`, `openclaw_deploy/rock_agent_config*.yaml` | legacy-only 或删除；新增 `podman_gateway.yaml` |

---

## 13. Runtime rules

```text
GPU:
  use --device nvidia.com/gpu=all --security-opt=label=disable
  do not use Docker --gpus all in Podman path

Host access:
  prefer host.containers.internal
  allow explicit host_gateway fallback

Socket security:
  use Unix socket only
  do not expose Podman API on public TCP

Metadata:
  always write sandbox_backend=podman and container_engine=podman for Podman path
  preserve container_id/image_name for historical analysis compatibility
```

---

## 14. Acceptance checks

Podman smoke：

```text
OpenClaw smoke pass
ZeroClaw smoke pass
TerminalBench2 smoke pass
SWE-bench mini smoke pass through Podman socket
No Docker daemon required
No ROCK proxy/admin dependency in Podman path
```

Full smoke matrix：

```text
must pass:
  AIME/GPQA/HLE/IMO/MMMU-Pro × Direct/OpenClaw/OpenCode/ZeroClaw smoke
  SWE-bench × OpenClaw/OpenCode/ZeroClaw smoke
  SWE-bench Pro × OpenClaw/OpenCode/ZeroClaw smoke
  TB2 × OpenClaw/OpenCode/ZeroClaw smoke
  external_benchmark × OpenClaw/OpenCode/ZeroClaw smoke

explicitly out of AlphaDiana main path:
  Direct × SWE
  Direct × TB2
```

A/B validation：

```text
same task + same model + same agent config + same scorer version
score / pass-fail consistent
key artifacts present and schema-compatible
failure category consistent or explainable
metadata can separate Podman run from baseline run
```

Static checks：

```bash
rg "docker run|docker exec|docker cp|docker logs" alphadiana scripts configs
rg "subprocess\..*docker|\[\"docker\"|\bdocker run\b|\bdocker exec\b" alphadiana scripts configs
rg "rock_proxy|rock_admin|rock_" alphadiana scripts configs
rg "ROCK|rock_" alphadiana scripts configs
```

Expected：

```text
Podman workflow 中没有 Docker CLI 调用
runtime 主路径没有 Docker CLI
ROCK 相关内容只出现在 baseline / legacy / non-Podman workflow 中
ROCK 只剩 legacy/deprecated/docs/test fixture
Podman workflow 的容器命令统一走 podman_cli.py
旧文件名可以暂时保留，但实际执行必须是 Podman
```

Result metadata example：

```json
{
  "sandbox_backend": "podman",
  "container_engine": "podman",
  "container_id": "...",
  "image_name": "...",
  "image_digest": "...",
  "runtime_config_digest": "...",
  "gateway_api_base": "http://127.0.0.1:<port>/v1",
  "paired_baseline_run_id": "..."
}
```

---

## 15. References

- Podman: https://github.com/containers/podman
- Podman system service / Docker API compatibility: https://docs.podman.io/en/latest/markdown/podman-system-service.1.html
- NVIDIA CDI support: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html
