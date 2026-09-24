# QuoteWise 前端工作台实现与交付指南

日期：2026-09-17

范围：任务中心、采购需求、报价草稿审核、决策比较、Policy 资源与合规证据、版本审计及 AI Summary

说明：本文以当前仓库代码和后端实际契约为准，描述本版 QuoteWise 前端的实现、运行方式与交接边界。页面中的采购任务、供应商、报价和器件示例均可能是合成数据；前端展示的推荐不代表采购批准，Policy 检索结果也不代表最终合规审批。

## 1. 本次工作范围

本次工作的核心是把原先分散的“审核与分析”流程重组为一个可执行、可追踪的采购工作台，并把后端已经提供的报价草稿、策略目录、制度检索和历史版本能力接到前端。

任务主流程收缩为四个阶段：

```text
采购需求 → 报价与审核 → 决策比较 → Summary
```

任务工作区保留两个辅助页面：

- `合规`：展示任务绑定的 Policy 和随结果冻结的制度检索证据。
- `版本 / 审计`：展示报价、人工问题和比较结果的历史版本。

本次前端范围包括：

- 按原型统一深色侧栏、任务头部、四阶段时间线、紧凑卡片和工作区标签。
- 删除独立“审核与分析”入口，将报价审核放回“报价与证据”，将运行状态和分析结果放入“决策比较”。
- 接入真实 `QuoteDraft` 两阶段流程，在正式报价写入任务前完成人工审核。
- 接入真实 Policy 导入目录、条款审核、发布、任务绑定和制度证据展示。
- 接入任务、报价、问题和结果历史，提供可审计视图。
- Summary 页面连接版本化后端作业，分别展示确定性事实和受约束模型叙述。

为支持“先审核草稿、后正式提交”，本轮同时增加了最小必要的后端持久化、API、Worker 作业和数据库迁移。除此之外，前端仍以后端接口和枚举为唯一契约来源，不在浏览器内复制金额、可行性或推荐规则。

## 2. 已完成功能

### 2.1 导航与任务工作区

- 侧栏保留“任务中心”“新建任务”“规则资源库”，已删除独立“审核与分析”。
- 旧地址 `/reviews` 自动返回任务中心；`/reviews/:taskId` 自动进入对应任务的“报价与证据”，避免旧链接直接失效。
- 任务头部统一显示场景编号、任务标题、数量、状态、Task Revision 和四阶段时间线。
- 工作区标签统一为：`概览`、`报价与证据`、`决策比较`、`合规`、`Summary`、`版本 / 审计`。
- 页面根据正式报价数量、运行状态、结果和阻塞问题定位当前阶段，但不把合规和审计伪装成主流程步骤。

### 2.2 任务中心与采购需求

任务中心已经接入真实任务列表和后端健康检查，并提供：

- 按场景编号、任务 ID、制造商和料号搜索。
- 按任务状态筛选。
- 按最近更新、最近创建及计划下单日期排序。
- 每 5 秒自动刷新任务状态。
- 显示 Task Revision、计划下单日期、最近更新时间和当前状态。

当前搜索、筛选和排序是在浏览器内对已取回的最近任务执行，不是服务端全量检索。

新建采购任务页面已经完成：

- 采购需求字段录入、前端基础校验和后端权威校验。
- 金额使用十进制字符串提交，不在浏览器中使用浮点数计算采购结果。
- 可选绑定后端 `/policy-sets` 返回的已发布 Policy。
- 默认明确选择“不绑定 Policy”；绑定时必须选择具体策略版本、索引版本、分类和地区。
- 创建后在任务概览、合规和审计页面恢复并展示冻结的 Policy Binding。
- 相同创建请求重试复用相同请求体和 `Idempotency-Key`。

采购需求文件的“解析并自动填入”使用后端持久化草稿和 Worker，支持 PDF/TXT/MD。概览页可修改采购需求或软废弃任务；两者均推进版本并保留审计记录。

### 2.3 报价草稿审核与正式提交

“报价与证据”页面已接入真实报价草稿流程：

1. 用户上传单个 PDF 或 CSV 报价，文件必须非空且不超过 5 MiB。
2. 后端创建草稿和 `DRAFT_REVIEW` 作业；草稿上传不推进 Task Revision，也不会进入正式报价历史。
3. Worker 调用解析、模型适配和确定性审核流程，页面轮询显示处理状态。
4. 页面展示全部解析字段的当前值、状态和证据。
5. 已验证字段只读；缺失、冲突或低可信等阻塞字段在对应字段旁显示原因和修正输入框。
6. 人工修正提交后重新执行确定性审核；全部阻塞解除后草稿进入 `READY_TO_SUBMIT`。
7. 用户点击“正式提交报价”后，后端原子检查 Task Revision 和 Draft Revision，再写入正式 Quote/Document，并只推进一次 Task Revision。

草稿状态已经完整映射：

| 状态 | 前端含义 |
| --- | --- |
| `UPLOADED` | 文件已接收，等待 Worker |
| `PROCESSING` | 正在解析和审核，显示运行指示 |
| `REVIEW_REQUIRED` | 存在需要人工处理的阻塞字段 |
| `READY_TO_SUBMIT` | 审核通过，可以正式提交 |
| `SUBMITTED` | 已转为正式报价 |
| `FAILED` | 处理失败，显示安全错误信息 |
| `STALE` | 任务输入已变化，必须废弃后重新上传 |
| `DISCARDED` | 草稿已废弃 |

页面同一时间只允许处理一个活动草稿，并提供：

- 上传失败后重试相同幂等请求。
- 按当前 Draft Revision 批量修正阻塞字段。
- 草稿废弃。
- 正式提交门禁。
- 已正式提交报价及文件版本历史。
- 兼容旧任务遗留的报价字段、运费和证据问题。

历史正式报价同时提供元数据、受控原件预览和下载；后端校验任务归属并记录文件访问事件。

### 2.4 决策比较与结果展示

`/tasks/:taskId/decision` 始终可以进入，并根据后端状态显示不同操作：

- 没有正式报价：提示先完成报价审核，并链接回“报价与证据”。
- 有正式报价但未运行：提供开始决策分析入口。
- 排队或运行中：显示加载动画、运行状态和已耗时。
- 运行失败：显示安全错误信息并提供后端允许的重试。
- 报价字段或证据阻塞：链接回“报价与证据”。
- Policy 检索阻塞：链接到“合规”。
- 当前结果存在：进入真实结果页。

结果页展示后端冻结的：

- 供应商比较矩阵。
- `FEASIBLE / INFEASIBLE / PENDING` 状态。
- 已知成本、确认总成本、采购数量和预计到货日期。
- 推荐范围、风险和阻塞原因。
- 解析字段、报价证据和来源位置。
- Policy 制度证据摘要。
- Result ID、Task Revision、Graph Run 和规则版本。

前端不提供权重滑杆、自由问答，也不自行重新计算金额、可行性和排名；显示内容以后端结果为准。推荐只表示系统分析结果，不表示审批、中标或采购授权。

### 2.5 Policy 资源库与任务绑定

制度管理已经从本地模拟数据切换为真实后端流程。

策略上传支持：

- 支持多选 PDF、UTF-8 TXT 或 Markdown，单个文件最大 5 MiB。
- 填写制度集名称、生效时间、分类和地区；技术 ID 和版本由系统生成。
- 上传成功后进入真实策略审核详情页。

制度导入目录支持：

- 展示标题、文件、状态、revision、策略版本、分类、地区、条款数和更新时间。
- 已发布制度与待审核文件分区展示。
- 已发布制度支持“发布新版本”和“停用制度版本”。
- 点击进入审核详情；已发布记录只读。

条款审核与发布支持：

- 从 URL 读取 `policy_import_id`，刷新后从后端恢复数据。
- 编辑、增加、删除和调整条款顺序。
- 编辑 `clause_id`、标题、正文、`control_code` 和 JSON `rule_parameters`。
- 使用 `expected_revision` 防止静默覆盖并发修改。
- 保存审核后进入 `READY_TO_PUBLISH`，发布后显示 Policy Index Version 和 Import Run ID。
- revision 冲突时要求接受服务端版本；发布失败时保留重试状态。

已发布制度目录展示制度集、版本、文件数、条款数、适用范围和发布时间。新建任务只能选择仍处于可用状态的已发布版本，不能手填或自动选择“最新版本”；停用版本保留历史记录，但不再出现在任务绑定列表中。

### 2.6 合规与制度证据

合规页展示任务冻结的 Policy Binding 和比较结果中的真实制度检索记录：

- `OK / NO_EVIDENCE / CONFLICT / ERROR` 检索状态。
- 已覆盖和缺失的控制项。
- 错误代码、Embedding/Rerank 模型、候选数量和耗时。
- Policy、文档、条款版本、原文、控制代码和检索排名。
- 可折叠查看 BM25、Vector、Fusion 和 Rerank 分数。

没有绑定 Policy 时，页面明确显示“未绑定 Policy，未执行制度检索”，不会显示为已通过。`POLICY_EVIDENCE_REVIEW` 问题也已移到合规页，用户可以在修复制度服务后提交 `RETRY_POLICY_RETRIEVAL`。

页面严格区分“找到制度证据”和“通过最终合规审批”；当前系统只实现前者。

### 2.7 版本审计与 Summary

“版本 / 审计”页面集中展示后端已有历史：

- 当前 Task Revision、Result 和 Policy Binding。
- Quote/Document 版本、哈希、文件类型和当前版本标记。
- 人工问题的类型、状态、问题、回答、创建/解决 revision、回答人和时间。
- 结果的 revision、Graph Run、规则版本、评估时间、当前/历史标记和 Policy 检索状态。
- 从审计页打开当前或历史结果。

后端没有统一事件时间线所需的完整时间字段，因此前端按 API 原顺序和 revision 展示，不虚构事件先后关系。

`Summary` 已替代原“审批 / 报告”命名。页面基于当前冻结结果创建异步摘要，展示确定性事实、模型叙述、版本绑定、失败重试和历史状态；摘要仍不代表审批。

## 3. 实现思路

### 3.1 以后端契约为唯一事实来源

前端类型集中在 `frontend/src/api/types.ts`，请求集中在 `frontend/src/api/client.ts`。页面不自行定义第二套状态或业务计算公式。

主要真实接口如下：

| 能力 | 接口 |
| --- | --- |
| 任务 | `GET/POST /api/v1/tasks`、`GET /api/v1/tasks/{task_id}` |
| 报价草稿 | `POST/GET /api/v1/tasks/{task_id}/quote-drafts` |
| 草稿详情 | `GET /api/v1/tasks/{task_id}/quote-drafts/{draft_id}` |
| 草稿修正 | `PUT /api/v1/tasks/{task_id}/quote-drafts/{draft_id}/corrections` |
| 草稿提交/废弃 | `POST .../submit`、`POST .../discard` |
| 正式报价历史 | `GET /api/v1/tasks/{task_id}/quotes` |
| 决策运行 | `POST /api/v1/tasks/{task_id}/runs` 及 Job retry |
| 问题与结果历史 | `GET .../issues`、`GET .../results` |
| Policy 导入 | `POST/GET /api/v1/policy-imports` |
| Policy 审核与发布 | `PUT .../clauses`、`POST .../publish` |
| 已发布 Policy | `GET /api/v1/policy-sets` |

后端错误统一转换为 `ApiClientError`，页面展示安全消息和可用的 Request ID，而不是输出内部堆栈或密钥。

### 3.2 两阶段报价门禁

正式报价不会在文件上传时立即创建。草稿、审核和正式提交分开，解决了“字段尚未确认但任务 revision 已经推进”的问题。

```text
文件上传
  → QuoteDraft + DRAFT_REVIEW Job
  → 解析字段与证据
  → 确定性审核
  → 人工只修正阻塞字段
  → READY_TO_SUBMIT
  → 原子创建正式 Quote/Document
  → Task Revision + 1
```

草稿保存基础 Task Revision 和独立 Draft Revision。任务在审核期间发生变化时，旧草稿进入 `STALE`，前端不会把旧审核结果静默套用到新任务版本。

### 3.3 版本、并发与幂等

所有改变权威状态的请求继续使用 `Idempotency-Key`：

- 网络失败后重试同一请求时复用原 Key 和原请求体。
- 用户改变内容后生成新的 Key。
- 草稿修正携带 `expected_draft_revision`。
- 正式提交同时携带 `expected_task_revision` 和 `expected_draft_revision`。
- Policy 条款保存和发布携带 `expected_revision`。

这样可以防止双击、网络重放和旧页面覆盖新版本。

### 3.4 数据读取与刷新

前端使用 React Query 管理服务器状态：

- API 数据按任务、报价、草稿、Policy 和历史记录设置独立 query key。
- 上传、修正、提交和发布成功后主动使相关缓存失效。
- 任务、草稿和运行中的 Job 使用有限频率轮询。
- 浏览器刷新后以 URL 中的 Task ID、Result ID 或 Policy Import ID 从后端恢复，不依赖 `localStorage` 假数据。

### 3.5 展示边界

前端只负责展示、输入收集和调用接口：

- 模型负责报价和制度文本理解。
- 确定性后端代码负责金额、数量、硬约束、状态和排序。
- 人工负责关键纠正和最终业务决策。
- 前端不持有模型密钥，不从浏览器直接调用模型，不把未知金额显示为 0。

## 4. 使用方法与交付物

### 4.1 首次准备

需要 Docker Desktop、Python 3、Node.js 22，以及项目根目录中有效的 `.env`。完整命令以仓库根目录的 `startup.md` 为准。

前端依赖：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM/frontend
npm ci
```

Python、数据库和迁移：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
docker compose up -d --wait postgres
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m alembic upgrade head
.venv/bin/python -m dotenv -f .env run -- .venv/bin/python -m supplier_comparison.checkpoints setup
```

拉取包含新迁移的代码后，至少需要重新执行 `alembic upgrade head`。如果 API、Worker 或前端代码有变化，应停止旧进程并重新启动对应服务。

### 4.2 每次启动

先启动 Docker Desktop 和 PostgreSQL：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
docker compose up -d --wait postgres
```

终端 1，启动 API：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m uvicorn supplier_comparison.backend.api:app \
  --host 127.0.0.1 --port 8000
```

终端 2，启动持续 Worker。报价草稿审核、决策运行和 Policy 发布都依赖 Worker，不能省略：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m supplier_comparison.worker run-loop \
  --poll-interval 1
```

终端 3，启动前端：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM/frontend
npm run dev -- --host 127.0.0.1
```

打开：

- 前端：<http://127.0.0.1:5173>
- 后端健康检查：<http://127.0.0.1:8000/health/ready>

### 4.3 推荐验收流程

```text
创建任务
  → 可选绑定已发布 Policy
  → 上传供应商报价草稿
  → 等待 Worker 解析
  → 核对并修正阻塞字段
  → 正式提交报价
  → 开始决策分析
  → 查看比较结果与证据
  → 查看合规检索与版本审计
  → 生成或查看当前版本 Summary
```

如需测试真实模型，必须使用加载 `.env` 的 Worker。自动化测试中的固定适配器结果只能用于可重复测试，不能当作真实模型效果。

### 4.4 主要交付文件

前端入口与框架：

| 文件 | 用途 |
| --- | --- |
| `frontend/src/App.tsx` | 路由和旧审核地址重定向 |
| `frontend/src/components/AppShell.tsx` | 侧栏、最近任务和全局布局 |
| `frontend/src/components/TaskWorkspaceHeader.tsx` | 四阶段时间线和任务标签 |
| `frontend/src/api/client.ts` | 真实 API 请求、错误和幂等键 |
| `frontend/src/api/types.ts` | 前后端共享响应形状的 TypeScript 类型 |
| `frontend/src/styles/blueprint.css` | 当前工作台视觉样式和响应式布局 |

主要页面：

| 文件 | 用途 |
| --- | --- |
| `frontend/src/pages/OverviewPage.tsx` | 任务中心、搜索、筛选和排序 |
| `frontend/src/pages/NewTaskPage.tsx` | 采购需求与可选 Policy Binding |
| `frontend/src/pages/TaskPage.tsx` | 任务概览和需求快照 |
| `frontend/src/pages/QuoteUploadPage.tsx` | 报价草稿上传、审核、修正、提交和历史 |
| `frontend/src/pages/DecisionPage.tsx` | 决策运行入口和状态分流 |
| `frontend/src/pages/ResultPage.tsx` | 比较矩阵、推荐、风险和证据 |
| `frontend/src/pages/CompliancePage.tsx` | 制度检索状态、引用和复核入口 |
| `frontend/src/pages/AuditPage.tsx` | 报价、问题和结果历史 |
| `frontend/src/pages/SummaryPage.tsx` | 版本化 AI Summary 生成、轮询、失败重试和历史查看 |
| `frontend/src/pages/ResourcePage.tsx` | Policy 上传、导入目录和已发布目录 |
| `frontend/src/pages/PolicyImportPage.tsx` | 条款审核与发布 |

报价草稿的最小后端支撑：

| 文件 | 用途 |
| --- | --- |
| `src/supplier_comparison/backend/api.py` | QuoteDraft REST 接口 |
| `src/supplier_comparison/backend/models.py` | QuoteDraft 持久化模型 |
| `src/supplier_comparison/backend/service.py` | 草稿状态、修正、提交和幂等逻辑 |
| `src/supplier_comparison/backend/workflow.py` | 草稿审核复用与正式决策衔接 |
| `src/supplier_comparison/worker.py` | `DRAFT_REVIEW` 作业执行 |
| `migrations/versions/a4c7e2f91b03_add_quote_draft_review_flow.py` | QuoteDraft 数据库迁移 |
| `tests/backend/test_quote_drafts.py` | 草稿流程后端测试 |

### 4.5 验证结果

本轮最近一次验证结果：

| 验证项 | 结果 |
| --- | --- |
| `npm run build` | 通过 |
| `npm run lint` | 通过 |
| 全仓库 Python 测试 | `476 passed, 5 skipped` |
| PostgreSQL 条件集成测试 | `5 passed` |
| Alembic 升级检查 | 无待生成迁移操作 |
| 浏览器页面检查 | 报价审核、决策和 Summary 页面布局正常 |

上述自动化测试使用固定模型适配器，不等同于真实外部模型验收。真实模型调用仍需在正确 `.env`、网络和模型额度条件下单独验证。

## 5. 下游说明

### 5.1 原占位能力现状

以下能力已经由后端契约和持久化实现，前端不再使用模拟数据：

| 功能 | 当前状态 |
| --- | --- |
| 采购需求文件解析 | PDF/TXT/MD 异步草稿、来源证据、模型元数据及创建任务绑定 |
| 修改采购需求 | 带 `expected_task_revision`，旧执行失效；有正式报价时自动全量重算 |
| 废弃任务 | 不可恢复软废弃，保留历史并拒绝后续写操作 |
| 历史报价原件预览/下载 | 权限校验文件流、正确 Content-Type/ETag 及访问审计 |
| AI Summary | 版本化异步作业、确定性事实骨架、受约束模型文本及失败重试 |
| 任务中心全量检索 | 服务端搜索、状态筛选、排序、分页及全量状态计数 |

Summary 后端完成时，报告必须绑定明确的 `task_revision`、`result_id`、规则版本和 Policy Index Version；旧结果的报告不能覆盖当前版本，也不能把模型文本当作审批结论。

### 5.2 后端已有但前端未完整展开的能力

- 后端提供单个草稿详情接口；当前页面以草稿列表恢复活动草稿，尚未单独提供草稿历史详情路由。
- 后端保存全部终态草稿；当前页面主要聚焦活动草稿和正式报价，没有制作完整的草稿审计列表。
- 后端仍保留单字段正式报价修正接口供兼容；当前前端使用批量修正，以便一次处理全部阻塞字段。
- 旧正式报价直传接口只为自动测试兼容而保留，生产默认关闭；前端不会暴露绕过草稿审核的入口。

这些是有意的产品取舍，不应为了“把所有接口都放到页面上”而破坏正式提交门禁。

### 5.3 下游开发必须保持的不变量

- 后端契约和枚举是唯一事实来源，前端不能另造状态。
- 草稿上传不推进 Task Revision；只有正式提交成功才推进一次。
- 阻塞未解除、Task Revision 过期、Draft Revision 过期或草稿为 `STALE` 时都不能正式提交。
- 所有写操作保持幂等，相同请求重试复用原 Key；内容变化后使用新 Key。
- Policy Binding 创建后不可在原任务上修改；如制度或索引版本变化，应创建新任务或按后端定义的新版本流程处理。
- Policy 检索只证明制度证据是否找到，不等于合规审批。
- 前端不得计算总成本、可行性或最终排序，也不得把 `UNKNOWN` 金额转换为 0。
- 模型密钥只存在于后端环境；不能进入前端构建产物、日志或提交记录。
- 报价、Policy 和结果必须继续保留来源、哈希、revision 和版本信息，不能只保存页面展示文本。

### 5.4 后续验证重点

1. 分别记录固定模型与真实模型的需求解析、Summary 结果和调用次数。
2. 在部署环境复核文件流认证策略；当前本地身份仍由后端测试用户注入。
3. 用 PostgreSQL 重启恢复验证需求草稿和 Summary Job 的持久化状态。

下游接手前应先执行数据库迁移、前端 build/lint、QuoteDraft 测试和 PostgreSQL 集成测试，再用一份明确标记为合成数据的 PDF/CSV 完成浏览器全流程。真实模型与固定适配器的结果必须分别记录。
