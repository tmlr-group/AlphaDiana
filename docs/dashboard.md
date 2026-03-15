# AlphaDiana Dashboard 使用指南

Dashboard 是 AlphaDiana 的 Web 管理界面，提供以下能力：

- **浏览评估结果**：查看历史 run 的准确率、pass@k/avg@k、分类得分、耗时分布
- **对比多个 run**：逐题并排对比，快速找到差异
- **创建评估任务**：通过表单配置并启动 Direct LLM 或 OpenClaw 评估，实时查看进度和日志
- **管理沙箱**：部署 OpenClaw 沙箱、查看网关状态与模型信息

## 目录

- [前置条件](#前置条件)
- [安装](#安装)
- [启动](#启动)
- [页面功能](#页面功能)
  - [Runs（历史结果）](#runs历史结果)
  - [Run Detail（单次详情）](#run-detail单次详情)
  - [Compare（多 run 对比）](#compare多-run-对比)
  - [Jobs（任务管理）](#jobs任务管理)
  - [New Evaluation（新建评估）](#new-evaluation新建评估)
- [API Key 配置](#api-key-配置)
- [环境变量参考](#环境变量参考)
- [常见问题](#常见问题)

---

## 前置条件

Dashboard 运行在现有 AlphaDiana 环境之上。请先完成主 README 中的 Quick Start 或手动安装：

```bash
conda activate alphadiana
pip install -e '.[all]'   # 或 pip install -e '.[all,dev]'
```

Node.js >= 18（用于构建前端）。

---

## 安装

### 1. 安装 Dashboard 依赖

```bash
conda activate alphadiana
pip install -e '.[dashboard]'
```

这会额外安装 `fastapi`、`uvicorn`、`httpx`。

### 2. 构建前端

```bash
cd alphadiana/dashboard/frontend
npm install
npm run build
cd ../../..
```

> 每次修改前端代码后需重新 `npm run build`（开发模式除外，见下文）。

---

## 启动

### 推荐方式：使用 run.sh

```bash
conda activate alphadiana
source dev/rock_env.sh       # 加载 ROCK 环境（沙箱功能需要）
source dev/.rock_ports.env   # 加载动态端口

cd alphadiana/dashboard
./run.sh          # 开发模式：前端热更新 + 后端自动重载
./run.sh --prod   # 生产模式：前后端合并到同一端口
```

启动后：
- 开发模式：浏览器访问 `http://localhost:5173`（Vite dev server，支持热更新）
- 生产模式：浏览器访问 `http://localhost:8000`

如果默认端口被占用，`run.sh` 会自动探测并切换到下一个可用端口，并在终端输出实际使用的端口。

### 手动启动

如需分别控制前后端：

```bash
# 终端 1：启动后端
conda activate alphadiana
source dev/rock_env.sh
uvicorn alphadiana.dashboard.backend.main:app --reload --port 8000

# 终端 2：启动前端（开发模式）
cd alphadiana/dashboard/frontend
VITE_BACKEND_PORT=8000 npm run dev
```

### 仅使用后端（已构建前端）

```bash
conda activate alphadiana
uvicorn alphadiana.dashboard.backend.main:app --host 0.0.0.0 --port 8000
# 浏览器访问 http://<host>:8000
```

---

## 页面功能

### Runs（历史结果）

列出 `results/` 目录下所有 `.jsonl` 文件对应的评估结果，支持：

- 按时间、准确率排序
- 搜索过滤（run ID、agent、benchmark）
- 多选后进入 Compare 页面
- 点击任意行进入 Run Detail

**多采样结果（num_samples > 1）** 会额外显示 pass@k 和 avg@k 指标。

### Run Detail（单次详情）

单次评估的完整视图：

| 区块 | 内容 |
|------|------|
| Summary | 总准确率、pass@k、avg@k、分类得分、token 用量 |
| Score Matrix | 每道题每个 sample 的对错矩阵（多采样时自动展示） |
| Time Chart | 各任务耗时分布（直方图） |
| Token Chart | 各任务 token 用量分布 |
| Config | 对应的 YAML 配置（由 Dashboard 自动保存到 `configs/`） |
| Results Table | 每道题的得分、答案、是否正确，点击展开 Trajectory 查看完整推理轨迹 |

### Compare（多 run 对比）

从 Runs 列表勾选 2 个或更多 run，点击 **Compare** 进入对比页。

- 并排显示各 run 的 summary 指标
- 按题目逐行对比：每道题在各 run 中的答案和对错情况一目了然
- 适合对比同 benchmark 下不同 agent、不同模型的表现

### Jobs（任务管理）

查看所有已创建的评估任务：

- **进度条**：实时显示已完成 / 总任务数
- **实时准确率**：随任务完成动态更新
- **日志抽屉**：点击任意任务可展开查看实时日志（多任务并发时日志互不干扰）
- **取消**：对运行中的任务点击 Cancel 可标记为取消

> 注意：任务状态仅保存在内存中，重启后端后历史任务记录会清空，但 `results/` 中的 JSONL 文件不受影响。

### New Evaluation（新建评估）

通过表单配置并提交评估任务，支持两种 agent：

#### Direct LLM

适合快速基线测试，不需要 ROCK 沙箱。

| 字段 | 说明 |
|------|------|
| Benchmark | 选择预置 benchmark 或自定义（AIME 2024/2025、MATH 等） |
| Model | OpenAI 兼容 API 的模型名（如 `Qwen3-8B`） |
| API Base URL | 模型服务地址（如 `http://localhost:8000/v1`） |
| API Key | 直接粘贴，或输入 `$变量名` 引用 `.env` 中的 key |
| Max Concurrent | 并发任务数 |
| Num Samples | 每题采样次数（>1 时自动计算 pass@k/avg@k） |

#### OpenClaw（需要 ROCK 沙箱）

提供两种操作：

**使用已有沙箱**：从 Sandbox 下拉框选择已部署的 OpenClaw 实例，可查看网关状态和已加载的模型信息，直接提交评估。

**部署并运行（Deploy & Run）**：同时填写模型 API 配置和评估配置，提交后 Dashboard 会：
1. 后台启动沙箱部署（等待约 5–10 分钟，含 npm install）
2. 自动探测网关健康状态
3. 部署完成后自动开始评估

整个流程在 Jobs 页面可以全程监控。

---

## API Key 配置

API Key 输入框支持两种模式：

**直接粘贴**：在输入框中粘贴完整 key。

**引用环境变量**：输入 `$变量名`（如 `$OPENROUTER_API_KEY`），后端会从项目根目录的 `.env` 文件或系统环境变量中读取实际值，key 不会在 API 响应中暴露。

在项目根目录创建 `.env` 文件：

```bash
cat > .env <<'EOF'
OPENROUTER_API_KEY="sk-or-v1-xxxxxxxx"
OPENAI_API_KEY="sk-xxxxxxxx"
SILICONFLOW_API_KEY="sk-xxxxxxxx"
ARK_API_KEY="xxxxxxxx"
DEEPSEEK_API_KEY="sk-xxxxxxxx"
EOF
```

输入 API Base URL 时，Dashboard 会根据域名自动匹配并填入对应变量名：

| API Base URL | 自动匹配变量 |
|---|---|
| `https://openrouter.ai/api/v1/` | `$OPENROUTER_API_KEY` |
| `https://api.openai.com/v1/` | `$OPENAI_API_KEY` |
| `https://api.siliconflow.cn/v1/` | `$SILICONFLOW_API_KEY` |
| `https://ark.cn-beijing.volces.com/api/...` | `$ARK_API_KEY` |
| `https://api.deepseek.com/v1/` | `$DEEPSEEK_API_KEY` |

---

## 环境变量参考

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ALPHADIANA_RESULTS_DIR` | `./results` | 存放 `.jsonl` 结果文件的目录 |
| `ALPHADIANA_CONFIGS_DIR` | `./configs` | 存放 `.yaml` 配置文件的目录（Dashboard 自动写入） |
| `ALPHADIANA_BACKEND_PORT` | `8000` | 后端首选端口（被占用时自动递增） |
| `ALPHADIANA_FRONTEND_PORT` | `5173` | 前端首选端口（被占用时自动递增） |
| `ROCK_BASE_URL` | 自动探测 | ROCK admin URL，通常由 `dev/rock_env.sh` 注入 |
| `ROCK_PROXY_URL` | 自动探测 | ROCK proxy URL，通常由 `dev/rock_env.sh` 注入 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | HuggingFace 镜像站（未设置时自动使用） |

端口变量由 `run.sh` 自动处理，通常无需手动设置。

---

## 常见问题

**Q: 启动时报 `ModuleNotFoundError: No module named 'fastapi'`**

```bash
pip install -e '.[dashboard]'
```

**Q: 前端打不开 / 提示 "Cannot connect to backend"**

确认后端已启动，且 `VITE_BACKEND_PORT` 与后端实际端口一致：

```bash
# 开发模式：用 run.sh 启动会自动同步端口
cd alphadiana/dashboard && ./run.sh
```

**Q: Jobs 页面日志乱序或混淆**

Dashboard 使用 `contextvars` 隔离并发任务的日志，Python 3.12+ 时 ThreadPoolExecutor 子线程也会正确继承上下文。如果运行 Python 3.10/3.11，并发任务日志可能有轻微混叠，但不影响结果文件。

**Q: 沙箱相关功能不可用（灰色）**

ROCK 服务未运行，或 `dev/rock_env.sh` 未 source。不依赖沙箱的功能（浏览结果、Direct LLM 评估）仍然可用。

**Q: Run Detail 页面的 Config 区域显示空白**

Config 由 Dashboard 在创建任务时自动保存到 `configs/<run_id>.yaml`。通过命令行 `alphadiana run` 启动的任务不会自动生成此文件，需手动将配置文件复制到 `configs/` 目录。
