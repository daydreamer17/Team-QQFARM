# 本地运行环境

本页说明如何在 Windows PowerShell 中启动和检查 Supplier Comparison 的本地 PostgreSQL。数据库测试不需要模型 API Key。

## 1. 前置条件

- 已安装并启动 Docker Desktop。
- 当前目录为仓库根目录：

```powershell
Set-Location "Your PATH"
```

## 2. 启动数据库

```powershell
docker compose up -d --wait postgres
```

`-d` 表示后台运行，`--wait` 表示等待健康检查通过后再结束命令。

## 3. 验证环境

查看容器状态：

```powershell
docker compose ps
```

`STATUS` 中出现 `healthy` 表示 PostgreSQL 已就绪。

测试数据库连接：

```powershell
docker compose exec -T postgres psql -U supplier_app -d supplier_comparison -c "SELECT 1;"
```

返回数字 `1` 表示容器、数据库、用户和连接均正常。

查看持久化卷：

```powershell
docker volume ls --filter name=supplier-comparison
```

应包含：

- `supplier-comparison_database_data`：保存数据库数据。
- `supplier-comparison_quote_files`：保存上传的报价文件。

## 4. 停止和恢复

停止并删除容器、保留数据：

```powershell
docker compose down
```

重新创建容器并使用原有数据：

```powershell
docker compose up -d --wait postgres
```

不要执行 `docker compose down -v`，其中 `-v` 会删除数据库卷和报价文件卷。

## 5. 当前边界

当前 Compose 只启动 PostgreSQL，并预留报价文件卷。正式业务表由 D 的 Alembic 迁移创建；API 和 worker 服务需要等待 D 提供启动入口后再加入 Compose。
