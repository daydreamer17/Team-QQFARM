# QuoteWise 启动说明

项目目录：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
```

## 首次准备

需要安装：Docker Desktop、Python 3、Node.js 22。

安装前端依赖：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM/frontend
npm ci
```

初始化 Python 和数据库：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
docker compose up -d --wait postgres
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m alembic upgrade head
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m supplier_comparison.checkpoints setup
```

## 每次启动

### macOS 一键启动（推荐）

先打开 Docker Desktop，然后在仓库根目录执行：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
./scripts/dev/start.sh
```

该脚本会自动启动 PostgreSQL、执行数据库迁移和 checkpoint 初始化，并在后台启动 API、Worker 和前端。日志位于 `logs/dev/`。

停止 API、Worker 和前端：

```bash
./scripts/dev/stop.sh
```

同时停止 PostgreSQL（保留数据）：

```bash
./scripts/dev/stop.sh --postgres
```

以下为需要分别观察各服务输出时的手工启动方式。

先打开 Docker Desktop，然后启动数据库：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
docker compose up -d --wait postgres
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m alembic upgrade head
```

再打开三个终端。

### 终端 1：后端 API

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m uvicorn supplier_comparison.backend.api:app \
  --host 127.0.0.1 --port 8000
```

### 终端 2：Worker

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m supplier_comparison.worker run-loop \
  --poll-interval 1
```

### 终端 3：前端

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM/frontend
npm run dev -- --host 127.0.0.1
```

打开：http://127.0.0.1:5173

后端检查：http://127.0.0.1:8000/health/ready

Worker 检查：http://127.0.0.1:8000/health/worker

如果需要同时确认数据库和 Worker：

```bash
curl 'http://127.0.0.1:8000/health/ready?require_worker=true'
```

使用原生 AWS Bedrock Converse 作为决策助手时，先安装 AWS 可选依赖：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m pip install -e '.[aws]'
```

然后在 `.env` 中设置 `SUPPLIER_CONVERSATION_MODEL_PROVIDER=bedrock-converse`、模型 ID 和 AWS Region；认证沿用 AWS 标准凭据链。

## 停止

三个终端分别按 `Ctrl+C`，然后停止数据库：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
docker compose stop postgres
```

不要执行 `docker compose down -v`，它会删除数据库数据卷。
