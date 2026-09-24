# Agent 联调变更与验证指南

更新时间：2026-09-18。

## 1. 本次改了什么

本次是在已实现的受约束调查 Agent 上补齐真实模型联调与 PostgreSQL 专项验证，不是新增自由聊天入口。

| 变更 | 解决的问题 |
| --- | --- |
| 明确业务目标优先级 | 用户要求分析差距、生成草稿或授权试算时，Agent 优先完成目标，不强制反复查原文 |
| 每轮提供仍适用的工具清单 | 已完成的无参数读取、分析不重复提供；制度状态变化后可读取新状态 |
| 显示服务器注入的授权试算参数 | 模型知道可以试算什么，但不能自行填写或扩大预算、截止日期 |
| 补充响应格式和已完成操作记录 | 减少无效返回、重复调用及无意义的调用预算消耗 |
| 增加真实模型与数据库回归测试 | 验证工具选择、观察结果、停止条件，以及跨连接恢复和重试次数限制 |

权限未放宽：成本、可行性、排序和发布许可仍由程序判断。Agent 不修改正式需求、报价或审批，不自动发送沟通草稿。

## 2. Agent 现在能做什么

| 工具 | 用途 |
| --- | --- |
| `analyze_selection_gap` | 分析供应商未入选的阻塞项、成本和交期差距 |
| `draft_clarification` | 生成未发送的供应商沟通草稿 |
| `simulate_requirement_change` | 在用户明确授权的假设下试算，不修改正式需求 |
| `get_policy_retrieval_status` | 读取制度检索缺失、冲突或故障诊断 |
| `retry_policy_retrieval` | 仅在明确临时故障且次数允许时，重试同版本制度检索 |
| `request_clarification` | 汇总仍需人工处理的问题并暂停 |

Agent 根据目标和实际工具结果选择下一步，并非每次都调用全部工具。报价原文、成本明细和任务上下文等既有只读工具继续保留。

## 3. 已完成的验证

### 真实模型

使用 `deepseek-ai/DeepSeek-V4-Flash`。开发联调及用户随后运行的 5 个场景均通过：

| 场景 | 用户本次输出中的行为 | 正确结果 |
| --- | --- | --- |
| `gap_and_draft` | 差距分析 → 沟通草稿 → 停止 | 运费未知，等待补充 |
| `authorized_simulation` | 授权预算 S$9,000 试算 → 停止 | 不改变正式需求；信息不足仍等待 |
| `policy_recovery` | 重试制度检索 | 获取有效证据后解决调查 |
| `policy_missing` | 读取诊断 → 读取上下文 → 请求人工处理 | 不盲目重试、不编造制度 |
| `policy_conflict` | 读取诊断 → 停止 | 不绕过冲突或擅自判通过 |

`WAITING_INPUT` 不是测试失败，而是正确等待人工处理。`RESOLVED / EVIDENCE_CONFIRMED` 仅说明本次制度调查已解决，不等于供应商资质或采购审批通过。

### PostgreSQL

使用 PostgreSQL 16.15 的独立临时测试库完成验证：

- 调查记录、工具观察、制度引用和结果能够持久化。
- 重开数据库连接后，checkpoint 能继续恢复；人工恢复后重试次数不归零。
- 8 个并发自动重试预留申请，仅 2 个获准；超出上限的申请被拒绝。
- 真实模型与 PostgreSQL 组合场景通过。

最终全仓库验证：`599 passed in 54.25s`，0 失败、0 跳过。临时测试库已删除，正式业务数据库未修改。

## 4. 最重要的验证指令

### A. 只测试 Agent 能力

不需要 PostgreSQL；需要 `.env` 中的模型配置和 API Key，会产生模型 API 费用。

```powershell
cd E:\iss_hackathon\Team-QQFARM
$env:RUN_AGENT_LIVE_TESTS = "1"

.\.venv\Scripts\python.exe -m dotenv -f .env run --no-override -- .\.venv\Scripts\python.exe -m pytest tests/backend/test_investigation.py -k test_live_agent -vv -s
```

预期：`5 passed`，其余测试显示 `deselected` 属于正常筛选。重点看 `tools`（实际工具）、`observations`（返回状态和公开行动说明）、`model_calls`（模型调用次数）、`status` 和 `stop_reason`。调用顺序和次数允许合理变化，不要求逐字复现。

### B. 数据库持久化、恢复与重试上限

先准备已完成现有 Alembic 迁移和 checkpoint 初始化的专用测试库；不要直接用正式业务库。两个 URL 必须指向同一测试库，连接串不要提交。

```powershell
$env:TEST_DATABASE_URL = "填写专用测试库连接串"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
$env:RUN_POSTGRES_TESTS = "1"
$env:RUN_AGENT_LIVE_TESTS = "1"

.\.venv\Scripts\python.exe -m dotenv -f .env run --no-override -- .\.venv\Scripts\python.exe -m pytest tests/backend/test_postgres_recovery.py tests/rag/test_postgres_retrieval.py -vv -s
```

预期：`8 passed`，包含真实模型与 PostgreSQL 组合验证。`--no-override` 确保 `.env` 不覆盖终端指定的测试库连接串。

### C. 全仓库最终回归

保持 B 中的测试库配置和两个开关：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run --no-override -- .\.venv\Scripts\python.exe -m pytest -q
```

当前版本预期：`599 passed`，无失败、无跳过。此命令也会重新调用真实模型。未开启专项开关时，相关测试默认跳过，不能据此宣称真实联调完成。

如上述变量仅为本次验证临时设置，完成后可清除：

```powershell
Remove-Item Env:RUN_AGENT_LIVE_TESTS, Env:RUN_POSTGRES_TESTS, Env:TEST_DATABASE_URL, Env:DATABASE_URL -ErrorAction SilentlyContinue
```

## 5. 重要文件与边界

| 文件 | 本次涉及的内容 |
| --- | --- |
| `src/supplier_comparison/backend/investigation.py` | 模型提示、上下文及每轮工具选择 |
| `src/supplier_comparison/backend/investigation_tools.py` | 业务工具清单、授权参数和完成操作过滤 |
| `src/supplier_comparison/backend/policy_investigation.py` | 制度工具清单与重试资格 |
| `tests/backend/test_investigation.py` | 5 个真实模型场景和固定回归 |
| `tests/backend/test_postgres_recovery.py` | 持久化、恢复、并发次数限制及真实模型组合验证 |
| `tests/rag/test_postgres_retrieval.py` | PostgreSQL/pgvector 检索回归 |

本次使用真实 LLM，但报价采用合成结构化数据，制度结果和故障采用可控测试夹具；不代表原始 PDF 提取准确率或外部 embedding/rerank 服务效果已验收。通过这些场景不保证模型在所有业务场景中都正确。

前端现已提供受控的“智能调查”入口和公开工具轨迹，但没有开放式聊天，也不会展示模型内部思维链。8 个隔离验收场景及参考预期分别位于 `data/generated/inputs/development/agent_investigation_demo` 与 `evaluation/reference/agent_investigation_demo`。完整功能说明见 [调查 Agent 指南](guide_INVESTIGATION_AGENT.md) 和 [入选差距与制度调查指南](guide_SELECTION_GAP_POLICY.md)。
