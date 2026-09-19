# LC4：分支集成、测试数据与前后端全流程验证指南

> 日期：2026-09-19  
> 当前开发分支：`lc`  
> 适用范围：合并 `main`、`seiran-q-patch-1` 后的当前本地工作区  
> 数据性质：本文涉及的 MCU、供应商、报价和制度均为合成演示数据，不是真实采购记录或 SME 验证结果。

## 1. 文档目的

本文记录本轮工作的四部分内容：

1. `main` 和 `seiran-q-patch-1` 分支合入后增加的能力与测试集。
2. 为匹配后端能力而补充的前端页面和交互。
3. 本地继续补齐的前后端功能及 `full_flow_demo` 统一演示数据包。
4. 已实际执行的测试、结果、复现步骤和仍未实现的边界。

本文描述的是当前本地 `lc` 工作区，不等同于远端 `origin/lc`。其中一部分功能和数据仍是未提交的本地改动，提交或推送前应再次检查 `git status`。

## 2. 分支集成记录

本轮关键提交顺序如下：

| 提交 | 说明 |
| --- | --- |
| `6e9be9d` | 将 `origin/main` 合入 `lc` |
| `6f0dfb2` | 协调合并后的接口与契约差异 |
| `194c812` | 将 `seiran-q-patch-1` 合入 `lc` |
| `182b1d1` | 修正 V9 数据集，使生成结果可复现 |

### 2.1 `main` 侧合入的主要能力

`main` 的相关提交补充了以下后端能力与验证材料：

- 有边界的调查流程（bounded investigations）。
- Policy 调查工具与经过校验的 Policy 引用。
- 批量审核、决策影响和入选差距分析。
- RAG 编排、解释和 PostgreSQL 条件集成测试。
- `electronics-v2` Policy 资料以及 RAG、调查、选择差距相关指南与测试。

这些能力的职责仍受项目架构约束：模型负责理解和解释，金额、单位、硬约束、状态及版本由确定性代码处理，Agent 不具备采购、审批或供应商联络权限。

### 2.2 共同历史中的 V8 边界数据

共同集成历史中包含 `quote_V8` 的五份边界条件 PDF：

| 文件 | 主要验证点 |
| --- | --- |
| `v8_supplier_a_wrong_package_currency.pdf` | 包装和币种不匹配 |
| `v8_supplier_b_wrong_revision_condition.pdf` | Revision 和物料状态不匹配 |
| `v8_supplier_c_unknown_fees_tax.pdf` | 费用与税费未知 |
| `v8_supplier_d_moq_over_budget_late.pdf` | MOQ、超预算和延期 |
| `v8_supplier_e_valid_best.pdf` | 满足条件的有效报价 |

目录：`data/generated/inputs/development/quote_V8/`

### 2.3 `seiran-q-patch-1` 侧合入的主要能力

`seiran-q-patch-1` 的核心提交为 `4d57d8b`，主要内容是：

- 稳定报价草稿创建流程。
- 增加 V9 采购偏好与边界条件数据。
- 增加报价草稿外键、提交状态和数据集测试。
- 增加独立参考答案，保持运行时数据与评测答案隔离。

后续提交 `182b1d1` 修正了生成器和 manifest，使 V9 文件哈希能够稳定复现。

## 3. 新增测试数据集

### 3.1 V9 报价权衡数据集

主要路径：

- 生成器：`data/generate_v9_tradeoff_data.py`
- 运行输入：`data/generated/inputs/development/quote_V9/`
- Manifest：`data/generated/manifests/quote_V9_manifest.json`
- 独立答案：`evaluation/reference/quote_V9/reference_answers.json`
- 数据说明：`data/generated/inputs/development/quote_V9/README.md`

数据组成：

- 10 组单行报价 PDF/CSV 对，应于供应商 A 至 J。
- 1 份供应商 K 的 USD 补充 CSV。
- 3 份采购需求。
- 每个任务最多组合 5 份报价，以符合当前单任务文件上限。

推荐使用的组合：

| 组合 | 供应商 | 目标 |
| --- | --- | --- |
| Preference trade-off | A、B、C、D、E | 成本和交期偏好权衡 |
| Boundary | F、G、H、I、J | 币种、延期、预算和包装边界 |
| Near tie | B、C、D、E、G | 接近成本下的次级偏好 |

V9 参考预期：

- 只按成本：C 和 E 均为 SGD 9,200，并列。
- 成本优先、交期破同分：E。
- 最快交付：第一组为 A，near-tie 组为 G。
- 成本差不超过 SGD 15 时选更快：D。
- H 延期，I 超预算，J 包装不匹配。

当前实现边界必须明确：

- 当前确定性偏好值只完整支持 `procurement_requirement_v9_cost.csv`。
- `FASTEST_CONFIRMED_DELIVERY`、次级偏好破同分和固定 USD→SGD 汇率仍是目标回归夹具，不代表已经实现。
- 供应商 D 的币种与当前后端契约仍存在待统一项。
- 供应商 K 用于未来验证 USD 5,700 按 1.61 换算为 SGD 9,177；当前不可把该结果描述为系统已支持的通用汇率计算。

### 3.2 `full_flow_demo` 统一演示数据包

为方便从需求上传一直演示到结果和 Summary，本轮新增：

- 生成器：`data/generate_full_flow_demo.py`
- 运行输入：`data/generated/inputs/development/full_flow_demo/`
- 操作者参考答案：`evaluation/reference/full_flow_demo/reference_answers.json`
- 数据说明：`data/generated/inputs/development/full_flow_demo/README.md`

数据包包含：

- 采购需求 PDF、TXT 和人工确认 JSON。
- 供应商 A、B、C 的报价 PDF 与 CSV。
- 合成 Policy TXT、元数据和已审核条款。
- 文件 manifest 与 SHA-256 哈希。

统一场景为 `FULL-FLOW-DEMO-001`：

| 字段 | 值 |
| --- | --- |
| 物料 | `QW-MCU9-DEMO` |
| 数量 | 1,000 pieces |
| Package / Revision / Condition | `QFN-32` / `R1` / `NEW` |
| 预算 | SGD 8,000 |
| 截止日期 | 2026-09-19 |

三家报价的预期业务结果：

| 供应商 | 预期状态 | 说明 |
| --- | --- | --- |
| A | `INFEASIBLE` | 违反硬约束 |
| B | 先 `PENDING`，补充后 `FEASIBLE` | 初始缺运费；人工确认缺失并填写 SGD 200 后总价 SGD 7,000 |
| C | `FEASIBLE` | 总价 SGD 7,100 |

完成正常补问后，B 是成本优先场景的推荐项。参考答案目录严禁挂载给运行时 Agent，避免答案泄漏。

## 4. 根据后端能力补充的前端

本轮将后端已有但前端缺少入口的能力补到了工作区。主要路由如下：

| 页面 | 路由 | 用途 |
| --- | --- | --- |
| 新建任务 | `/tasks/new` | 创建需求、上传需求文件 |
| 编辑需求 | `/tasks/:taskId/edit` | 修改采购需求，Policy Binding 只读 |
| 报价与证据 | `/tasks/:taskId/quotes/new` | 上传、解析、纠正和提交报价 |
| 集中审核 | `/tasks/:taskId/review` | 汇总阻塞字段与人工审核 |
| 调查记录 | `/tasks/:taskId/investigations` | 查看后端保存的调查 Case |
| 决策比较 | `/tasks/:taskId/decision` | 比较正式报价和结果 |
| 入选差距 | `/tasks/:taskId/gaps` | 展示候选项与入选项的差距 |
| 合规 | `/tasks/:taskId/compliance` | 展示 Policy 检索及合规引用 |
| Summary | `/tasks/:taskId/summary` | 生成、轮询、失败重试和历史查看 |
| 版本/审计 | `/tasks/:taskId/audit` | Revision、废弃理由、文件访问等审计 |

新增或重构的关键前端文件包括：

- `frontend/src/components/RequirementFields.tsx`
- `frontend/src/pages/EditTaskPage.tsx`
- `frontend/src/pages/InvestigationPage.tsx`
- `frontend/src/pages/ReviewPage.tsx`
- `frontend/src/pages/SelectionGapPage.tsx`
- `frontend/src/pages/SummaryPage.tsx`
- `frontend/src/pages/AuditPage.tsx`
- `frontend/src/components/FilePreviewDialog.tsx`


## 5. 本轮补齐的六项前后端闭环

### 5.1 采购需求文件解析

- 支持 PDF、TXT、MD；前端移除 DOCX。
- 文件大小上限为 10 MiB。
- 扫描件、空文本、错误编码和错误格式显式失败。
- 草稿异步解析，前端轮询状态并填入候选值。
- 候选字段保存证据和来源 ID，模型只能引用当前草稿提供的来源。
- 人工最终确认分别标记 `USER_INPUT` 或 `USER_CORRECTION`，不覆盖原始提取值。

相关接口：

- `POST /api/v1/requirement-drafts`
- `GET /api/v1/requirement-drafts/{draft_id}`
- `POST /api/v1/requirement-drafts/{draft_id}/discard`

### 5.2 修改采购需求

- 前端复用需求字段组件，Policy Binding 只读。
- 写入完整 `ProcurementRequirement`，同时提交 `expected_task_revision`。
- 修改后新增 Requirement 与 `REQUIREMENT_UPDATED` Revision。
- 当前结果、图运行、问题、快照和未提交草稿失效，但历史记录保留。
- 已有正式报价时自动创建全量重算 Job；没有正式报价时回到 `DRAFT`。
- `ABANDONED` 任务拒绝写操作。

接口：`PUT /api/v1/tasks/{task_id}/requirement`

### 5.3 废弃任务

- 要求填写理由并二次确认。
- 采用不可恢复的软废弃，不删除原件、报价、结果、Summary 或审计记录。
- 终止或 supersede 活动作业，状态设为 `ABANDONED`。
- 废弃后页面只读，不提供恢复接口。

接口：`POST /api/v1/tasks/{task_id}/abandon`

### 5.4 历史原件预览与下载

- 按所有者、`task_id` 和 `document_id` 校验访问。
- 校验文件仍位于受控存储根目录，防止路径穿越。
- PDF 使用 iframe 预览；TXT/MD 加载为文本；均支持下载。
- 响应包含正确 Content-Type、文件名、ETag、private cache 和 `nosniff`。
- 每次预览或下载记录 `PREVIEW`/`DOWNLOAD` 审计事件。

接口：

`GET /api/v1/tasks/{task_id}/documents/{document_id}/content?disposition=inline|attachment`

### 5.5 AI Summary

- 后端从冻结结果构建确定性事实，包括资格、成本、交期、风险、Policy 和证据引用。
- 模型只负责输出经过 Pydantic 校验的中文叙述，不重新计算或修改推荐结论。
- Worker 异步生成，最多两次模型尝试。
- 输入版本变化后旧 Summary 标记为 `STALE`，但仍可查看。
- 失败会保留错误和调用记录，可对同一报告有限重试，不伪造成功结果。

接口：

- `POST /api/v1/tasks/{task_id}/summaries`
- `GET /api/v1/tasks/{task_id}/summaries`
- `GET /api/v1/tasks/{task_id}/summaries/{summary_id}`
- `POST /api/v1/tasks/{task_id}/summaries/{summary_id}/retries`

### 5.6 任务中心服务端查询

任务列表支持：

- `query`
- `status`
- `sort`
- `limit`
- `offset`

响应包含 `items`、`total`、`limit`、`offset` 和全量 `status_counts`。搜索覆盖 Task ID、场景编号、制造商和料号；前端使用 300 ms 防抖，不再只过滤浏览器中的最近 50 条。

## 6. 数据库与后端变更

新增迁移：

- `migrations/versions/d8e91a2bc4f0_complete_frontend_backend_features.py`
- `migrations/versions/e91bc30d5a72_normalize_feature_jsonb_columns.py`

关键变更：

- 新增 `requirement_drafts`。
- 新增 `summary_reports`。
- 新增 `document_access_events`。
- `jobs` 增加 `requirement_draft_id`、`summary_id`。
- `task_revisions` 增加结构化 `details`。
- `requirements` 增加来源 artifact 引用。
- 任务增加软废弃时间字段。
- PostgreSQL 中对应结构化字段规范为 JSONB。

当前迁移 Head：`e91bc30d5a72`。

### 6.1 联调中发现并修复的问题

实际跑通全流程时修复了以下问题：

- PostgreSQL 外键顺序：创建需求草稿后先 flush，再创建关联 Job。
- 需求解析和 Summary 的真实模型调用补齐网络调用与等待参数。
- Worker 通用异常会写入 `FAILED`，避免 Job 永久停留在 `RUNNING`。
- Worker 补齐 `DraftReviewRunner` 导入。
- 人工确认 `UNKNOWN` 运费状态后允许进入正式工作流，但金额保持空值，并继续触发确认和金额补问。
- Summary 曾混淆 B/C 的总价和运费证据；现改为传入压缩后的确定性事实与规范引用 ID，提示词版本升级为 `procurement-summary/1.0.1`，旧结果标记 `STALE`。
- 真实需求模型曾返回字符串布尔值、字符串数量及不统一单位；增加确定性规范化，需求提示词版本升级为 `requirement-intake/1.0.1`。
- `scripts/run_real_extraction.py` 增加 V1 CSV 运行支持。

## 7. 已完成的端到端演示结果

使用 `full_flow_demo` 已完成一次真实本地全流程：

| 项目 | 结果 |
| --- | --- |
| Task | `task_451092ab57dd4fe2a9cc3d5875a7bb13` |
| Scenario | `FULL-FLOW-DEMO-001` |
| 最终状态 | `COMPLETED` |
| Task Revision | 6 |
| 正式报价 | 3 份 |
| Result Artifact | `artifact_1293c754cc79477a9abd107aa9d552b7` |
| 最新 Summary | `summary_fe42998b8ecf4232937a7a4dae4a58b2` |

Policy 也已完成真实导入、发布和检索：

| 项目 | 值 |
| --- | --- |
| Policy Set | `full-flow-demo-electronics` |
| Version | `2026.09.1-demo` |
| Index | `pidx-41b0d83811a378c11d76daf1` |
| 条款数 | 12 |
| 覆盖控制 | Approved supplier、RoHS、金额审批 |

三个控制项的真实检索状态均为 `OK`，每项返回 3 条引用；Embedding 和 Rerank 各 1 次尝试。该结果只证明检索链路可用，不等于系统已替代人工完成正式合规审批。

需求文件的真实模型验证结果：

- API → PostgreSQL → Worker 链路完成。
- 草稿状态为 `READY`。
- 产生 19 个候选字段，模型调用 1 次。
- 与人工确认 JSON 比较为 19/19 一致。
- 验证用临时草稿随后已废弃。

报价真实模型验证结果：

| 格式 | A | B | C |
| --- | --- | --- | --- |
| PDF | 1 次调用 | 2 次调用 | 1 次调用 |
| CSV | 1 次调用 | 1 次调用 | 1 次调用 |

每份报价均输出 30 个候选字段；B 的运费保持缺失，没有被模型猜成 0。运行结果为结构校验 `PASSED/UNSCORED`：它表示解析结果通过结构与来源检查，不应被描述为完整字段准确率已经评分通过。

## 8. 实际测试结果

以下结果是在当前本地工作区实际执行所得：

| 测试项 | 结果 |
| --- | --- |
| 全仓库 Pytest | `648 passed, 14 skipped`，15.42 秒 |
| `full_flow_demo` 数据集专项 | `3 passed` |
| PDF/CSV 解析相关测试 | `106 passed` |
| 需求解析与数据集规范化 | `13 passed` |
| 真实提取运行脚本测试 | `16 passed` |
| 相关确定性集成测试 | `27 passed` |
| V9 报价草稿专项 | `10 passed, 2 deselected` |
| V9 Manifest | 29 个文件，0 个哈希不匹配 |
| PostgreSQL 条件测试 | `8 passed, 1 skipped` |
| 前端 ESLint | 通过 |
| 前端 TypeScript/Vite Build | 通过，96 modules transformed |
| Alembic Check | `No new upgrade operations detected` |

14 个全量测试 skip 主要来自：

- 5 个需要付费真实 Agent 调用的测试。
- 8 个有 PostgreSQL/环境条件的恢复测试，其中包含 1 个真实调用条件项。
- 1 个 PostgreSQL Policy 检索条件项。

跳过项不应写成通过；需要相应环境变量或付费模型条件后单独执行。

## 9. 本地复现方式

以下命令均从仓库根目录开始。不要从根目录直接执行 `npm run dev`，因为 `package.json` 位于 `frontend/`。

### 9.1 启动 PostgreSQL 并升级数据库

```bash
docker compose up -d --wait postgres
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m alembic upgrade head
```

检查迁移：

```bash
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m alembic current
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m alembic check
```

### 9.2 启动 API

```bash
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m uvicorn supplier_comparison.backend.api:app \
  --host 127.0.0.1 --port 8000
```

### 9.3 启动 Worker

另开一个终端：

```bash
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m supplier_comparison.worker run-loop
```

### 9.4 启动前端

再开一个终端：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM/frontend
npm run dev -- --host 127.0.0.1
```

访问：`http://127.0.0.1:5173`

### 9.5 运行重点测试

全量测试：

```bash
.venv/bin/python -m pytest -q
```

`full_flow_demo`：

```bash
.venv/bin/python -m pytest \
  tests/backend/test_full_flow_demo_dataset.py \
  tests/backend/test_intake_summary.py -q
```

V9 报价草稿：

```bash
.venv/bin/python -m pytest \
  tests/backend/test_quote_drafts.py -k v9 -q
```

PostgreSQL 条件测试：

```bash
env RUN_POSTGRES_TESTS=1 \
  .venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m pytest \
  tests/backend/test_postgres_recovery.py \
  tests/rag/test_postgres_retrieval.py -q
```

前端检查：

```bash
cd frontend
npm run lint
npm run build
```

## 10. 建议的完整手工演示顺序

1. 在 Policy 资源页导入并发布 `full_flow_demo` 的合成 Policy。
2. 新建任务，上传需求 PDF 或 TXT，等待需求草稿状态变为 `READY`。
3. 核对候选值和证据，人工确认需求并绑定已发布 Policy。
4. 分别上传 A、B、C 报价 PDF 或 CSV，完成解析和字段审核。
5. 正式提交三份报价。
6. 对 B 的缺失运费先确认其确实缺失，再人工输入 SGD 200。
7. 启动或等待全量计算，检查 A 不可行、B/C 可行，B 总价 SGD 7,000。
8. 查看调查记录、决策比较、入选差距和 Policy 引用。
9. 生成 Summary，核对其中的事实、引用 ID 和推荐结果与冻结结果一致。
10. 在版本/审计页检查 Revision、人工纠正、文件访问和 Summary 版本。

如果修改需求，应看到 Task Revision 推进、旧结果失效并重新排队；如果废弃任务，应看到全页面只读，但历史内容仍可访问。

## 11. 已知限制和验收边界

- 所有数据均为合成数据，不代表真实器件、真实供应商或真实报价。
- V9 中的最快交付偏好、次级偏好破同分和固定汇率仍主要用于回归设计，尚未形成完整产品能力。
- `PASSED/UNSCORED` 是结构验证结果，不是人工标注的字段级准确率。
- Policy 检索成功不等于采购批准或最终合规审批。
- Summary 是解释性产物，不能改变确定性计算结果，也不具备审批权。
- `ABANDONED` 任务不可恢复；当前不提供永久删除接口。
- 真实模型、Embedding 和 Rerank 测试依赖 `.env` 中的后端凭据；密钥不得写入前端、日志、测试夹具或本文档。
- 当前本地工作区还有未提交变更。提交前应运行全量测试、前端检查、Alembic check，并确认没有把运行时文件、密钥或评测参考答案挂载给 Agent。

## 12. 相关文档

- `docs/guide/guide_V1_V7_TESTING.md`
- `docs/guide/guide_RAG.md`
- `docs/guide/guide_RAG_ORCHESTRATION.md`
- `docs/guide/guide_SELECTION_GAP_POLICY.md`
- `docs/guide/LIGHTSAIL_RAG_ACCEPTANCE.md`
- `docs/guide/guide_frontend.md`
- `docs/ARCHITECTURE.md`
- `docs/DATA.md`
- `docs/WORKFLOW.md`
