# 本地运行环境

本页说明如何在 Windows PowerShell 中启动 Supplier Comparison 的 PostgreSQL、FastAPI 和后台 worker。数据库与固定输出测试不需要模型 API Key。

完整 MCU 两次中断演示见 [成员 D 交接](guide/guide_D.md)。

## 1. 前置条件

- Python 3.11 或兼容版本。
- 已安装并启动 Docker Desktop。
- PowerShell 当前目录为仓库根目录。

创建虚拟环境并安装项目：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env` 是本地文件。不要提交 API Key；模型配置尚未完成时仍可运行数据库、API 和全部固定输出测试。

## 2. 启动 PostgreSQL

Compose 使用固定镜像 `pgvector/pgvector:0.8.6-pg16-bookworm`，在 PostgreSQL 16 中提供 `vector` 扩展。已有 `database_data` 卷会继续挂载；切换镜像不需要删除卷。

```powershell
docker compose up -d --wait postgres
docker compose ps
docker compose exec -T postgres psql -U supplier_app -d supplier_comparison -c "SELECT 1;"
```

`STATUS` 显示 `healthy` 且查询返回 `1` 表示数据库就绪。

## 3. 初始化数据库

业务表由 Alembic 管理，LangGraph checkpoint 表由独立命令管理：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m supplier_comparison.checkpoints setup
.\.venv\Scripts\python.exe -m alembic check
docker compose exec -T postgres psql -U supplier_app -d supplier_comparison -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
```

验证 migration 可逆：

```powershell
.\.venv\Scripts\python.exe -m alembic downgrade base
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`downgrade` 只处理业务对象；checkpoint 表由 LangGraph 保留，`vector` 扩展作为共享数据库基础设施也不会被 migration 删除。不要在有业务数据的共享环境中执行该可逆性检查。

## 4. 启动 FastAPI

本机 Python 模式：

```powershell
.\.venv\Scripts\python.exe -m uvicorn supplier_comparison.backend.api:app --host 127.0.0.1 --port 8000
```

Compose 模式：

```powershell
docker compose build api worker
docker compose up -d --wait api worker
```

Compose 会通过一次性的 `migrate` 和 `checkpoint-setup` 服务先升级业务表并初始化
LangGraph checkpoint 表，成功后才启动 API 和 Worker。使用 `docker compose ps -a`
确认这两个服务均以退出码 0 完成。手工执行迁移命令只保留为诊断手段。

Swagger 位于 `http://127.0.0.1:8000/docs`。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

## 5. 后台 worker

Compose 中的 worker 自动轮询并领取 `PENDING` job。用户在页面启动分析或回答问题后，不需要复制 `job_id` 或执行终端命令：

```powershell
docker compose up -d worker
```

本机开发也可以启动同样的常驻 worker：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe -m supplier_comparison.worker run-loop --poll-interval 1
```

`run-job --job-id <job_id>` 仍保留为运维诊断入口，不属于终端用户操作流程。

决策聊天默认复用主模型配置，也可通过 `SUPPLIER_CONVERSATION_MODEL_*` 单独配置。
`SUPPLIER_CONVERSATION_JOB_STALE_SECONDS` 控制聊天 `RUNNING` job 的中断判定时间，
默认 120 秒，允许范围为 30–3600 秒。worker 会重新领取仍绑定当前 result/revision 的
中断 job；累计第三次启动仍未完成时，将其标记为 `conversation_retry_exhausted`。

## 6. 运行测试

默认测试全部离线，PostgreSQL 跨进程恢复测试默认跳过：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

数据库启动并完成初始化后，可显式运行真实 PostgreSQL 恢复测试：

```powershell
$env:RUN_POSTGRES_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests\backend\test_postgres_recovery.py -q
Remove-Item Env:RUN_POSTGRES_TESTS
```

测试使用固定模型输出验证编排，不代表真实模型提取已通过。配置本地 API Key 后，另按 `guide_D.md` 上传 V1 原生文本 PDF 做 smoke test。

## 7. 停止和恢复

停止并删除容器、保留数据：

```powershell
docker compose down
docker compose up -d --wait api worker
```

不要执行 `docker compose down -v`；`-v` 会删除数据库和报价文件卷。卷用途：

- `supplier-comparison_database_data`：PostgreSQL 业务表与 LangGraph checkpoint。
- `supplier-comparison_quote_files`：不可变上传文件。

本机 Python 使用 `localhost` 数据库地址；Compose 会覆盖为服务名 `postgres`。API 仅绑定 `127.0.0.1`，PostgreSQL 不应暴露到公网。

## 8. 当前运行边界

- 支持 PDF 和注册 CSV；主演示使用 V1 原生文本 PDF。
- OCR 默认关闭，扫描 PDF 应明确返回 `pdf_page_requires_ocr`。
- React 前端和单后台 worker 已可本地运行；版本化 AI Summary 与受控决策聊天已接入。聊天 worker 中断可限次恢复，主图任意节点的完整崩溃窗口恢复仍未完成。
- 本地身份由 `TEST_USER_ID` 固定提供；仅用于开发与演示。
- 主办方 Lightsail／Claude 接口尚未验收时，只能声明本地后端通过。

## 9. Integration update (2026-09-22)

2026-09-22 集成修复：本地数据库已升级到 `d52f7b19c3a4`，新增的
`task_history_bindings` 表由现有 Alembic 迁移创建。更新代码后必须重建
API 和 worker 镜像；镜像已包含 `data/generated/supplier_history/mcu9/`
运行数据。只看 `/health/ready` 成功不足以证明任务创建可用，部署后还应创建一条
测试任务，确认历史数据哈希及绑定正常，再将该测试任务废弃。

Windows 下请保留 `.gitattributes` 规定的换行格式：历史 JSON 和源 CSV 使用 LF，
demo3、preference_demo 的报价 CSV 按原生成器使用 CRLF。不要通过重写 manifest
哈希来掩盖文件字节变化。
