# 本地运行环境

简体中文 · [English](LOCAL_ENVIRONMENT.en.md)

推荐使用仓库内的一键脚本。脚本会启动 PostgreSQL、执行迁移、初始化 LangGraph 检查点，并启动 API、Worker 和前端。

## 1. 前置条件

- Python 3.11 或更高版本
- Node.js 22 和 npm
- Docker Desktop
- 可用的模型、Embedding 和 Rerank 服务凭据（需要真实解析/RAG/AI 功能时）

不要提交 `.env`。首次安装会从 `.env.example` 创建本地配置文件。

## 2. macOS

首次安装：

```bash
cd /path/to/Team-QQFARM
./scripts/dev/setup.sh
```

检查 `.env`，至少替换模型服务密钥，然后启动：

```bash
./scripts/dev/start.sh
```

可指定端口：

```bash
./scripts/dev/start.sh --api-port 8000 --frontend-port 5173
```

停止应用进程但保留 PostgreSQL：

```bash
./scripts/dev/stop.sh
```

同时停止 PostgreSQL 容器（保留数据卷）：

```bash
./scripts/dev/stop.sh --postgres
```

## 3. Windows PowerShell

```powershell
cd C:\path\to\Team-QQFARM
.\scripts\dev\setup.ps1
.\scripts\dev\start.ps1
```

停止：

```powershell
.\scripts\dev\stop.ps1
```

## 4. 服务地址

| 服务 | 默认地址 |
| --- | --- |
| 前端 | `http://127.0.0.1:5173` |
| API | `http://127.0.0.1:8000` |
| Swagger | `http://127.0.0.1:8000/docs` |
| API 存活检查 | `http://127.0.0.1:8000/health/live` |
| API 就绪检查 | `http://127.0.0.1:8000/health/ready` |
| Worker 心跳 | `http://127.0.0.1:8000/health/worker` |

日志写入 `logs/dev/`，进程状态写入 `.local-data/`；两者都不应提交 Git。

## 5. 关键环境变量

| 变量 | 用途 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 连接串 |
| `QUOTE_STORAGE_PATH` | 报价与解析文件存储 |
| `POLICY_UPLOAD_STORAGE_PATH` | 制度上传文件存储 |
| `SUPPLIER_MODEL_*` | 主 LLM 配置 |
| `SUPPLIER_EMBEDDING_*` | 制度向量检索配置 |
| `SUPPLIER_RERANK_*` | 制度重排配置 |
| `SUPPLIER_CONVERSATION_MODEL_*` | 决策助手模型覆盖配置；未设置时复用主模型 |
| `SUPPLIER_AGENT_ENABLED` | 是否启用可选调查 Agent，默认 `false` |
| `SUPPLIER_PDF_OCR_ENABLED` | 是否启用 OCR，默认 `false` |

具体默认值和可选项以 `.env.example` 为准。配置独立 provider 时不得依赖静默回退；目标 provider 不可用应明确失败。

## 6. 手工启动（排障用）

如果一键脚本失败，可以分别执行以下命令定位问题。

启动数据库并迁移：

```bash
docker compose up -d --wait postgres
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m alembic upgrade head
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m supplier_comparison.checkpoints setup
```

终端 1：API

```bash
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m uvicorn supplier_comparison.backend.api:app \
  --host 127.0.0.1 --port 8000
```

终端 2：Worker

```bash
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m supplier_comparison.worker run-loop --poll-interval 1
```

终端 3：前端

```bash
cd frontend
npm run dev -- --host 127.0.0.1
```

## 7. 常见问题

| 现象 | 优先检查 |
| --- | --- |
| 历史任务读取失败 | PostgreSQL 是否为原来的数据卷、迁移是否成功、API ready 是否正常 |
| 上传后一直等待 | Worker 心跳和 `logs/dev/worker.err.log` |
| `worker_failed` | Worker 错误日志、模型凭据、模型 Schema 校验和当前 task revision |
| 制度发布失败 | Embedding 配置、数据库 `vector` 扩展和制度条款审核状态 |
| 制度检查一直未执行 | 任务是否绑定已发布制度、报价是否已正式提交、Worker 是否运行 |
| AI 助手失败 | Conversation 模型配置和 Worker 日志；失败不会影响确定性比较数据 |
| 前端仍显示旧页面 | 确认当前 Git 分支、Vite 进程工作目录，并硬刷新浏览器 |

停止脚本不会删除 Docker 数据卷。除非明确要清空所有本地业务数据，不要执行 `docker compose down -v`。
