# 制度检查与材料核验操作指南

本轮流程为 **采购需求 → 报价与审核 → 制度检查 → 决策比较 → 采购总结**。先计算报价基础事实，待制度阶段明确确认后才发布当前比较结果。本文介绍操作与验证方法，不代替实际测试记录，也不表示云端或真实模型全流程已验收。

## 1. 启动更新后的服务

下列命令在仓库根目录的 **PowerShell** 中执行。使用已有 `.venv` 和本地 `.env`；数据库、API 和 worker 必须使用相同数据库及文件存储配置。先备份重要数据库，停止旧 API/worker 后迁移；不要删除数据卷。

```powershell
Set-Location E:\iss_hackathon\Team-QQFARM
docker compose up -d postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m supplier_comparison.checkpoints setup
.\.venv\Scripts\python.exe -m uvicorn supplier_comparison.backend.api:app --host 127.0.0.1 --port 8000
```

在另一个根目录终端运行后台 worker：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.worker run-loop --poll-interval 1
```

也可停止常驻 worker 后，针对 API 返回的真实 job ID 执行一次：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.worker run-job --job-id <job_id>
```

前端在单独终端启动，使用项目要求的 Node 版本：

```powershell
Set-Location E:\iss_hackathon\Team-QQFARM\frontend
npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

如果端口被占用，先确认是否已有开发服务；不要同时启动第二份。Swagger 为 `http://127.0.0.1:8000/docs`。全容器运行时，使用更新后镜像重建 `api` 和 `worker`，避免旧镜像仍运行旧流程；不要让旧 worker 与新本机 worker 同时处理同一数据库。

迁移新增材料记录和附件表，旧任务保留旧流程标记。历史结果不会补造材料审核记录；旧任务重新分析进入新流程。LangGraph checkpoint 仍由独立 setup 命令管理。

## 2. 导入可执行的合成制度

演示制度有两个不可变版本：`data/policies/compliance-closure-demo/v1/` 保留选定后金额待办；`data/policies/compliance-closure-demo/v2/` 增加发布前金额审批记录核验，门槛为 SGD 6500.00。两版均覆盖供应商准入、RoHS 材料和金额审批，适用范围为 `Electronics / SG`。若要验证三类证明闭环，请发布 v2；审核人为显式标注的虚构演示审核人，不能当成真实企业审核。

配置已有 `SUPPLIER_EMBEDDING_*`、`SUPPLIER_RERANK_*` 及其引用的密钥环境变量后，从根目录执行：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.rag smoke-models
.\.venv\Scripts\python.exe -m supplier_comparison.rag import-policies --manifest data/policies/compliance-closure-demo/v2/manifest.json --publish
```

以上命令会调用真实模型服务，可能产生费用。发布需全部 embedding 成功；从发布返回值或 `GET /api/v1/policy-sets` 取得实际 `policy_index_version`，不要手写或沿用其他制度的索引版本。新建采购任务时选择该已发布集合与索引。任务固定版本，不会自动切换到后来发布的制度。

旧制度的空 `rule_parameters` 或旧阈值格式不等于新可执行规则。它们仍可展示引用，但会显示未自动核验。要启用核验，必须审核 `compliance-rule/1.0` 的匹配字段、日期要求、缺失/过期/不匹配处理和执行阶段，并发布**新的制度版本**；不要覆盖旧正文、索引或历史参数。

v2 金额条件为 `TOTAL_COST / SGD / GTE / 6500.00 / BEFORE_PUBLICATION`。达到门槛时必须核对任务内的金额审批记录；系统只保存和匹配审批事实，不执行审批。审批缺失、金额不足、错币种、过期或拒绝时保留发布阻塞，但不把供应商资质写成不合格。

## 3. 五步操作与材料演示

1. **采购需求：**选择上述已发布制度，确认采购料号、地区及品类。
2. **报价与审核：**按现有流程上传报价、确认字段和补充必要费用，进入制度检查。报价未知项仍遵守原来的比较阻塞保护。
3. **制度检查：**等待当前版本评估完成。查看各适用条款、原文引用、检查原因及材料要求；不能根据“上传成功”判断通过。检索异常先修复或按现有问题入口重试，不能以“暂不补充材料”绕过。
4. **确认后比较：**可以补齐材料，也可以明确确认仍缺的项目。全部候选资料不足时得到暂定比较，没有已核验正式推荐；存在已核验候选时，先在该资格范围按采购偏好排序。制度资格不改变报价的可行性含义，也不写入用户排除名单。
5. **采购总结：**生成后检查其核验版本、引用和待办与所选结果一致。旧结果、旧总结和旧 AI 消息保留原依据，不随新材料改写。

单供应商基础材料包：`data/generated/fixtures/compliance/closure-demo/`。多供应商异常与版本替换材料包：`data/generated/fixtures/compliance/material-regression/`，其中 `supplier-approval`、`rohs-certificates`、`amount-approvals` 三类各有 `v1` 和 `v2`。完整文件与预期见该目录的 `README.md`。

本目录也提供一份**新生成的合成采购需求与单供应商报价**，可在 2026-09-24 演示，无需延长旧 V1 报价：

- `procurement_requirement.txt`：前端需求文件上传；如使用手动需求录入，则按同目录 `confirmed_requirement.json` 填写。JSON 是契约数据，不是前端需求文件上传格式。
- `supplier_SUP-024_quote.csv`：报价上传使用的标准单行 CSV；独立 scenario/quote/document ID，供应商为 SUP-024，报价日期 2026-09-24，有效期至 2027-09-01，运费明确。
- 需求为 1000 件、预算 SGD 8000（含运费及适用其他费用），计划下单 2026-09-24、截止交付 2026-10-01。税在该虚构案例中不适用；付款条款明确为发票日起 Net 30。

这份单供应商数据用于复现“缺材料 → 不匹配 → 明确替代 → 确认与发布”的闭环，不用于多供应商排名效果对比。它是独立合成报价，不表示旧供应商延长了真实或旧示例报价。演示日期超过计划下单日期时，创建新的合成需求日期并确认，不能静默改写历史任务或过期原件；报价与材料仍需在各自有效期内。

| 文件 | 文件中声明的事实 |
| --- | --- |
| `admission_SUP-024.txt` | SUP-024 的虚构准入记录，已准入，有明确有效期 |
| `rohs_other_part_SUP-024.txt` | 制造商 QQ Demo Components，覆盖 QW-MCU8-DEMO |
| `rohs_QW-MCU9_SUP-024.txt` | 制造商 QQ Demo Components，覆盖 QW-MCU9-DEMO |

身份与现有 V1 CSV 中 Sterling Components（SUP-024）一致。文件内没有排名或系统参考答案。不要把需求、报价、材料目录或本指南导入制度知识库。其他演示集若采用不同供应商/料号，需要对应真实来源事实，不能把 SUP-024 资料套到其他对象。

演示时，在 SUP-024 的准入检查项上传准入文件，人工对照填写材料编号、结论和日期。再在 RoHS 项上传另一料号文件，**如实填写文件中的 QW-MCU8-DEMO**，不要把预填的采购料号误认为原件覆盖范围。等待核验后查看具体匹配原因。随后使用“更正/替代”入口上传覆盖 QW-MCU9-DEMO 的文件，明确替代旧记录；仅再新增一份互相矛盾的材料不能按最后上传者自动判通过。

本材料有效期为 2026-09-01 至 2027-09-01，未声明永久有效。演示超过有效期时应更新合成材料版本并重新确认，不能修改系统时间绕过核验。V1 报价原件本身有独立报价日期及有效期；若已过期，请使用按现有数据规范生成的当前演示报价，或在测试固定时钟下验证历史用例，不要把过期报价当成当前可行报价。

材料保存后任务版本增加，页面显示重新核验；核验结束后仍需明确确认本阶段。确认也产生新版本，worker 再检查资料、引用及版本一致性，然后发布配套比较。双窗口旧表单会返回版本冲突，刷新并核对变化后重试。

## 4. Swagger / API 联调

| 接口 | 用途 |
| --- | --- |
| `GET /api/v1/tasks/{task_id}/compliance` | 推荐生成前读取独立制度工作区、当前评估和历史材料 |
| `POST /api/v1/tasks/{task_id}/compliance/evidence` | 新增人工确认事实及可选附件 |
| `POST /api/v1/tasks/{task_id}/compliance/evidence/{evidence_id}/revisions` | 明确更正/替代某一材料记录 |
| `GET /api/v1/tasks/{task_id}/compliance/evidence/{evidence_id}/files/{file_id}/content` | 本任务授权附件查看；`download=true` 下载 |
| `POST /api/v1/tasks/{task_id}/compliance/confirm` | 确认特定评估与保留缺项，排队后续比较 |

材料写入为 multipart：`expected_task_revision`、字符串形式 JSON `facts`、可选 `file`，以及 `Idempotency-Key` 请求头。`facts` 示例中 quote ID 必须替换为当前任务 API 返回的实际 ID：

```json
{
  "quote_id": "<current-quote-id>",
  "control_code": "ROHS_COMPLIANCE",
  "supplier_id": "SUP-024",
  "manufacturer": "QQ Demo Components",
  "manufacturer_part_number": "QW-MCU9-DEMO",
  "material_number": "DEMO-ROHS-SUP024-002",
  "coverage_confirmed": true,
  "outcome": "PASS",
  "effective_from": "2026-09-01",
  "expires_on": "2027-09-01",
  "permanent": false,
  "source_refs": ["fictional-demo-material-register/DEMO-ROHS-SUP024-002"],
  "note": "Synthetic demonstration; fields checked against the attached source."
}
```

`outcome` 是原件声明的人工确认事实，不是前端决定的最终核验结果。至少提供附件或可追溯来源。服务端记录操作者和时间，不能在请求中伪造。支持 PDF/TXT/MD、最大 10 MiB；不做证书解析、OCR、真实性鉴定或来源链接访问。

金额审批记录沿用同一接口，`control_code` 为 `AMOUNT_APPROVAL`，并增加精确字符串金额与币种，例如 `"approval_amount": "8000.00"`、`"currency": "SGD"`。前端从“金额审批要求”区域进入；已有记录通过“替换金额审批记录”建立版本关系，不能覆盖旧记录。

确认接口使用 JSON，包含 `expected_task_revision`、`expected_assessment_id`、`acknowledged_missing_item_ids`、`acknowledge_no_policy`。ID 均取自**当前**工作区；明确暂不补充时填写当前全部相应缺项 ID，不能传旧列表。未绑定制度时必须显式确认 `acknowledge_no_policy=true`，页面显示“未启用”，不是“合规通过”。相同幂等键与相同请求返回原操作；不同请求不要复用同一个键。

## 5. 实现边界与故障定位

- 目录确定全部适用要求，RAG 提供核对原文；Top-3 不是完整制度清单。未知或不支持条款显示 `NOT_EVALUATED`，不能作为通过项。
- 核验基于人工确认来源，只覆盖当前任务、身份、料号、时间和支持的规则。没有跨任务供应商认证库、证书真伪检查或采购审批执行。
- 附件存储与材料记录保留历史。页面从评估引用追溯材料和条款；历史结果读取冻结评估，不能拿新材料解释旧结果。
- `BLOCKED` 且有制度错误：查看引用/服务错误和现有制度检索问题，修复后重试。`AWAITING_CONFIRMATION`：等待人工确认，不是 worker 卡死。保存后长期处理中：检查 worker、对应 job 状态及安全错误码。
- 资料冲突使用明确替代关系；材料不匹配检查供应商、制造商、料号和日期。未注明有效期与永久有效不同。
- AI 可以解释引用、缺项和排序，不能通过聊天补充权威材料、豁免制度或授权采购、下单、付款。

## 6. 验证方法与记录边界

开发者可运行已有规则、工作区及前端测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/rules/test_compliance.py tests/backend/test_compliance_workspace.py
Set-Location frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

PostgreSQL 条件测试需显式启用 `RUN_POSTGRES_TESTS=1` 并配置测试数据库连接；先阅读对应测试对隔离数据库和权限的要求。默认测试使用固定适配器，不证明真实模型质量。真实验证另记录模型配置、时间、制度/索引版本、任务/评估/结果 ID、观察到的错误及重试结果，不记录 API Key。

本演示文件的 manifest/规则契约验证记录由实施结果单独报告。Embedding/Rerank smoke、真实报价解析、完整 UI 及云端验收应分别记录，不能将其中任何一项等同全部通过。

### 2026-09-24 本地验证记录

本轮实施集成记录如下，范围限本地；不代表 Lightsail 或主办方模型环境通过。

| 验证项 | 已观察到的结果与范围 |
| --- | --- |
| 真实 Embedding / Rerank smoke | 两个模型均首次尝试成功；embedding 为 1024 维 |
| 真实 RAG 与制度闭环 | 隔离 PostgreSQL 中完整制度闭环通过，真实检索；不将固定报价适配器等同真实报价解析 |
| 真实 AI 会话变更链路 | 预览、明确确认、应用流程通过；并非采购授权测试 |
| PostgreSQL 条件套件 | 10 passed，1 项付费 Agent 测试跳过 |
| 合成制度静态验证 | manifest、三个有限规则契约和 Decimal 阈值边界通过 |

后端全套固定适配器测试及前端最终结果以本轮最终交付记录为准，避免把中间运行数量写成最终验收结果。

## Final local acceptance (2026-09-24)

- Backend: current main workspace regression is 1051 passed and 46 opt-in tests skipped; paid/live-model and PostgreSQL opt-in cases are listed separately below.
- Frontend: 112 tests passed; lint and production build passed. Chrome desktop (1440px) and mobile (390px) interaction checks used mocked API responses, not a live full-stack browser test.
- PostgreSQL: fixed and live RAG cross-process compliance tests both passed (2 tests), including migration upgrade/downgrade/upgrade, checkpoints and document reuse. Existing recovery/retrieval suites: 10 passed, 1 paid Agent test skipped. Isolated test databases were removed.
- Live models: BAAI/bge-m3 (1024 dimensions) and BAAI/bge-reranker-v2-m3 smoke tests each succeeded on the first attempt. Live policy import/retrieval and the compliance closure passed; quotation extraction in this test used a fixed adapter, not live PDF extraction.
- Live LLM: one isolated conversation preview/confirmation/application/explanation regression passed. This does not constitute exhaustive chatbot or multi-tool Agent acceptance.
- Local API/worker images were rebuilt and started. Migration/checkpoint setup exited successfully; database head is 17b0c2d4e6f8. Health live/ready returned HTTP 200 and the new routes appeared in OpenAPI.
- Official cloud acceptance was not performed in this iteration. Existing database/file volumes and unrelated local changes were preserved.

To repeat the paid live RAG tests, use an account authorized to create and drop isolated test databases:

```powershell
$env:RUN_POSTGRES_TESTS='1'
$env:RUN_COMPLIANCE_LIVE='1'
.\.venv\Scripts\python.exe -m dotenv -f .env run -- .\.venv\Scripts\python.exe -m pytest -q tests/backend/test_compliance_workspace.py -k postgres
Remove-Item Env:RUN_POSTGRES_TESTS
Remove-Item Env:RUN_COMPLIANCE_LIVE
```
