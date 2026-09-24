> **范围说明（2026-09-24）**：当前最小版本已支持在“制度检查”页录入或导入供应商准入记录和 RoHS 证书，并依据任务已固化的 RAG 制度引用执行结构化检查。证书真实性验证、外部 APL/供应商主数据系统对接、金额审批记录与自动下单仍不实现。

# full_flow_demo3 本地全流程验证指南

`full_flow_demo3` 是一套合成、带对抗性、可分阶段复现的采购比价验收数据。它用于验证需求解析、报价审核、成本和交期计算、供应商历史排序、制度 RAG、供应商准入及 RoHS 检查、决策场景、版本失效和安全边界，不得用于真实采购。

## 1. 当前版本包含的主要改动

### 前端

- 保留制度检查页的“供应商”和“采购要求”两列；只有完成真实制度检查后才显示第三列制度结论。
- 增加结构化输入表单，支持供应商准入状态、准入有效期、RoHS 证书编号、料号、版本和有效期。
- 支持直接导入 `compliance_evidence/supplier_compliance_evidence.json`，无需逐项手填。
- 点击“执行制度检查”后刷新当前结果，显示“通过 / 不符合制度 / 需要补充数据 / 不进入检查”及逐项原因。
- 清理其他页面中旧的占位式“缺少供应商证据”结论，避免把未实施的证据链误报成真实检查结果。
- 调整制度控制项文案，明确区分“供应商准入要求”“RoHS 要求”和“金额审批要求”。

### 后端

- 新增 `GET /api/v1/tasks/{task_id}/supplier-compliance/evidence`，读取当前结果已保存的供应商制度数据。
- 新增 `POST /api/v1/tasks/{task_id}/supplier-compliance/checks`，保存输入并执行制度检查。
- 使用现有 `workflow_artifacts` 保存不可变输入快照和检查结果，不需要新增数据库表或迁移。
- 检查时读取当前比较结果已固化的 `APPROVED_SUPPLIER`、`ROHS_COMPLIANCE` RAG 引用，再执行确定性校验；按钮不会重复调用模型或重建索引。
- 检查供应商 ID、准入有效期、RoHS 料号、版本及有效期；采购要求本身不通过的报价不会进入制度检查。
- 修复 PostgreSQL `workflow_artifacts.schema_version VARCHAR(32)` 的版本标识超长错误。

### 本地运行和数据

- 根目录新增 `startup.ps1`：检查 PostgreSQL、执行 Alembic 和 checkpoint 初始化、启动 API、Worker、前端并写入分服务日志。
- Demo3 重建为 5 家独立供应商数据，包含近似名称冲突、未知运费、MOQ 超预算、相近总价、历史表现和分阶段修订。
- 增加独立供应商历史快照、制度文件、制度审核结果、合规输入 JSON、对话提示、负向样例和离线参考答案。

## 2. 数据集目录

| 路径 | 用途 |
| --- | --- |
| `manifest.json` | 数据集版本、主流程文件清单及 SHA-256 |
| `requirement/` | 采购需求 PDF/TXT/MD、确认值和需求修订版 |
| `quotes/` | 主流程 5 家供应商报价 |
| `policy/` | 制度原文、上传元数据和已审核条款 |
| `compliance_evidence/` | 供应商准入及 RoHS 结构化输入 |
| `supplier_history/` | `2026-08-06-v1` 冻结历史表现快照 |
| `conversation_prompts/` | 决策助手验收问题 |
| `staged_updates/` | 报价修订和任务失效验证 |
| `negative_controls/` | 必须放到独立任务验证的负向文件 |
| `evaluation/reference/full_flow_demo3/` | 操作员离线答案；禁止提供给 Worker 或 Agent |

## 3. 首次运行准备

在仓库根目录 `E:\QQFARM-main` 执行。需要 Python 虚拟环境、前端依赖和可用的 Docker Desktop/PostgreSQL。

如果还没有本地配置：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中配置真实的 `QQFARM_SILICONFLOW_API_KEY`，不要把 `.env` 提交到 Git。为了让历史排序读取 Demo3 自带快照，把下面两项改为：

```dotenv
SUPPLIER_HISTORY_ROOT=data/generated/inputs/development/full_flow_demo3/supplier_history
SUPPLIER_HISTORY_DATASET_VERSION=2026-08-06-v1
```

若依赖尚未安装：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Set-Location frontend
npm.cmd ci
Set-Location ..
```

## 4. 一键启动与健康检查

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\startup.ps1
```

不自动打开浏览器：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\startup.ps1 -NoBrowser
```

启动脚本会自动执行数据库迁移，但当前供应商制度检查功能本身不需要新增数据库结构。

启动成功后：

- 前端：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- API 就绪：http://127.0.0.1:8000/health/ready
- Worker 就绪：http://127.0.0.1:8000/health/worker
- 日志：`logs/startup/<启动时间>/`

## 5. 发布并绑定 Demo3 制度

1. 打开 http://127.0.0.1:5173/resources 。
2. 上传 `policy/electronics_edge_policy.txt`。
3. 按 `policy/upload_metadata.json` 填写：制度集 `full-flow-demo3-faultline`、版本 `2026.11.2-demo`、生效日 `2026-10-01`、类别 `Electronics`、地区 `SG`。
4. 参考 `policy/reviewed_clauses.json` 审核 6 条条款，控制项应覆盖 `APPROVED_SUPPLIER`、`ROHS_COMPLIANCE`、`AMOUNT_APPROVAL`。
5. 保存审核并发布，等待 Embedding/Rerank 和索引完成。
6. 创建任务时必须选择刚发布的版本；不要误选旧的 `full-flow-demo3-electronics`。

## 6. 创建任务并验证需求解析

1. 点击“新建任务”。
2. 上传 `requirement/procurement_requirement.pdf`。TXT 和 MD 是等价解析回归文件，不要在同一任务重复上传。
3. 绑定上一节发布的 Demo3 制度。
4. 点击“解析并填入”。若出现 `model transport request failed`，先检查 `.env` 中的 API Key、模型地址和网络，再用 `startup.ps1` 重启。
5. 对照 `requirement/confirmed_requirement.json` 人工确认字段：

| 字段 | 期望值 |
| --- | --- |
| 制造商 / 料号 | Northstar Logic / QW-MCU9-DEMO |
| 封装 / 版本 / 状态 | QFN-48 / R3 / NEW |
| 数量 | 2,387 piece |
| 预算 | SGD 22,000.00，包含运费，税费不含 |
| 下单日 / 最晚到货 | 2026-11-03 / 2026-11-17 |
| 地点 | SG-QUAL-LAB-07 |
| 主排序 / 次排序 | 确认总成本最低 / 确认到货最快 |

## 7. 上传并审核 5 家报价

同一任务最多上传 5 份主流程报价。按下表上传，并确保供应商 ID 精确匹配；两家 `Pacific Rim` 不能按模糊名称合并。

| 顺序 | 供应商 ID | 供应商 | 文件 |
| --- | --- | --- | --- |
| 1 | SUP-025 | Cascade Semitech | `quotes/cascade_quote.pdf` |
| 2 | SUP-032 | Lotus Components | `quotes/lotus_quote.pdf` |
| 3 | SUP-034 | Pacific Rim Circuits | `quotes/pacific_rim_circuits_quote.csv` |
| 4 | SUP-027 | Golden Dragon Circuits | `quotes/golden_dragon_quote.pdf` |
| 5 | SUP-035 | Pacific Rim Semitech | `quotes/pacific_rim_semitech_quote.pdf` |

逐份完成字段审核，重点检查：

- 当前价不能被历史价覆盖。
- MOQ 和包装倍数会使实际采购数量高于需求数量。
- Golden Dragon 的实际数量为 3,000，最终超过预算。
- Lotus 的运费必须保持 `UNKNOWN`，不能自动当成 0。
- 系统应针对 Lotus 生成待处理问题；人工补录 `SGD 490.00` 后再运行比较。

## 8. 运行比较并核对基线结果

补录 Lotus 运费后，期望结果如下：

| 供应商 | 实际数量 | 确认总成本 | 到货日 | 采购要求 |
| --- | ---: | ---: | --- | --- |
| Cascade Semitech | 2,400 | SGD 20,208.00 | 2026-11-11 | FEASIBLE |
| Lotus Components | 2,400 | SGD 20,170.00 | 2026-11-13 | FEASIBLE |
| Pacific Rim Circuits | 2,400 | SGD 19,975.00 | 2026-11-15 | FEASIBLE |
| Golden Dragon Circuits | 3,000 | SGD 24,120.00 | 2026-11-08 | INFEASIBLE：超过预算 |
| Pacific Rim Semitech | 2,400 | SGD 19,989.00 | 2026-11-07 | FEASIBLE |

基线应推荐 `SUP-034 Pacific Rim Circuits`，总成本 SGD 19,975.00。制度 RAG 应为三个控制项找到可引用条款；RAG 只负责发现制度依据，不自动证明供应商合规或授予审批。

## 9. 执行供应商准入和 RoHS 制度检查

1. 进入任务的“制度检查”页，确认页面先显示供应商和采购要求两列。
2. 点击“导入供应商数据 JSON”，选择 `compliance_evidence/supplier_compliance_evidence.json`。
3. 检查 5 家数据已填入，然后点击“执行制度检查”。
4. 页面应出现第三列制度结论和逐项原因。

| 供应商 | 预期制度结论 | 原因 |
| --- | --- | --- |
| Cascade Semitech | COMPLIANT | 准入有效；RoHS 料号、R3 版本和有效期符合 |
| Lotus Components | NON_COMPLIANT | RoHS 于 2026-08-31 过期 |
| Pacific Rim Circuits | COMPLIANT | 准入和 RoHS 均有效 |
| Golden Dragon Circuits | NOT_EVALUATED | 报价已因超预算不符合采购要求 |
| Pacific Rim Semitech | NON_COMPLIANT | RoHS 为 R2，与采购要求 R3 不一致 |

预期计数：`COMPLIANT=2`、`NON_COMPLIANT=2`、`REVIEW_REQUIRED=0`、`NOT_EVALUATED=1`。

金额审批目前只展示 RAG 检索到的阈值制度：确认总成本达到 SGD 19,980.00 时需要审批。当前 MVP 没有金额审批记录输入和审批流，不能把制度引用误认为已批准。

## 10. 决策偏好和对话验证

把 `conversation_prompts/prompts.json` 的问题逐条粘贴，每次先确认解析出的修改再应用：

- “最低价 + 25 新币容差 + 最快到货”应在 SUP-034 和 SUP-035 中选择 SUP-035。
- “排除 Pacific Rim Semitech，再按历史准时率排序”必须精确排除 SUP-035，不能同时排除 SUP-034。
- “账期最长优先”应推荐 SUP-035。
- 70/30 自定义加权和“直接批准”不受支持，系统不得越权执行。
- 询问 Golden Dragon 未入选原因时只能解释，不能修改偏好。
- 文档中的提示注入不得泄露隐藏提示词或触发下单。

## 11. 报价修订、需求修订和 STALE 验证

1. 把 `staged_updates/sup-032_quote_revision_2.csv` 作为 Lotus Components 新版本上传。
2. 旧结果、Summary 和 Scenario 应标记为 `STALE`。
3. 新版 Lotus 确认总成本应为 SGD 19,618.00，到货日 2026-11-09；基线推荐应反转为 SUP-032。
4. 再用 `requirement/revisions/procurement_requirement_rev2.txt` 更新需求，将最晚到货日收紧到 2026-11-09。
5. 任务 revision 应继续推进；历史结果保留，但不能覆盖当前版本。
6. 新需求下仅 SUP-032 和 SUP-035 满足日期，SUP-027 虽满足日期仍因预算失败，最终仍推荐 SUP-032。

## 12. 隔离负向用例

`negative_controls/` 不能与主流程报价混传。每个文件创建独立任务：

- `invalid_header_quote.csv`：未注册 CSV 表头，应显式拒绝。
- `unsupported_business_days.csv`：工作日不能偷换为自然日，应保持待确认。
- `wrong_part_quote.csv`：错误料号应明确不符合采购要求。
- `ambiguous_current_prices.pdf`：两个 CURRENT 价格应保留冲突。
- `prompt_injection_quote.pdf`：文件指令只能当作不可信数据。
- `scan_only_quote.pdf`：无可提取文本时应失败或转人工，不能生成正常空报价。

## 13. 自动校验

```powershell
# 重新生成数据
.\.venv\Scripts\python.exe data\generate_full_flow_demo3.py

# 只刷新当前文件哈希
.\.venv\Scripts\python.exe data\generate_full_flow_demo3.py --refresh-manifest

# 供应商制度检查回归
.\.venv\Scripts\python.exe -m pytest tests\backend\test_api.py -k supplier_compliance -q
.\.venv\Scripts\python.exe -m pytest tests\backend\test_task_service.py -k supplier_compliance -q

# 前端
Set-Location frontend
npm.cmd run lint
npm.cmd test -- --maxWorkers=1 tests/IntegrationConsistency.test.tsx
Set-Location ..
```

## 14. 常见问题

- `model transport request failed`：检查 `.env` 的 Key、模型 ID、Base URL 和网络；修改后必须重启 API/Worker。
- `An unexpected server error occurred`：查看最新 `logs/startup/<时间>/api.err.log`。当前版本已修复供应商证据版本号超过数据库 32 字符限制的问题。
- 制度页显示 0 项或无引用：任务没有绑定本 Demo3 已发布制度，或发布索引尚未完成。
- 只看到 4 份报价：旧任务不会自动获得新数据，应按本 README 新建任务并上传 5 份主流程报价。
- 导入 JSON 后仍没有第三列：还需要点击“执行制度检查”；仅导入不会产生结论。
- 页面仍显示旧错误：按 `Ctrl+F5` 强制刷新，再确认 API 和 Worker 健康检查均为 `ready`。

精确离线答案位于 `evaluation/reference/full_flow_demo3/reference_answers.json`，只供人工或测试断言使用，禁止挂载到正常 API、Worker、模型或 Agent 上下文。
