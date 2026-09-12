# 本地运行环境

本页说明如何在 Windows PowerShell 中启动 Supplier Comparison 的 PostgreSQL、FastAPI 和一次性 worker。数据库与固定输出测试不需要模型 API Key。

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
```

验证 migration 可逆：

```powershell
.\.venv\Scripts\python.exe -m alembic downgrade base
.\.venv\Scripts\python.exe -m alembic upgrade head
```

`downgrade` 只处理业务表；checkpoint 表由 LangGraph 保留。不要在有业务数据的共享环境中执行该可逆性检查。

## 4. 启动 FastAPI

本机 Python 模式：

```powershell
.\.venv\Scripts\python.exe -m uvicorn supplier_comparison.backend.api:app --host 127.0.0.1 --port 8000
```

Compose 模式：

```powershell
docker compose build api worker
docker compose run --rm api python -m alembic upgrade head
docker compose run --rm api python -m supplier_comparison.checkpoints setup
docker compose up -d --wait api
```

Swagger 位于 `http://127.0.0.1:8000/docs`。健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

## 5. 执行一次性 worker

API 创建 run 或回答 issue 后会返回 `job_id`。每个 job 由一个短生命周期进程执行：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.worker run-job --job-id <job_id>
```

Compose 方式：

```powershell
docker compose --profile worker run --rm worker python -m supplier_comparison.worker run-job --job-id <job_id>
```

worker 在成功结束或写入 interrupt 后退出。本周没有常驻轮询器，不能只启动 `api` 后等待 job 自动执行。

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
docker compose up -d --wait api
```

不要执行 `docker compose down -v`；`-v` 会删除数据库和报价文件卷。卷用途：

- `supplier-comparison_database_data`：PostgreSQL 业务表与 LangGraph checkpoint。
- `supplier-comparison_quote_files`：不可变上传文件。

本机 Python 使用 `localhost` 数据库地址；Compose 会覆盖为服务名 `postgres`。API 仅绑定 `127.0.0.1`，PostgreSQL 不应暴露到公网。

## 8. 当前运行边界

- 支持 PDF 和注册 CSV；主演示使用 V1 原生文本 PDF。
- OCR 默认关闭，扫描 PDF 应明确返回 `pdf_page_requires_ocr`。
- 没有 React、正式认证、RAG、审批、报告、后台 worker 调度或任意节点崩溃自动恢复。
- 本地身份由 `TEST_USER_ID` 固定提供；仅用于开发与演示。
- 主办方 Lightsail／Claude 接口尚未验收时，只能声明本地后端通过。
