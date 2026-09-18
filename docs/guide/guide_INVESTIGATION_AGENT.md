# 受约束自主调查 Agent：开启与验证

已完成第 4–6 部分的后端第一版：真实 LLM 选择工具、读取观察后调整下一步、持久化调查记录、集中补问、入选差距工具及制度故障调查。默认关闭，开启后用于**审核后的字段疑点查证与制度检索异常调查**。不是自动采购，也不是已完成的前端聊天。

## 1. 流程与权限

```text
提取 → 全量确定性审核 → 程序决策影响分析
  → Agent 调查真正影响决策的疑点
      选择工具 → 实际返回 → 调整简短计划 → 再查或停止
  ├─ 程序证明未知不影响推荐 → 保留未知，不补问
  └─ 未解决、冲突、模型不可用或预算耗尽 → 一次集中核对与字段纠正
      → 新任务修订 → 全量重审与重算
  → 原比较、制度检索与发布门禁
```

模型只决定下一项调查动作。成本、状态、排序、无影响证明、权限和版本由程序负责。即使原文出现金额，也不会自动写回；用户核对后通过批量纠正接口提交。`STOP` 不等于已解决，原文写“不含运费”也不等于金额已知。

非费用冲突或关键字段错误没有安全影响证明时，以 `UNDETERMINED` 调查/请求核对，不能凭调查解除门禁。文件身份不匹配仍立即拒绝，不交给模型“修好”。已有超预算、超期等业务不符合不是字段填写错误。

## 2. 现在可自主选择的工具

| 工具 | 返回什么 |
| --- | --- |
| `get_task_context` | 当前需求、制度绑定与允许的工具 |
| `analyze_decision_impact` | 当前程序影响报告；没有可靠报告时明确未找到 |
| `get_comparison_result` | 本轮程序比较结果，可能尚不可发布 |
| `get_cost_breakdown` | 程序成本明细和已知小计，不猜测未知费用 |
| `locate_quote_source` | 当前报价字段引用，或有限的整文件文本预览与位置 |
| `get_confirmed_quote_records` | 当前任务、报价、文件仍适用的真实人工纠正记录；没有就返回未找到 |
| `request_clarification` | 本次需核对的字段卡片，不写回业务事实 |
| `retrieve_policy` | 完整绑定且已配置检索器时，读取冻结制度并返回引用 |
| `analyze_selection_gap` | 程序计算全部阻塞项、成本/交期差距与条件试算 |
| `draft_clarification` | 未发送的供应商沟通草稿，不改报价或需求 |
| `simulate_requirement_change` | 仅服务器预先注入用户授权参数时开放；模型不能自行填写预算或截止日 |

制度异常调查使用单独白名单：`get_task_context`、`get_policy_retrieval_status`、`retry_policy_retrieval`、`request_clarification`。不接受模型传入 query、制度版本或任务 ID；重试复用原必查查询与冻结版本。制度卡片不是报价字段纠正卡片。

不允许任意 SQL、文件路径、外部网址或其他任务 ID。每次调用前后检查输入是否仍然适用；非法工具/参数和同一来源的等价重复调用会被拒绝并记录。工具调用顺序不是固定的，也不要求展示所有工具。

原文预览最多 40 个来源/12,000 字符，单来源最多 2,000 字符；截断会明确标记，预览不能替代人工确认“整份文件确实缺失”。OCR 仍按现有实验设置处理，界面不需要放图片，按文件名、页码/行列与引用核对。

制度工具属于探索性读取，**不会满足或替换固定三类必查门禁**。知识库范围沿用当前任务版本绑定；这不代表组织级知识库隔离或供应商资质真实性核验已经完成。

## 3. 开启真实模型

在本地 `.env` 设置（本轮不修改你的真实 `.env`）：

```dotenv
SUPPLIER_AGENT_ENABLED=true
SUPPLIER_AGENT_TIMEOUT_SECONDS=30
SUPPLIER_AGENT_MAX_TOKENS=1024
SUPPLIER_AGENT_MAX_MODEL_CALLS=8
SUPPLIER_AGENT_MAX_TOOL_CALLS=10
SUPPLIER_AGENT_MAX_SECONDS=90
SUPPLIER_AGENT_POLICY_MAX_RETRIES=2
```

默认复用 `SUPPLIER_MODEL_MODEL_ID`、`SUPPLIER_MODEL_BASE_URL`、`SUPPLIER_MODEL_API_KEY_ENV`。也可用 `SUPPLIER_AGENT_MODEL_ID`、`SUPPLIER_AGENT_BASE_URL`、`SUPPLIER_AGENT_API_KEY_ENV` 单独配置。Key 的值只放在本地对应环境变量，不上传 GitHub。端点必须为 HTTPS 或本地 HTTP，不允许 URL 嵌入账号/密码。

需要 API key；固定测试不需要。没有配置或模型故障不能声称调查成功。HTTP 不自动重试，每次请求都有超时及输出 token 上限；输入上下文超过 48,000 字符会停止。

调用、工具及总耗时预算在同一阶段各调查间共享：报价调查与制度调查分别执行，已记录次数和开始时间不会因重放归零。自动制度重试默认整个 graph 最多预留 2 次，先持久化预留再请求，跨重放和人工恢复不归零；这不是底层 HTTP 请求次数，embedding/rerank 原有单次请求重试仍独立生效。外部 RAG 调用仍遵守现有单次超时；总耗时在调用边界检查，不是强制中断正在执行的数据库/网络调用。超过预算后不会再启动下一次调用或发布未知结论。

模型每轮获得仍适用的工具清单：已完成的无参数只读操作不重复提供；制度状态变化后可再次读取新状态，只有明确临时故障才提供自动重试。服务器注入授权参数并公开范围，模型不填写或扩大参数。提示优先完成用户明确请求的分析/试算，而非强制先反复读取原文；模型不遵守时仍由程序拒绝或停止。

开启后启动新 START 任务，按既有 worker 指令运行：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe -m supplier_comparison.worker `
  run-job --job-id "替换为真实job_id"
```

返回 `WAITING_INPUT` 且 `issue_type=BATCH_FIELD_REVIEW` 时，按 `answer_schema.cards` 一次核对，再向 `answer_schema.submit_to` 批量提交字段纠正。运费状态与金额会一起列出；不能向旧 issue/answers 接口伪造确认。批量纠正返回新 START job，原调查与 issue 失效，重新审核后才能比较/发布。

无需新增数据库表，记录复用 `WorkflowArtifact`。真实 PostgreSQL 环境沿用当前迁移和 checkpoint 配置，不因本轮固定测试通过就认为数据库已经验证。不要在进行中的旧 checkpoint 上任意切换图/模式，先完成旧任务或启动新任务。

## 4. 查看调查记录

`GET /api/v1/tasks/{task_id}/investigations`

每个 `case_id` 返回最新快照，包括目标、任务/报价版本、影响证明哈希或明确无证明、制度绑定、短计划、`observations`、调用次数、状态及停止原因。每次观察含真实工具名、请求参数、状态、数据、来源和耗时；不展示模型内部思维链。

- `RESOLVED / NO_DECISION_IMPACT`：程序证明该未知不影响当前决策，不代表字段已填好。
- `kind=POLICY` 且 `RESOLVED / EVIDENCE_CONFIRMED`：程序验证当前必查条款、版本和引用匹配；不代表供应商资质核验、金额审批或采购批准完成。
- `WAITING_INPUT`：证据不足或冲突，需要确认；没有最终推荐许可。
- `LIMIT_REACHED / BUDGET_EXHAUSTED`：本轮达到预算，问题仍未解决。
- `STALE / INPUT_CHANGED`：旧记录不再适用于当前输入；原记录仍保留，`stored_status` 展示历史实际状态。

为了不虚构“所有来源已经查过”，实现协议补充 `EVIDENCE_INSUFFICIENT`、`MODEL_UNAVAILABLE`；只有相关报价来源和当前确认记录均已查到终态，才记录 `SOURCES_EXHAUSTED`。等待和耗尽都不会被包装成成功。

前端可先展示“目标 → 计划 → 工具名/状态/引用 → 待核对卡片 → 统一提交”，但本轮没有实现页面或时间线组件。所有读取接口沿用服务器任务所有者授权；当前测试身份不等于完整登录系统。

## 5. 验证与剩余边界

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/backend/test_investigation.py tests/backend/test_api.py tests/backend/test_workflow.py tests/backend/test_workflow_policy_rag.py
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖不同工具顺序、真实调用记录、无影响未知无需调用模型、多报价集中补问、人工纠正重算、历史失效、模型非法返回/故障、重复与越权调用、输入变化、共享调用/时间预算及制度门禁。真实模型冒烟采用合成演示数据、SQLite 和内存 checkpoint，数据放在系统临时目录，不证明真实 PostgreSQL 或正式供应商资料核验通过。

第 5–6 部分的接口、示例与验证见 [入选差距与制度调查](guide_SELECTION_GAP_POLICY.md)。完整输入可直接使用分析 API，不强制为每家已知不合格报价启动 LLM 调查。

尚未实现：结果自然语言提问入口、前端调查页面、聊天授权参数接入、完整供应商资质数据源与业务核验、正式登录和组织级知识库隔离。原 `POLICY_EVIDENCE_REVIEW / RETRY_POLICY_RETRIEVAL` 恢复机制继续保留，修订制度或索引仍需新版本新任务。本轮仍复用固定三类必查条款，没有实现动态制度目录。
