# Week2 制度检索子系统交接指南

2026-09-17 更新：真实聊天模型制度解释已实现，最新入口见 [guide_RAG_EXPLANATION.md](guide_RAG_EXPLANATION.md)。下面早期交付中的“固定解释客户端”及“真实适配待实现”状态保留为历史记录。

2026-09-16 更新：新制度 `2026.09.2`、导入验证与按控制码／要求级检索编排已交付，最新使用入口为 [guide_RAG_ORCHESTRATION.md](guide_RAG_ORCHESTRATION.md)。本文件的 `electronics-v1` 示例及待签字状态是前一轮交付背景，不会自动更新旧采购快照。

> 状态：本地实现与真实 SiliconFlow 开发集验证完成（2026-09-16）；制度检索发布门禁已接入 LangGraph（2026-09-17）。Lightsail 验收为 `BLOCKED_EXTERNAL`：当前工作环境没有 AWS CLI、AWS 环境变量、AWS profile 或实例入口，不能把本地结果计作 AWS 验收。供应商注册表和完整合规矩阵仍由对应负责人完成。

## 1. 本轮交付

本子系统负责把版本化采购制度导入 PostgreSQL，并通过 BM25、pgvector 精确余弦检索、RRF 和 rerank API 返回可核验的 Top-3 条款。它只检索制度和生成受控说明，不识别供应商身份、不查询批准状态或 RoHS 事实、不决定 `ComplianceMatrix`，也不改变价格和交期排序。

主要代码位于：

| 路径 | 作用 |
| --- | --- |
| `src/supplier_comparison/rag/contracts.py` | `RetrievalRequest`、`RetrievalResult`、`PolicyCitation`、`PolicyExplanation` 和 `PolicyRetriever` 冻结契约 |
| `src/supplier_comparison/rag/clients.py` | SiliconFlow embedding／rerank 客户端与无网络固定客户端 |
| `src/supplier_comparison/rag/manifest.py` | manifest 白名单、Markdown 条款边界、日期、重复 ID 和内容哈希校验 |
| `src/supplier_comparison/rag/models.py` | 制度、文件草稿、索引、导入和检索轨迹表 |
| `src/supplier_comparison/rag/importer.py` | 幂等导入、索引版本计算、失败隔离和原子发布 |
| `src/supplier_comparison/rag/uploads.py` | PDF／TXT 不可变保存、文本提取、人工审核草稿与发布服务 |
| `src/supplier_comparison/rag/repository.py` | SQL 范围过滤、pgvector 精确余弦 Top-10、引用原文／哈希复核和轨迹持久化 |
| `src/supplier_comparison/rag/retriever.py` | BM25 Top-10、RRF `k=60`、rerank Top-3、覆盖／冲突／错误状态 |
| `src/supplier_comparison/rag/explanation.py` | 只允许调用方确认事实及本次 citation ID 的解释边界 |
| `src/supplier_comparison/rag/evaluation.py` | 分阶段 Recall、引用支持率、状态准确率、延迟和错误评测 |
| `migrations/versions/c83a72d80b1f_create_policy_retrieval_schema.py` | RAG 业务表 migration；不管理 LangGraph checkpoint 表 |
| `migrations/versions/e2a4c6d8f0b1_create_policy_file_import_schema.py` | 文件导入草稿、修订和审核条款表 |
| `migrations/versions/f3b5d7e9a1c2_bind_policy_versions_to_tasks.py` | 将冻结的制度集合、索引、品类和地区绑定到采购任务 |

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

当前 head 为 `f3b5d7e9a1c2`。RAG 表为：

- `policy_sets`
- `policy_documents`
- `policy_clauses`
- `policy_indexes`
- `policy_clause_embeddings`
- `policy_import_runs`
- `retrieval_traces`
- `policy_file_imports`
- `policy_file_import_clauses`

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

### 5.1 从后端上传 PDF／TXT 制度

文件上传接口和 CLI 最终复用同一个 `PolicyImporter`。上传接口只接受 `application/pdf` 的 `.pdf` 文件和 UTF-8 `text/plain` 的 `.txt` 文件，单文件默认上限 5 MiB，PDF 默认上限 50 页，提取正文默认上限 200,000 字符。PDF 使用 pdfplumber 提取原生文本；扫描件或无可提取文字的 PDF 返回 `policy_pdf_requires_ocr`，当前不会自动 OCR。

后端流程固定为：

```text
上传并提取 → REVIEW_REQUIRED → 替换审核后条款 → READY_TO_PUBLISH → 发布 → PUBLISHED
```

接口如下：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/api/v1/policy-sets` | 列出可以绑定到新任务的已发布制度集／索引组合 |
| `GET` | `/api/v1/policy-imports` | 列出当前操作者的制度文件导入摘要和状态 |
| `POST` | `/api/v1/policy-imports` | multipart 上传 PDF／TXT；`metadata` 为 JSON 字符串 |
| `GET` | `/api/v1/policy-imports/{policy_import_id}` | 查询提取正文、草稿条款、修订和状态 |
| `PUT` | `/api/v1/policy-imports/{policy_import_id}/clauses` | 以 `expected_revision` 替换整组已审核条款 |
| `POST` | `/api/v1/policy-imports/{policy_import_id}/publish` | 显式生成 embedding 并原子发布索引 |

三个写接口都要求 `Idempotency-Key`。API 不返回文件系统路径，原始文件使用生成 ID 写入不可覆盖位置；Compose 使用独立 `policy_files` 卷。上传生成的草稿没有 `control_code`，不能直接发布。审核请求必须为每条条款提供唯一 `clause_id`、标题、完整原文、`control_code` 和可选的 `rule_parameters`。

两个列表接口返回 `{items, total, limit, offset}`，默认 `limit=50`，最大为 100。`policy-imports` 可按 `status`、`policy_set_version`、`category` 和 `region` 筛选，只返回当前服务端操作者的记录；列表摘要不包含提取全文、条款正文或文件路径。`policy-sets` 只返回制度集与索引均为 `PUBLISHED` 的可用组合，可按 `category` 和 `region` 筛选，并返回创建任务所需的 `policy_set_version` 与 `policy_index_version`。前端应从该接口选择冻结版本，不应从导入草稿推断可用索引。

可在启动 API 后从 `http://localhost:8000/docs` 使用 Swagger 完成完整流程。上传表单中的 `metadata` 示例：

```json
{
  "policy_set_id": "uploaded-electronics-policy",
  "policy_set_version": "2026.09.1",
  "policy_id": "POL-UPLOAD-001",
  "document_id": "DOC-UPLOAD-001",
  "document_version": "1.0.0",
  "title": "Uploaded Electronics Policy",
  "effective_from": "2026-09-17T00:00:00Z",
  "effective_to": null,
  "categories": ["Electronics"],
  "regions": ["SG"]
}
```

当前一个上传草稿对应一个文档和一个制度集合版本。已发布内容不可修改；修改正文时应创建新的 `policy_set_version` 并重新上传。上传和发布使用现有服务端操作者身份，不接受请求体覆盖身份。正式部署前仍需由统一认证模块限制制度管理接口的访问角色。

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

普通自动测试使用固定检索适配器，不访问网络。创建采购任务时可提交冻结的制度绑定：

```json
{
  "policy_binding": {
    "policy_set_version": "2026.09.1",
    "policy_index_version": "pidx-375fa65c082096e41d4f6b66",
    "category": "Electronics",
    "region": "SG"
  }
}
```

绑定制度且存在可发布推荐的任务，在最终确定性比较之后、发布推荐之前，分别检索 `APPROVED_SUPPLIER`、`ROHS_COMPLIANCE` 和 `AMOUNT_APPROVAL`。没有可行报价时直接发布确定性的 `NO_FEASIBLE_QUOTES`，不让无关制度服务故障阻塞该结论。每个控制码单独执行一次 Top-3 检索，避免一个 Top-3 结果无法覆盖三个以上控制码。LangGraph checkpoint 只保存 snapshot、ComparisonResult 和检索 artifact ID；引用正文保存在 PostgreSQL artifact／retrieval trace 中。

三个结果均为 `OK` 且实际包含对应控制码引用时，图才调用现有 revision 保护发布结果。任一结果为 `NO_EVIDENCE`、`CONFLICT`、`ERROR`，或返回错误制度版本／缺少对应引用时，图创建 `POLICY_EVIDENCE_REVIEW` issue 并 `interrupt()`，不会设置 `current_result_id`。临时模型或数据库故障修复后，可以提交：

```json
{
  "expected_task_revision": 2,
  "answer": {"answer_type": "RETRY_POLICY_RETRIEVAL"}
}
```

恢复后会用新 task revision 重新冻结 snapshot、重新比较和重新检索。制度正文或索引版本发生变化时不能沿用旧绑定，应以新发布版本创建新任务。未提交 `policy_binding` 的旧任务保留 Week1 行为，不调用 RAG。

合规模块只能消费引用、覆盖和状态。批准供应商状态、RoHS 记录和供应商身份必须通过对应的结构化表精确查询；相似度和 RAG 文本不能决定这些事实。

`PolicyExplanationService` 只把调用方已确认事实和本次 Top-3 引用交给解释客户端，并验证返回 claim 的 citation ID。该说明不能产生 `COMPLIANT`／`NON_COMPLIANT`、修改报价字段或改变供应商排序。

本轮没有增加公开搜索或 chatbot API。LangGraph 只调用内部 `PolicyRetriever`，不接受用户提供任意查询或任意制度路径。

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
| 平均总延迟 | 1856.16 ms |
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

- `BLOCKED_EXTERNAL`：Lightsail 上的模型授权、重启、完整重建和资源测试尚未执行。解除条件是提供官方实例入口及模型访问配置；不得静默改用个人 API 作为 AWS 结果。执行步骤和证据模板见 [`LIGHTSAIL_RAG_ACCEPTANCE.md`](LIGHTSAIL_RAG_ACCEPTANCE.md)。
- 供应商身份、批准状态、RoHS、`ComplianceMatrix` 和确定性合规规则由合规负责人实现。
- LangGraph 检索节点、snapshot 版本绑定、失败中断、受控重试和当前 revision 发布检查已实现；供应商注册事实及完整合规矩阵尚未接入。
- React、审批、报告、公开问答和 chatbot 不属于本子系统。
- 当前只提供受控解释契约与固定客户端；真实解释模型适配应由调用方按同一引用约束接入，并单独记录调用。

## 11. 本轮完成性审计

| 方案要求 | 状态 | 验收证据 |
| --- | --- | --- |
| 冻结契约、SiliconFlow 客户端与 1024 维 smoke | 已完成 | `tests/rag/test_contracts.py`、`tests/rag/test_clients.py`；本机和 Compose 真实 smoke 均成功 |
| 5 份英文制度、24 个完整条款 | 已完成 | `data/policies/electronics-v1/`；manifest 结构测试 |
| 版本化 PostgreSQL 表与原子发布 | 已完成 | migration 升级、降级、再升级；失败导入无部分向量测试 |
| 幂等、安全路径导入与完整重建 | 已完成 | 重放、越界、重复 ID、同版本异哈希、模型／预处理换版测试 |
| BM25＋pgvector＋RRF＋rerank Top-3 | 已完成 | 固定适配器测试、真实 PostgreSQL 精确检索测试和 8 题真实模型评测 |
| 引用核验、覆盖、冲突与错误状态 | 已完成 | 原文／哈希复核、旧有效期、无证据、冲突、rerank 映射及索引维度不匹配测试 |
| LangGraph 制度检索发布门禁 | 已完成（固定适配器集成测试） | 三控制码独立检索、snapshot 绑定、成功发布、失败中断、不发布及 transient retry 恢复测试 |
| 重启持久化 | 已完成（本地） | Compose PostgreSQL 重启后保留 26 条条款、2 个已发布索引和检索轨迹 |
| 干净卷完整重建 | 已完成（本地） | 独立 `supplier-rag-rebuild` Compose project 完成 migration、真实模型 smoke 和 24 条制度发布，并清理独立临时卷 |
| Lightsail 官方环境验收 | `BLOCKED_EXTERNAL` | 当前没有官方实例或 AWS 访问入口；本地验证不作为替代；见 [`LIGHTSAIL_RAG_ACCEPTANCE.md`](LIGHTSAIL_RAG_ACCEPTANCE.md) |
| A／C 对制度业务语义签字 | 待对应负责人确认 | 本轮只保证格式、哈希、导入和检索约束；逐条清单见 [`POLICY_SEMANTIC_REVIEW.md`](POLICY_SEMANTIC_REVIEW.md) |
