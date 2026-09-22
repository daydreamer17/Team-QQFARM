# full_flow_demo3

这是一套重新设计的**合成、带对抗性、可分阶段复现**全流程测试数据。它与 lc 分支原数据在供应商组合、价格、数量、日期、制度阈值和预期结果上都不同；不得用于真实采购。

## 它重点测什么

- 需求 PDF/TXT/MD 的自然语言解析与人工确认。
- 5 家全新报价的 PDF/CSV 混合输入、分页证据、MOQ、包装倍数、费用状态、旧价格与当前价格。
- “未知运费会不会改变选择”的阻塞判断，以及人工回答后的恢复。
- 低单价但 MOQ 导致超预算、成本仅差 SGD 14、复杂付款条款和近似名称供应商精确身份。
- 六项主/次排序指标、成本容差、排除供应商和供应商历史快照。
- Policy 上传、条款复核、Embedding/Rerank 检索、引用与审批阈值提示。
- Scenario baseline/delta、apply、STALE，需求修改和报价修订后的全量重算。
- 决策助手的自然语言意图、解释引用、禁止越权和提示注入防护。

## 主流程文件（同一任务最多 5 份报价）

1. 发布 `policy/electronics_edge_policy.txt`，用同目录元数据和 reviewed clauses 核对条款。
2. 新建任务，优先上传 `requirement/procurement_requirement.pdf`；TXT、MD 是等价解析回归。
3. 人工核对 `requirement/confirmed_requirement.json`，不要直接盲信模型填充。
4. 目标分支没有全局历史数据时，启动 API/Worker 前把 `SUPPLIER_HISTORY_ROOT` 设为 `data/generated/inputs/development/full_flow_demo3/supplier_history`；本目录已包含完整、可校验的 `2026-08-06-v1` 快照。
5. 按 `manifest.json` 的 `primary_quotes` 上传 5 份报价。Pacific Rim Circuits 使用 canonical CSV，其余使用不同版式 PDF。
6. Lotus Components 的运费应保持 UNKNOWN，系统应补问且不得当作 0。人工验收值为 SGD 490.00，仅保存在运行时隔离的 `evaluation/reference/full_flow_demo3/`。
7. 完成字段审核后运行比较，并检查制度证据、未知项影响、差距说明和供应商信息页。
8. 逐条粘贴 `conversation_prompts/prompts.json` 中的问题，确认“解释”和“修改偏好”不会混淆。

## 变更与失效测试

- 上传 `staged_updates/sup-032_quote_revision_2.csv` 作为 Lotus Components 的新版本：旧结果、Summary 和 Scenario 应变为 STALE，并把推荐从 SUP-034 反转为 SUP-032。
- 再用 `requirement/revisions/procurement_requirement_rev2.txt` 将到货期限收紧到 2026-11-09：应推进 task revision，历史结果保留但不得覆盖当前版本。
- 精确预期只保存在 `evaluation/reference/full_flow_demo3/reference_answers.json`，不得挂载给 Worker 或 Agent。

## 隔离负向用例

`negative_controls/` **不要和主流程报价一起上传**，每个文件应在独立任务中测试：

- `invalid_header_quote.csv`：未注册 CSV 表头，应显式拒绝。
- `unsupported_business_days.csv`：工作日交期不能被 MVP 偷换为自然日，应保持 PENDING。
- `wrong_part_quote.csv`：错误料号应明确 INFEASIBLE。
- `ambiguous_current_prices.pdf`：同一版本存在两个 CURRENT 价格，应保留 CONFLICT。
- `prompt_injection_quote.pdf`：供应商文档中的指令不得改变规则、泄露提示词或触发外部动作。
- `scan_only_quote.pdf`：无可提取文本，应显式失败或转人工，不得生成“正常空报价”。

## 重新生成与校验

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
PYTHONPATH=src .venv/bin/python data/generate_full_flow_demo3.py
.venv/bin/python -m pytest tests/backend/test_full_flow_demo3_dataset.py -q
```
