# 决策影响分析：第 1、2 步交付说明

实现基线：main `0d248d2` + 本地扩展。规则版本为 `supplier-comparison/1.1.0`，影响接口版本为 `decision-impact/1.0.0`。

## 1. 已完成与边界

已完成：明确接口与调查协议基线；复用并收紧比较引擎的成本下界判断；在 LangGraph 审核后、人工补问前执行影响分析；将报告保存为不可变 artifact 并关联比较快照。

当前实现是确定性的影响分析工具，不调用 LLM。自主调查执行器、批量问题回答、前端调查时间线和入选差距工具属于后续任务。

## 2. 输入、输出与工具接口

纯工具入口：`analyze_decision_impact(DecisionImpactRequest) -> DecisionImpactResult`。

审核入口：`analyze_reviewed_decision_impact(requirement, envelopes, task_id, task_revision, evaluated_at, policy_binding)`；只有此入口通过审核边界后，才用于正式工作流。

| 输入 | 含义 |
| --- | --- |
| `task_id`、`task_revision` | 当前任务与输入修订 |
| `comparison` | 采购需求、报价候选、带时区的评估时间 |
| `policy_binding` | 冻结的制度集合、索引、类别与地区；用于绑定证明，不在这里执行制度审核 |
| `review_bindings` | 审核入口填入报价文件哈希与审核记录标识 |

输出包含比较结果、逐报价影响、阻塞报价 ID、无需主动补问的未知报价 ID，以及 `input_sha256`。哈希绑定需求、报价与字段版本、评估时间、任务修订、审核来源和制度绑定。输入变化后重新分析，不复用旧证明。

逐报价字段：`status`、`reason_code`、`message`、`unknown_fields`、`cost_lower_bound`、`best_confirmed_cost`、`additional_cost_to_tie`、`assumptions`。金额使用 Decimal，JSON 序列化为字符串；无法证明的下界与阈值为 `null`。

| `status` | 含义 | 流程行动 |
| --- | --- | --- |
| `NO_ISSUE` | 报价已确认可行，无未解决事实 | 正常比较 |
| `REQUIRES_INVESTIGATION` | 合法未知费用可能影响排名/并列，或没有可行基准 | 保持阻塞；支持的缺失运费进入现有确认流程 |
| `NON_BLOCKING` | 成本下界已严格高于可行基准，或已有确定的不可行原因 | 保留未知；不主动补问，不将该报价视为已确认可行 |
| `UNDETERMINED` | 币种、计价、税务、非费用事实或排序规则等未解决 | 保守处理；不能凭小计较高宣布无影响 |

`NON_BLOCKING` 是报价比较范围的结论，不替代必要审核与制度发布门禁。候选集合并列时保留并列，不能随意选一个赢家。

## 3. 判断依据与例子

第一版只在“最低已确认总成本”、相同已确认币种与计价口径、税务为 `NOT_APPLICABLE`、仅非负费用未知的模型范围内证明成本支配。不解释未建模的折扣，也不支持跨币种换算或新的排序偏好。

假设已有可行供应商总成本 10,000：

- 另一家小计 9,800、运费未知：需调查；额外费用为 200 时形成并列，低于 200 时可能更便宜。
- 另一家小计 10,000、运费未知：也需调查，因为零运费可能形成并列。
- 另一家小计 15,000、运费未知：非阻塞；总成本仍未知，不填 0。
- 没有已确认可行供应商：不能用“看起来很贵”跳过潜在候选。
- 小计较高但币种、计价单位或税务不一致：不能使用该小计作为安全下界。

## 4. 审核与工作流边界

原 `quote_input_from_reviewed_extraction` 仍严格要求 `downstream_ready`，不修改 B 的审核契约。

新增 `quote_input_for_decision_impact` 仅允许一种初步比较例外：完整审核已完成，身份、证据、类型、跨字段等检查通过，唯一未解决的阻塞是费用字段上的 `CRITICAL_FIELD_MISSING` 或 `FEE_STATUS_UNKNOWN`。错误、冲突、拒绝及模型失败不能使用这个例外。

例外入口不会改变原 `ReviewEnvelope`，不会创建虚假的人工事件，也不会把缺失字段标成 `VERIFIED`。未选报价可能仍为 `REVIEW_REQUIRED`，但报告明确记录其未知为何不阻塞任务层推荐。

现有工作流变为：

```text
提取 → 全量确定性审核 → 决策影响分析
  ├─ 全部未知已证明非阻塞 → 冻结比较 → 现有制度检索门禁 → 受控发布
  ├─ 影响推荐的单家缺失运费 → 现有 CONFIRM_MISSING / SHIPPING_AMOUNT → 重算
  └─ 不安全、无法判断或尚不支持的问题 → 保持需审查，不能强行发布
```

目前补问仍复用单家缺失运费流程：多个影响推荐的报价、明确 `UNKNOWN` 但需要更正、其他待确认费用等尚未接入通用批量回答；明确返回需审查，而不是假装完成自主调查。

新节点为 `analyze_decision_impact`；报告 artifact 类型为 `DECISION_IMPACT_RESULT`。每次冻结比较重新分析，`INPUT_SNAPSHOT` 保存 `decision_impact_artifact_id`。

原结果接口 `GET /api/v1/tasks/{task_id}/results` 和单结果接口新增 `decision_impact`，可查看本次依据；旧结果没有该关联时返回 `null`。最初补问尚未生成比较结果时，报告已经存在于 workflow artifact 中，前端专用调查读取接口留待后续实现。

制度检索固定三类查询与异常暂停仍保持当前行为；无影响证明不允许跳过必查制度。正式选中供应商依然须为已确认可行，报价、需求及任务版本继续受控。

工作流同时检查全部活跃报价范围、任务修订、报价/文件版本及上传文件哈希。旧结果保存旧证明，新的纠正运行生成新证明，不覆盖历史记录。新节点适用于新启动的运行；上线时应单独验证或完成旧图的进行中任务，不承诺任意跨图升级自动兼容。

## 5. 后续调查协议基线（已定义，执行器待实现）

后续 `Investigation Case` 使用以下协议，不与比较引擎的供应商 `PENDING` 状态混用：

| 记录字段 | 含义 |
| --- | --- |
| `case_id`、`task_id`、`task_revision`、`quote_id`、`quote_version` | 调查与输入身份 |
| `impact_input_sha256`、`policy_binding` | 关联本次影响证明与制度版本 |
| `goal`、`known_facts`、`unknown_fields` | 目标、已有事实及来源、剩余问题 |
| `plan`、`observations` | 简短可调整计划及真实工具结果，不保存模型内部思维链 |
| `status`、`stop_reason` | 调查生命周期与停止原因 |

调查状态冻结为 `PLANNED`、`RUNNING`、`RESOLVED`、`WAITING_INPUT`、`LIMIT_REACHED`、`STALE`。停止原因分别为 `EVIDENCE_CONFIRMED`、`NO_DECISION_IMPACT`、`SOURCES_EXHAUSTED`、`CONFLICT_UNRESOLVED`、`BUDGET_EXHAUSTED`、`INPUT_CHANGED`。等待、达到上限和失效均不代表问题解决。

后续工具统一返回 `tool_name`、任务/输入版本、`status`、`data`、来源引用及可公开的 `error_code`；工具状态为 `OK`、`NOT_FOUND`、`NEEDS_INPUT`、`ERROR`、`DENIED`、`STALE`。工具权限由服务端注入，模型不能自行批准调用或把工具失败改成成功。

本轮只实现影响工具与协议文档；没有新增 Agent 执行器或自动调用外部服务。

后续实施更新（2026-09-18）：上述句子描述第 1、2 步的历史交付范围。当前本地已实现可选的调查执行器，见 [自主调查指引](guide_INVESTIGATION_AGENT.md)；实现为真实停止边界补充 `EVIDENCE_INSUFFICIENT` 与 `MODEL_UNAVAILABLE`，避免把未查完或模型故障误记为来源耗尽。原严格审核与制度门禁继续保持。

## 6. 验证指令

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/rules tests/backend/test_workflow.py tests/backend/test_workflow_policy_rag.py
```

覆盖费用阈值、并列、非阻塞未知、无可行基准、币种/税务/计价风险、严格审核入口、制度检索门禁、版本重算、历史结果及访问权限。测试使用固定解析输出，无需 API key。

全仓库回归：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

PostgreSQL 专项由原测试策略单独启用；默认跳过不代表已验证真实 PostgreSQL 或真实 LLM。
