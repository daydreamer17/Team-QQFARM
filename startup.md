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

先打开 Docker Desktop，然后启动数据库：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
docker compose up -d --wait postgres
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

## 停止

三个终端分别按 `Ctrl+C`，然后停止数据库：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
docker compose stop postgres
```

不要执行 `docker compose down -v`，它会删除数据库数据卷。
