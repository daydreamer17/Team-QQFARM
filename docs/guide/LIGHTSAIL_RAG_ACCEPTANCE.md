# Lightsail RAG 验收手册

> 当前状态：`BLOCKED_EXTERNAL`。此文档是官方环境执行清单，不代表 Lightsail 已通过。只有在主办方实例上完成全部步骤并填写结果记录后，才可改为 `PASSED`。

## 1. 验收边界

本手册只验收 Week2 制度检索子系统：PostgreSQL／pgvector、SiliconFlow embedding 与 rerank、制度导入、混合检索、重启恢复和干净卷完整重建。它不验收供应商身份、批准状态、RoHS 事实、`ComplianceMatrix`、LangGraph、React 或审批流程。

不得使用个人 API 的结果替代官方环境结果。实例未提供、模型未授权或额度不足时，将对应步骤记录为 `BLOCKED_EXTERNAL`，保留错误码和时间，不记录 API Key 或 provider body。

## 2. 前置记录

在执行前记录以下非敏感信息：

| 项目 | 记录值 |
| --- | --- |
| 验收时间（UTC） | 待填写 |
| 执行人 | 待填写 |
| Git commit | 待填写 |
| Lightsail 实例名称／区域 | 待填写 |
| CPU／内存／磁盘 | 待填写 |
| 操作系统与架构 | 待填写 |
| Docker 版本 | 待填写 |
| Docker Compose 版本 | 待填写 |
| pgvector 镜像 digest | 待填写 |
| API 镜像 digest | 待填写 |

Key 只写入服务器上权限为 `600`、且被 Git 忽略的 `.env`。不要把 `.env`、私钥、Authorization header 或 provider body复制到验收记录。

## 3. 部署与迁移

在仓库根目录执行：

```bash
git status --short
git rev-parse HEAD
docker --version
docker compose version
docker compose config --quiet
docker compose build api
docker compose up -d postgres
docker compose run --rm api python -m alembic upgrade head
docker compose run --rm api python -m alembic current
docker compose up -d api
docker compose ps
curl --fail --silent http://127.0.0.1:8000/health/ready
```

通过条件：工作区没有意外改动；commit 与待验收提交一致；迁移 head 为 `c83a72d80b1f`；PostgreSQL 和 API 均为 healthy；ready 响应为 `ready`。

## 4. 官方模型与制度发布

```bash
docker compose exec -T api python -m supplier_comparison.rag smoke-models
docker compose exec -T api python -m supplier_comparison.rag import-policies \
  --manifest data/policies/electronics-v1/manifest.json \
  --publish
docker compose exec -T api python -m supplier_comparison.rag import-policies \
  --manifest data/policies/development-conflict/manifest.json \
  --publish
```

通过条件：

- embedding 模型为 `BAAI/bge-m3`，维度为 1024；
- rerank 模型为 `BAAI/bge-reranker-v2-m3`；
- 主制度集合发布 24 条，索引版本为 `pidx-375fa65c082096e41d4f6b66`；
- 冲突夹具发布 2 条，索引版本为 `pidx-52ce651d9380fd20330899a7`；
- 用相同命令重放时返回 `replayed=true`，且不重复调用 embedding。

若制度内容或冻结配置经批准发生变化，索引版本会随之变化；此时先更新评测夹具和交接文档，再记录新版本，不能强行要求旧哈希。

## 5. 官方环境开发集评测

```bash
stamp="$(date -u +%Y%m%d-%H%M%S)"
output="evaluation/results/local/${stamp}/policy-rag-lightsail.json"
container_output="/tmp/policy-rag-lightsail-${stamp}.json"
docker compose exec -T api python -m supplier_comparison.rag evaluate \
  --dataset evaluation/reference/policy_rag/development_questions.json \
  --policy-set-version 2026.09.1 \
  --policy-index-version pidx-375fa65c082096e41d4f6b66 \
  --output "$container_output"
mkdir -p "$(dirname "$output")"
api_container="$(docker compose ps -q api)"
docker cp "${api_container}:${container_output}" "$output"
```

通过条件：8 个问题全部完成；rerank Recall@3 宏平均至少 80%；引用支持率与状态准确率为 100%；无答案、旧版本过滤和冲突状态符合参考；`ERROR` 为 0。输出位于被忽略的 `evaluation/results/local/`，不得提交。

## 6. 重启与持久化

重启前后分别记录安全计数：

```bash
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "select count(*) as clauses from policy_clauses; select count(*) as published_indexes from policy_indexes where status = '\''PUBLISHED'\''; select count(*) as traces from retrieval_traces;"'
docker compose restart postgres api
docker compose ps
curl --fail --silent http://127.0.0.1:8000/health/ready
docker compose exec -T postgres sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "select count(*) as clauses from policy_clauses; select count(*) as published_indexes from policy_indexes where status = '\''PUBLISHED'\''; select count(*) as traces from retrieval_traces;"'
```

通过条件：服务恢复 healthy；条款、索引和 trace 计数没有减少；再次导入返回相同索引版本和 `replayed=true`。

## 7. 干净卷完整重建

使用独立 Compose project 验证，避免删除主演示卷：

```bash
export POSTGRES_PORT=55432
export API_PORT=18000
docker compose -p supplier-rag-rebuild build api
docker compose -p supplier-rag-rebuild up -d postgres
docker compose -p supplier-rag-rebuild run --rm api python -m alembic upgrade head
docker compose -p supplier-rag-rebuild run --rm api python -m supplier_comparison.rag smoke-models
docker compose -p supplier-rag-rebuild run --rm api python -m supplier_comparison.rag import-policies \
  --manifest data/policies/electronics-v1/manifest.json \
  --publish
docker compose -p supplier-rag-rebuild ps
docker stats --no-stream
docker compose -p supplier-rag-rebuild down -v
unset POSTGRES_PORT API_PORT
```

执行 `down -v` 前必须再次确认 project 名为 `supplier-rag-rebuild`；不得对主演示 project `supplier-comparison` 执行删卷。通过条件是干净卷能够完成 migration、模型 smoke 和 24 条制度的完整 embedding／发布，并得到与同一内容和模型配置一致的索引版本。

## 8. 结果记录

| 验收项 | `PASSED`／`FAILED`／`BLOCKED_EXTERNAL` | 证据摘要 |
| --- | --- | --- |
| Compose 构建与健康检查 | 待填写 | 待填写 |
| Alembic head | 待填写 | 待填写 |
| Embedding 授权与 1024 维 | 待填写 | 待填写 |
| Rerank 授权与映射 | 待填写 | 待填写 |
| 主制度原子发布与幂等重放 | 待填写 | 待填写 |
| 8 题评测 | 待填写 | 待填写 |
| PostgreSQL 重启恢复 | 待填写 | 待填写 |
| 干净卷完整重建 | 待填写 | 待填写 |
| 资源占用 | 待填写 | 待填写 |

任何一项 `FAILED` 时整体不通过。模型或实例不可用时整体保持 `BLOCKED_EXTERNAL`，不能改成 `PASSED`。
