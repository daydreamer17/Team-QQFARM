# 第 5–6 部分：入选差距与制度调查

本轮仅修改 Team-QQFARM，未修改 A/B，未提交或推送 GitHub。复用现有规则、LangGraph、pgvector 检索器和 WorkflowArtifact，不增加数据库表，不改变正式需求、报价或审批权限。

## 1. 入选差距是什么

例：需求最晚 9/19 到货；A 已确认可行，S$7,100；B 为 S$7,000，但预计 9/21 到货。

程序返回 B 超期 2 天、成本低 S$100；仅假设 B 能在 9/19 前到货，价格、运费、MOQ、有效期及其他报价不变且无加急费，重算后 B 才是当前报价比较优选。供应商尚未承诺，不代表正式入选或制度审批通过。

若 B 同时运费未知、超预算、规格不符等，全部列出；只改交期不能消除其他阻塞项。未知成本不填零，也不计算虚假的价格差；成本持平可能是并列，不是唯一推荐。当前支持最低已确认总成本与日历日到货语义，不支持全球最小改动优化或未知加急价格预测。

核心返回：`failed_reasons`、`pending_reasons`、`comparison_reasons`、`budget_excess`、`cost_difference_vs_other`、`delivery_days_late`、`target_lead_time_days` 和带明确假设的 `delivery_improvement`。每份试算外层均有 `hypothetical=true`、`formal_recommendation_allowed=false`、`policy_assessment_performed=false`；内部比较许可仅适用于假设下的报价比较，不能直接发布。

### 查看与试算

先执行已有提取/审核工作流，使用当前 revision；未审核、文件身份不匹配或不安全审查问题会拒绝。合法未知费用与程序发现的待确认项可以展示，但不会被修成事实。

```text
GET /api/v1/tasks/{task_id}/selection-gaps?expected_task_revision=4
POST /api/v1/tasks/{task_id}/requirement-simulations
```

POST 示例，只有用户明确授权的预算/截止日假设可进入：

```json
{
  "expected_task_revision": 4,
  "confirm_hypothetical": true,
  "changes": {"delivery_deadline": "2026-09-21"}
}
```

两接口沿用任务所有者权限，不增加 revision，不改 current_result，不启动正式采购。正式改变需求或报价须走既有版本化更新与重新审核流程。沟通草稿随差距响应返回，`draft_only=true`、`sent=false`，不会自动联系供应商。

Agent 可选择 `analyze_selection_gap`、`draft_clarification`。`simulate_requirement_change` 默认不开放；只有服务器预先注入授权参数才加入白名单，模型不能自行扩大授权。目前 POST 可直接试算，前端聊天及授权接入尚未实现。

## 2. 制度调查怎么工作

原必查范围仍为批准供应商、RoHS 和金额审批。任一条款检索异常时，启用的 Agent 可以读程序诊断、在允许时重试，或请求处理；只有权威检索结果通过版本/控制项/引用检查才解除检索暂停。

| 状态 | 程序处理 |
| --- | --- |
| `NO_EVIDENCE` | 提示核对适用范围与条款；不自动重试，不编造制度 |
| `CONFLICT` | 制度管理员修订、审核发布新版本并创建新任务；不换问题绕过冲突 |
| `ERROR` 且有明确网络、429 或可重试 5xx 依据 | 允许同查询、同快照、同版本的有限自动重试 |
| 配置、认证、索引不匹配、返回契约错误或未知故障 | 需要系统修复，不自动循环 |
| 仍失败、模型故障或预算耗尽 | 保持 `POLICY_EVIDENCE_REVIEW` 暂停，集中展示诊断与下一步 |

工具为 `get_policy_retrieval_status`、`retry_policy_retrieval`、`request_clarification` 及只读上下文。无 query/版本/路径/SQL 参数；报价字段问题和制度问题使用不同卡片，不要求用户修改运费来修制度。

```dotenv
SUPPLIER_AGENT_ENABLED=true
SUPPLIER_AGENT_POLICY_MAX_RETRIES=2
```

真实 Agent 复用原模型配置和 API key；固定测试无需 key。自动重试在整个 graph 内默认最多预留 2 次，可设置 0–3；每次请求前写入 `POLICY_RETRY_ATTEMPT`，失败或重放也占次数。该上限不替代底层 embedding/rerank 已有 HTTP 请求重试策略。

`GET /api/v1/tasks/{task_id}/investigations` 可查看 `kind=QUOTE/POLICY`、工具、真实观察、来源、调用次数与停止原因。`EVIDENCE_CONFIRMED` 只说明制度检索恢复；制度文件不是供应商资质证据，金额审批和正式采购批准仍必须另行满足。

无需新数据库迁移。已有运行中的旧 checkpoint 不建议直接切换图结构，应完成旧任务或启动新 START 任务。

## 3. 验证

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/rules/test_selection_gap.py tests/backend/test_investigation.py tests/backend/test_policy_investigation.py tests/backend/test_api.py tests/rag/test_retriever.py
.\.venv\Scripts\python.exe -m pytest -q
```

覆盖交期改善后条件优选、复合阻塞、未知运费、并列、全部不可行、不支持语义、授权和只读边界、版本与所有者隔离；以及临时故障恢复、不可重试异常、持久化次数上限、重放、越权参数拒绝和发布暂停。测试采用 SQLite/内存 checkpoint 与固定模型、固定检索器；不能替代真实模型效果或 PostgreSQL 专项验收。

2026-09-18 最初实现回归：`586 passed, 5 skipped`；当时未执行真实 LLM 与 PostgreSQL 专项。后续补验见下节，不能把这份历史结果当作当前仍未验证。

## 4. 真实模型与 PostgreSQL 补验

真实 Agent 使用本地配置的 `deepseek-ai/DeepSeek-V4-Flash`，不是固定模型回答。报价事实采用合成 canonical CSV，制度结果及临时故障采用可控测试夹具；这是工具选择与调查闭环验收，不是原始 PDF 提取准确率或外部 embedding/rerank 网络验收。

| 场景 | 已观察到的行为 |
| --- | --- |
| 差距分析和沟通草稿 | `analyze_selection_gap → draft_clarification → request_clarification`，读取未知运费后暂停，不虚构总价 |
| 用户授权预算试算 | `simulate_requirement_change → request_clarification`，正式需求/结果不变 |
| 制度检索临时故障 | 选择 `retry_policy_retrieval`，同版本返回有效引用后程序结束调查 |
| 必查条款缺失 | 读取诊断或直接停止，集中展示制度处理卡片，不自动重试、不发布 |
| 制度冲突 | `WAITING_INPUT / CONFLICT_UNRESOLVED`，不切换版本或判通过 |
| 真实 LLM + PostgreSQL | 跨连接仍能读取成功重试、引用及调查记录，当前结果持久化 |

联调曾发现模型忽略用户请求、重复读取以及耗尽调用预算，因此调整了提示中的业务目标优先级，公开服务器已注入的授权参数和响应 schema，并每轮移除已完成的无参数只读工具；程序的重复拒绝、授权、版本及预算门禁未放宽。固定回归覆盖动态工具清单及授权上下文。真实样例通过不保证模型每次都最优；无效返回、越权、模型故障或预算耗尽仍按既有机制暂停。

PostgreSQL 16.15 使用独立临时测试库，运行现有 Alembic 迁移与 PostgresSaver setup，未迁移/清空业务库。专项验证覆盖 pgvector、跨连接 checkpoint 恢复、同 revision 上传并发、制度引用持久化及新增的调查恢复/自动重试上限。8 个并发预留请求只获准 2 次；重开连接及人工恢复后自动重试计数不归零。

2026-09-18 最终全仓库验证同时启用 `RUN_POSTGRES_TESTS=1` 和 `RUN_AGENT_LIVE_TESTS=1`：`599 passed in 54.25s`，无失败、无跳过。包含 5 个真实模型场景、1 个真实模型与 PostgreSQL 组合场景，以及数据库恢复和并发次数限制回归。验证后已删除本次创建的临时测试数据库，正式业务数据库未被修改；未提交或推送 GitHub。

### 复验指令

先配置一个**专用测试数据库**并迁移、初始化 checkpoint，再在当前 PowerShell 设置其连接串；不要为了测试清空正式库。两个 URL 应指向同一个测试库，连接串和 API key 不提交。

```powershell
$env:TEST_DATABASE_URL = "填写专用测试库连接串"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
$env:RUN_POSTGRES_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests/backend/test_postgres_recovery.py tests/rag/test_postgres_retrieval.py -vv

# 以下真实模型测试会调用付费 API；dotenv 仅加载本地配置。
$env:RUN_AGENT_LIVE_TESTS = "1"
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe -m pytest tests/backend/test_investigation.py -k test_live_agent -vv -s
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe -m pytest tests/backend/test_postgres_recovery.py -k test_postgres_live_agent -vv -s
```

不启用 `RUN_AGENT_LIVE_TESTS=1` 时，付费验收默认跳过；不启用 `RUN_POSTGRES_TESTS=1` 时，数据库专项默认跳过。不要将带 `SKIPPED` 的普通回归等同于真实联调完成。测试中的实际故障注入和恢复不会故意中断正常业务数据库或第三方服务。
