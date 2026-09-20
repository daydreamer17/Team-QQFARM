# QuoteWise 新版报价审核与提交流程指南

> 更新日期：2026-09-20
>
> 适用分支：当前 `lc` 工作区
>
> 适用范围：报价 PDF/CSV 上传、LLM 候选提取、30 字段人工确认、后端权威复核、正式提交与手动比较
>
> 数据性质：本文中 MCU、供应商、报价和 Policy 均为合成演示数据。

## 1. 改造目标

旧流程中，部分字段只要通过自动预检就会变成只读，用户只能修正阻塞字段。这会把“模型提取成功”误当成“业务事实一定正确”。

新流程把职责拆分为：

- LLM 负责从文件中提取候选值和证据，不拥有最终确认权。
- 后端确定性代码负责类型、枚举、金额、版本、证据边界和关联字段预检。
- 人工重点处理缺失、冲突和低置信度字段；系统已填写的内容可直接确认，也可以修改。
- 前端负责立即格式与关联校验，给出简短、可操作的错误原因。
- 后端在人工提交审核和正式提交时各执行一次权威复核。

## 2. 完整流程

```text
上传 PDF/CSV
→ 文件验证、哈希和不可覆盖存储
→ PDF/CSV 解析并生成稳定来源 ID
→ LLM 提取 30 个业务字段的候选值与证据
→ 后端确定性预检
→ 前端自动填入候选值，并优先展示需要处理的字段
→ 用户补充异常项；其余内容可直接确认或修改
→ 前端执行字段和关联校验
→ 用户点击“确认并提交报价”
→ 前端依次调用人工审核与正式提交接口
→ 后端应用审核动作并执行权威复核
→ 复核通过后自动正式提交；不要求用户再次点击
→ 正式提交前，后端重新加载最新 Batch、人工事件、字典和采购需求再次复核
→ 写入正式报价并推进 Task Revision
→ 继续上传其他报价
→ 已提交报价可上传新版或软停用；历史原件始终保留
→ 全部报价完成后，用户手动点击“开始比较”
```

每份报价正式提交后不会自动开始比较，也不会自动选择供应商。

## 3. 报价草稿状态

| 状态 | 含义 | 用户可执行操作 |
| --- | --- | --- |
| `UPLOADED` | 文件已保存，等待 Worker | 等待 |
| `PROCESSING` | 正在解析、调用模型和执行预检 | 查看进度 |
| `REVIEW_REQUIRED` | 需要人工确认 30 个字段 | 预览原件、修改、确认缺失 |
| `READY_TO_SUBMIT` | 人工确认完整，后端权威复核通过 | 正式提交 |
| `SUBMITTED` | 已写入正式报价历史 | 查看原件、上传新版或软停用 |
| `FAILED` | 文件、解析或模型处理失败 | 查看错误后重新上传 |
| `STALE` | 草稿打开后任务需求或版本已变化 | 重新上传并审核 |
| `DISCARDED` | 用户已废弃草稿 | 只读审计 |

## 4. 30 个人工审核字段

前端通过 `GET /api/v1/quote-field-schema` 动态获取字段定义，不再单独维护一份硬编码规则。

| 分组 | 字段 |
| --- | --- |
| 身份 | `supplier_name`、`supplier_country` |
| 规格 | `category`、`item`、`manufacturer`、`manufacturer_part_number`、`package`、`revision`、`condition` |
| 价格 | `currency`、`unit_price`、`price_basis_quantity`、`price_basis_unit` |
| 包装 | `packaging_type`、`units_per_pack`、`order_multiple_units` |
| MOQ | `moq_quantity`、`moq_unit` |
| 费用 | `shipping_fee_status`、`shipping_fee_amount`、`other_fees_status`、`other_fees_amount`、`tax_mode` |
| 交期 | `lead_time_days`、`day_basis`、`delivery_semantics`、`start_event` |
| 商务 | `payment_terms`、`quote_date`、`valid_until` |

### 4.1 16 个始终关键字段

```text
supplier_name
manufacturer
manufacturer_part_number
package
condition
currency
unit_price
price_basis_quantity
price_basis_unit
order_multiple_units
moq_quantity
moq_unit
shipping_fee_status
other_fees_status
tax_mode
valid_until
```

这些字段必须有可用值。`UNKNOWN`、`MISSING` 或 `CONFLICT` 都不能通过正式提交门禁。

### 4.2 10 个条件关键字段

```text
revision
packaging_type
units_per_pack
shipping_fee_amount
other_fees_amount
lead_time_days
day_basis
delivery_semantics
start_event
quote_date
```

当后端根据采购需求、文档原文或当前字段值判定它们适用时，必须提供可用值。不适用时可通过 `CONFIRM_MISSING` 确认缺失。

### 4.3 4 个可选字段

```text
supplier_country
category
item
payment_terms
```

可选字段也必须留下人工审核记录，但可以确认为原报价未提供。

## 5. 人工审核动作

| 动作 | 使用场景 | 结果 |
| --- | --- | --- |
| `CONFIRM_VALUE` | 对照原件后确认当前候选值 | 保留 `DOCUMENT` 来源，不改变值 |
| `SET_VALUE` | 补充缺失值或修正错误值 | 记录为 `USER_INPUT` 或 `USER_CORRECTION` |
| `CONFIRM_MISSING` | 可选或当前不适用字段确实未提供 | 保留 `MISSING`，增加人工确认事件 |
| `MARK_MISSING` | LLM 错误提取了原文中不存在的值 | 清除错误候选值，保留修正历史 |
| `CONFIRM_CONFLICT` | 可选或不适用字段在原文中仍有无法消除的冲突 | 保留冲突审计；不能让必填字段通过 |

用户点击“确认全部字段并提交后端复核”时，前端会为 30 个字段各生成一个动作。后端不接受少于 30 个字段、重复字段或夹带系统字段的请求。

## 6. 费用状态与金额规则

`shipping_fee_status/shipping_fee_amount` 和 `other_fees_status/other_fees_amount` 使用同一套确定性规则。

| 费用状态 | 金额规则 | 能否通过必填门禁 |
| --- | --- | --- |
| `KNOWN_AMOUNT` | 必须是大于或等于 0 的十进制字符串 | 可以 |
| `FREE` | 金额留空或显式为 `0`/`0.00` | 可以 |
| `NOT_APPLICABLE` | 金额留空或显式为 `0`/`0.00` | 可以 |
| `INCLUDED` | 金额必须留空，避免重复加总 | 可以 |
| `UNKNOWN` | 金额必须留空 | 不可以，需先确认真实状态 |

金额必须以十进制字符串传输，例如 `"40.00"`。前端不使用 JavaScript 浮点数执行金额计算。

特别注意：

- “报价未说明运费”不等于 `FREE`、`NOT_APPLICABLE` 或金额 `0`。
- 原文明确“已包含”时，不应再录入一笔单独金额。
- 币种变更时，所有非空金额的 unit 必须与新币种一致。

## 7. 关联字段校验

| 关联组 | 规则 |
| --- | --- |
| 价格 | `unit_price`、`price_basis_quantity`、`price_basis_unit` 必须成组有效 |
| 费用 | 两组费用状态必须与金额一致 |
| 币种 | 单价、运费和其他费用的 unit 必须与 `currency` 一致 |
| MOQ/包装 | MOQ 不是按颗时，必须同时确认包装类型和每包数量 |
| 交期 | `lead_time_days`、`day_basis`、`delivery_semantics`、`start_event` 需要成组完整 |
| 有效期 | `quote_date` 不能晚于 `valid_until` |

后端已根据原报价判定为当前适用的交期字段，不能通过把四个字段全部清空来绕过校验。

## 8. 单价版本规则

如果报价中同时存在当前价和历史价：

- 唯一 `CURRENT` 值进入当前候选。
- `SUPERSEDED` 值仅保留为审计事实。
- 存在多个不同 `CURRENT` 值时生成冲突。
- 只有历史价、无法识别当前价时阻止正式提交。

`full_flow_demo2` 的 E 报价中，当前价是 `SGD 7.32`，`SGD 7.40` 是已废弃历史价，不应形成当前价格冲突。

## 9. 前端操作步骤

### 9.1 上传报价

1. 进入任务的“报价与证据”页面。
2. 填写受管供应商编号。
3. 选择 PDF 或固定模板 CSV，单文件不超过 5 MiB。
4. 点击“上传并开始审核”。
5. 保持 API 和 Worker 运行，等待草稿从 `PROCESSING` 进入 `REVIEW_REQUIRED`。

开发环境会自动将上传标记为合成数据，正式构建不会向普通用户显示该选项。

### 9.2 核对报价内容

1. 点击“查看原件”。
2. 先处理页面顶部标红的缺失、冲突或异常项目。
3. 系统已填写的内容无需重新输入；需要时展开“其他内容”查看或修改。
4. 可通过“查看原文与依据”核对原文、页码、来源和字段规则。
5. 可选或不适用字段未提供时保持为空，系统会生成 `CONFIRM_MISSING`。
6. 检查完成后，点击“确认并提交报价”。系统会自动为未修改字段生成确认记录，并在复核通过后正式提交。

复核通过后系统会自动正式提交；复核失败则保留当前输入并显示错误。

### 9.3 自动复核与正式提交

1. 前端先提交审核动作，后端执行确定性复核。
2. 复核通过后，前端自动调用正式提交接口。
3. 任一步失败都会停留在当前页面，并显示需要处理的问题。
4. 提交后该报价进入历史记录，可继续上传其他供应商报价。
5. 全部报价都完成后，再点击工作区中的“开始比较”。

### 9.4 更新或停用已提交报价

- 点击“修改报价”后，系统以当前原件创建新的审核草稿并重新提取；修改并提交成功后形成下一版本，旧版本保留在历史中。
- 点击“停用报价”并确认后，该报价不再参与后续比较，但报价记录、全部版本和审计记录不会删除。
- 更新或停用都会推进任务版本，并使旧的比较结果与 Summary 失效；用户需重新运行比较。

## 10. API 契约

### 10.1 获取字段 Schema

```http
GET /api/v1/quote-field-schema
```

返回字典版本、哈希、审核策略版本、30 个字段和关联组。

### 10.2 人工审核整份报价

```http
PUT /api/v1/tasks/{task_id}/quote-drafts/{draft_id}/review
Idempotency-Key: <unique-key>
Content-Type: application/json
```

请求示意：

```json
{
  "expected_draft_revision": 2,
  "schema_version": "quote-review-schema/1.0.0",
  "actions": [
    {
      "action": "CONFIRM_VALUE",
      "field_name": "supplier_name",
      "expected_field_id": "fld_example",
      "expected_field_version": 1
    },
    {
      "action": "SET_VALUE",
      "field_name": "shipping_fee_amount",
      "expected_field_id": "fld_shipping",
      "expected_field_version": 1,
      "raw_value": "Supplier confirmed SGD 50.00",
      "normalized_value": "50.00",
      "unit": "SGD",
      "reason": "Supplier confirmed the freight amount."
    }
  ]
}
```

> 示例仅展示两个动作；真实请求必须精确包含全部 30 个业务字段。

### 10.3 草稿原件

```http
GET /api/v1/tasks/{task_id}/quote-drafts/{draft_id}/content?disposition=inline
GET /api/v1/tasks/{task_id}/quote-drafts/{draft_id}/content?disposition=attachment
```

### 10.4 正式提交与手动比较

```http
POST /api/v1/tasks/{task_id}/quote-drafts/{draft_id}/submit
POST /api/v1/tasks/{task_id}/runs
```

这是两个独立操作。第一个接口不会自动调用第二个接口。

## 11. 版本、幂等和安全规则

- 所有审核和提交请求都要携带 `Idempotency-Key`。
- 相同 key 和相同 payload 重放会返回同一结果。
- 相同 key 但 payload 不同会返回 `409 idempotency_key_reused`。
- `expected_draft_revision`、`field_id` 或 `field_version` 过期时返回 409。
- 409 时前端保留用户已输入内容，但必须刷新并核对最新版本。
- 跨任务或跨用户访问草稿、原件和审核接口均返回 404，不泄露草稿是否存在。
- 存储路径必须位于受控文件根目录，API 不暴露本地磁盘路径。

## 12. 常见错误与处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 费用状态为 `UNKNOWN` | 供应商报价没有提供足够信息 | 向供应商确认后选择真实状态；不要猜测为 0 |
| `KNOWN_AMOUNT` 但金额为空 | 状态与金额不完整 | 填写非负十进制金额 |
| `INCLUDED` 仍填写金额 | 可能重复加总 | 清空金额 |
| `FREE/NOT_APPLICABLE` 配非零金额 | 费用状态矛盾 | 清空金额或改为 0；若实际收费则改为 `KNOWN_AMOUNT` |
| 交期组只填部分字段 | 相对交期无法确定性计算 | 补齐天数、日历口径、交付语义和起算事件 |
| 更改币种后金额报错 | 金额 unit 与 `currency` 不一致 | 重新确认所有非空金额 |
| 提示字段版本变化 | 草稿被其他请求更新 | 保留当前输入，刷新最新草稿后重新核对 |
| 人工确认后仍无法正式提交 | 仍有硬阻塞，例如证据越界、字段集不完整或文件身份错误 | 重新解析或上传正确文件，不能用“确认全部”绕过 |

## 13. `full_flow_demo2` 操作提示

数据目录：

```text
data/generated/inputs/development/full_flow_demo2/
```

推荐依次上传 B、C、D、E、G 五份 PDF，对应供应商 ID：

```text
V9-SUP-B
V9-SUP-C
V9-SUP-D
V9-SUP-E
V9-SUP-G
```

按原文严格提取时，以下信息不应由系统从采购需求、旧 CSV、其他报价或参考答案中自动补齐：

| 供应商 | PDF 中需要人工补充的信息 |
| --- | --- |
| B | `tax_mode` |
| C | 运费真实状态和金额、`tax_mode` |
| D | `other_fees_status`、`tax_mode` |
| E | `manufacturer`、`other_fees_status`、`tax_mode` |
| G | `manufacturer`、`other_fees_status`、`tax_mode` |

仅在合成演示中，经演示操作员明确确认后，可使用以下演示输入：

- C 运费：`KNOWN_AMOUNT` / `"50.00"` / `SGD`。
- 未声明其他费用的演示报价：操作员确认后填 `NOT_APPLICABLE`，金额留空或为 `"0.00"`。
- 未声明税务口径的演示报价：操作员确认后填 `NOT_APPLICABLE`。
- E/G 缺少的制造商：操作员确认后填 `QQ Demo Components`。

这些值的来源必须是人工演示输入，并记录为 `USER_INPUT`；不得冒充 PDF 原文证据。在真实采购中，必须从供应商或获批的业务渠道获得真实值。

E 报价的单价必须保持：

```text
CURRENT: SGD 7.32
SUPERSEDED/AUDIT ONLY: SGD 7.40
```

五份报价全部完成 `30/30` 人工审核和正式提交后，再手动开始比较。

## 14. 启动与验证

修改后需要重启 API、Worker 和前端。本次改造没有新增数据库迁移。

### 终端 1：API

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m uvicorn supplier_comparison.backend.api:app \
  --host 127.0.0.1 --port 8000
```

### 终端 2：Worker

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m supplier_comparison.worker run-loop \
  --poll-interval 1
```

### 终端 3：前端

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM/frontend
npm run dev -- --host 127.0.0.1
```

访问：

```text
http://127.0.0.1:5173
```

自动化验证：

```bash
cd /Users/lc/Desktop/hackson/Team-QQFARM
.venv/bin/python -m pytest -q

cd frontend
npm test
npm run build
npm run lint
```

本次改造的最终本地结果：

- Python：`737 passed, 15 skipped`。
- Frontend Vitest/RTL：`19 passed`。
- TypeScript/Vite build：通过。
- ESLint：通过。
- `git diff --check`：通过。

跳过项为需要真实 PostgreSQL 或付费 Live Agent 环境的条件测试，不能把本地固定输出测试描述为真实外部模型验收。

## 15. 当前边界

- 人工确认是审核和数据修正，不等于采购审批。
- 系统不会自动联系供应商、议价、签约、下单或付款。
- 报价字段完整只代表允许进入后续计算；FX、Policy 或其他系统证据缺失时，供应商仍可能是 `PENDING`。
- 本版未增加 LLM 总体异常检查，最终准入由后端确定性规则负责。
- 已提交历史报价保持只读，新版本通过新草稿上传和审核。
