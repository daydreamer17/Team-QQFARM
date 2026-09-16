# Week2 制度检索子系统交接指南

> 状态：本地实现与真实 SiliconFlow 开发集验证完成（2026-09-16）。Lightsail、供应商合规门禁和 LangGraph 接入仍由对应负责人完成。

## 1. 本轮交付

本子系统负责把版本化采购制度导入 PostgreSQL，并通过 BM25、pgvector 精确余弦检索、RRF 和 rerank API 返回可核验的 Top-3 条款。它只检索制度和生成受控说明，不识别供应商身份、不查询批准状态或 RoHS 事实、不决定 `ComplianceMatrix`，也不改变价格和交期排序。

主要代码位于：

| 路径 | 作用 |
| --- | --- |
| `src/supplier_comparison/rag/contracts.py` | `RetrievalRequest`、`RetrievalResult`、`PolicyCitation`、`PolicyExplanation` 和 `PolicyRetriever` 冻结契约 |
| `src/supplier_comparison/rag/clients.py` | SiliconFlow embedding／rerank 客户端与无网络固定客户端 |
| `src/supplier_comparison/rag/manifest.py` | manifest 白名单、Markdown 条款边界、日期、重复 ID 和内容哈希校验 |
| `src/supplier_comparison/rag/models.py` | 7 张制度、索引、导入和检索轨迹表 |
| `src/supplier_comparison/rag/importer.py` | 幂等导入、索引版本计算、失败隔离和原子发布 |
| `src/supplier_comparison/rag/repository.py` | SQL 范围过滤、pgvector 精确余弦 Top-10、引用原文／哈希复核和轨迹持久化 |
| `src/supplier_comparison/rag/retriever.py` | BM25 Top-10、RRF `k=60`、rerank Top-3、覆盖／冲突／错误状态 |
| `src/supplier_comparison/rag/explanation.py` | 只允许调用方确认事实及本次 citation ID 的解释边界 |
| `src/supplier_comparison/rag/evaluation.py` | 分阶段 Recall、引用支持率、状态准确率、延迟和错误评测 |
| `migrations/versions/c83a72d80b1f_create_policy_retrieval_schema.py` | RAG 业务表 migration；不管理 LangGraph checkpoint 表 |

主演示制度位于 `data/policies/electronics-v1/`，共 5 个虚构英文文档、24 个完整条款。`data/policies/development-conflict/` 只用于冲突评测，不能作为主演示制度。固定交接示例位于 `data/examples/policy_rag/`。

## 2. 固定模型和配置

当前索引配置已经冻结：

```text
Embedding: BAAI/bge-m3
Dimension: 1024
Rerank: BAAI/bge-reranker-v2-m3
Timeout: 30 seconds
Maximum attempts: 2 including the first call
Import batch size: 16
Preprocessing: Unicode NFKC + lowercase + alphanumeric tokens with internal hyphens
```

Embedding 和 rerank 使用不同配置前缀，但默认都从 `QQFARM_SILICONFLOW_API_KEY` 读取密钥。`SUPPLIER_POLICY_ROOT` 指向允许导入的制度根目录，本地默认是 `data/policies`，Compose 固定为 `/app/data/policies`。不要把 Key 写入命令、文档、日志或 Git。参考变量已写入 `.env.example`；本地真实值只放在被忽略的 `.env`。

## 3. 数据库初始化

在 PowerShell 中执行：

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

当前 head 为 `c83a72d80b1f`。新表为：

- `policy_sets`
- `policy_documents`
- `policy_clauses`
- `policy_indexes`
- `policy_clause_embeddings`
- `policy_import_runs`
- `retrieval_traces`

`policy_clause_embeddings.embedding` 固定为 `vector(1024)`。首版只有普通唯一索引和外键索引，没有 HNSW 或 IVFFlat。

## 4. 真实模型 smoke

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.rag smoke-models
```

成功时输出安全的 JSON 摘要，其中应包括：

```json
{
  "embedding_dimension": 1024,
  "embedding_model": "BAAI/bge-m3",
  "rerank_model": "BAAI/bge-reranker-v2-m3",
  "status": "OK"
}
```

命令不会输出 Key 或 provider body。模型授权失败、响应结构错误或 embedding 不是 1024 维时必须先处理配置，不能自动换模型或创建其他维度的索引。

## 5. 导入和重建制度

发布主演示集合：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.rag import-policies `
  --manifest data/policies/electronics-v1/manifest.json `
  --publish
```

冲突开发夹具需要单独发布：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.rag import-policies `
  --manifest data/policies/development-conflict/manifest.json `
  --publish
```

导入器只接受 `data/policies/` 白名单下的文件。它拒绝绝对越界路径、`..`、重复条款 ID、Markdown 孤立章节、空条款、无效日期和同一制度版本的不同内容哈希。

发布过程先保存权威正文和 `BUILDING` 索引；全部条款 embedding 成功且数量、维度与哈希一致后，才在一个事务中写入向量并标记 `PUBLISHED`。失败索引为 `FAILED`，不会被检索。相同 manifest 和相同模型配置再次导入返回 `replayed=true`，不会重复调用 embedding。模型、维度、预处理或集合内容变化会产生新 `policy_index_version` 并从权威条款完整重建。

当前 SiliconFlow 配置对应的开发索引版本为：

```text
electronics-v1:       pidx-375fa65c082096e41d4f6b66
development-conflict: pidx-52ce651d9380fd20330899a7
```

这些值由内容和配置计算；修改制度后以导入命令输出为准，并同步更新评测夹具中的显式冲突索引版本。

## 6. 检索顺序和状态

每次检索按以下顺序运行：

1. 精确匹配 `policy_set_version` 和 `policy_index_version`，只读取 `PUBLISHED` 索引。
2. 在 SQL 中按 required control code、品类、地区及左闭右开有效期过滤。
3. BM25 取 Top-10；查询 embedding 后，pgvector 以 `<=>` 精确余弦距离取 Top-10。
4. 以 RRF `k=60` 融合，并用 `clause_id` 稳定打破并列。
5. 将候选并集交给 rerank API，严格校验返回索引唯一且在候选范围内，取 Top-3。
6. 从数据库重新核验每条引用的制度、文档、版本、原文、哈希和 control code。
7. 保存融合前后排名、分数、模型、尝试次数、耗时、覆盖状态和错误到 `retrieval_traces`。

状态含义：

| 状态 | 条件 | 集成方处理 |
| --- | --- | --- |
| `OK` | 所有 required control code 均有本次有效引用，且无规则冲突 | 合规模块可继续核验结构化事实 |
| `NO_EVIDENCE` | 至少一个 required control code 缺少 Top-3 支持 | `REVIEW_REQUIRED` |
| `CONFLICT` | 同一适用范围和 control code 存在冲突参数 | `REVIEW_REQUIRED` |
| `ERROR` | 索引、模型、维度、数据库、rerank 映射或引用核验失败 | `REVIEW_REQUIRED` |

故障时可以把 BM25 候选留在 trace 中帮助诊断，但结果状态仍是 `ERROR`，不能以 BM25 结果继续判定合规。

## 7. 内部集成

其他模块只依赖 `PolicyRetriever`：

```python
request = RetrievalRequest(
    task_id=task_id,
    task_revision=task_revision,
    snapshot_id=snapshot_id,
    policy_set_version="2026.09.1",
    policy_index_version="pidx-375fa65c082096e41d4f6b66",
    query="What RoHS evidence is required for electronics parts?",
    required_control_codes=["ROHS_COMPLIANCE"],
    category="Electronics",
    region="SG",
    evaluated_at=evaluated_at,
)
result = retriever.retrieve(request)
```

普通自动测试使用 `FixedPolicyRetriever` 以及 `data/examples/policy_rag/retrieval_result.json`，不得访问网络。集成人员需把 `policy_set_version + policy_index_version + retrieval_id` 冻结到 snapshot，并在发布结果前按现有规则再次检查 task revision。

合规模块只能消费引用、覆盖和状态。批准供应商状态、RoHS 记录和供应商身份必须通过对应的结构化表精确查询；相似度和 RAG 文本不能决定这些事实。

`PolicyExplanationService` 只把调用方已确认事实和本次 Top-3 引用交给解释客户端，并验证返回 claim 的 citation ID。该说明不能产生 `COMPLIANT`／`NON_COMPLIANT`、修改报价字段或改变供应商排序。

本轮没有增加公开搜索或 chatbot API，也没有修改现有 LangGraph。

## 8. 开发集评测

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$output = "evaluation/results/local/$stamp/policy-rag-development.json"

.\.venv\Scripts\python.exe -m supplier_comparison.rag evaluate `
  --dataset evaluation/reference/policy_rag/development_questions.json `
  --policy-set-version 2026.09.1 `
  --policy-index-version pidx-375fa65c082096e41d4f6b66 `
  --output $output
```

`evaluation/results/local/` 已忽略，不能提交真实模型结果。本地 2026-09-16 的一次受控运行结果为：

| 指标 | 结果 |
| --- | ---: |
| BM25 Recall@10 | 100% |
| pgvector Recall@10 | 100% |
| RRF Recall@10 | 100% |
| rerank Recall@3 | 91.67% |
| 引用支持率 | 100% |
| 状态准确率 | 100% |
| 平均总延迟 | 885.76 ms |
| ERROR | 0 / 8 |

8 题覆盖正常答案、无证据、旧有效期过滤和冲突。该成绩是本地开发集结果，不代表留出集或 Lightsail 已通过。不要根据留出问题调参。

## 9. 测试命令

普通无网络测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

显式 PostgreSQL 测试：

```powershell
$env:RUN_POSTGRES_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -q tests/rag/test_postgres_retrieval.py
Remove-Item Env:RUN_POSTGRES_TESTS
```

该测试覆盖真实 pgvector 精确检索、JSONB 品类／地区过滤、检索轨迹、关闭旧连接后恢复，以及没有 HNSW／IVFFlat。

## 10. 尚未完成的边界

- Lightsail 上的模型授权、重启、完整重建和资源测试尚未执行。
- 供应商身份、批准状态、RoHS、`ComplianceMatrix` 和确定性合规规则由合规负责人实现。
- LangGraph 节点、snapshot 绑定、当前 revision 发布检查由集成人员实现。
- React、审批、报告、公开问答和 chatbot 不属于本子系统。
- 当前只提供受控解释契约与固定客户端；真实解释模型适配应由调用方按同一引用约束接入，并单独记录调用。
