# 集中审核与批量修改：第三部分交付指引

本轮完成后端协议、路由与回归验证，不包含前端页面或自主调查执行器。复用已有 `ReviewEnvelope`、字段纠正服务及 LangGraph，不重新实现 B 的审核规则。

## 1. 现在的流程

```text
所有报价完成字段审核
  → GET review 一次列出各家当前问题和原文件位置
  → 用户核对原文件，修改多个字段
  → POST fields/corrections 一次提交
  → 同一事务保存，任务版本只增加一次，旧推荐失效
  → worker 运行返回的 job_id
  → 所有报价统一重审 → 决策影响重算 → 比较与现有制度门禁
```

支持跨供应商批量修改。同一批中的一个字段无效或版本冲突时，整个提交不保存；能发现的字段错误集中返回。重新审核可能发现修改引入的新问题，不保证任意修改只需一轮。

## 2. 集中查看当前问题

`GET /api/v1/tasks/{task_id}/review`

| 返回字段 | 含义 |
| --- | --- |
| `task_revision` | 本次提交必须使用的任务版本 |
| `review_pending` | 尚未完成当前输入的审核；不能把空问题列表理解成通过 |
| `quotes` | 各家报价的当前字段、字段版本、审核状态、文本证据和原文件名 |
| `problems` | 尚未解决的审核发现，以及已生成影响报告中的比较输入问题 |
| `blocking_problem_count` | 当前返回的问题中需要处理的数量，不是最终发布许可 |

每个问题带 `quote_id`、`field_name`、`field_version`、`codes`、`message`、`needs_resolution`、`resolution`、`document_id` 和 `original_filename`。PDF 证据提供页码，CSV 证据提供行号/列名；通过字段的 `source_refs` 与 `evidence_sources.source_id` 关联。无需展示图片，用户打开指定原文件核对即可。

缺失字段可能没有局部证据：用户需要查看整份文件，系统不伪造引用。身份/证据等系统问题并非都能通过改表单解决，按 `resolution` 区分字段修改与重新提取/系统修复。

若比较阶段尚未生成影响报告，本接口只能返回已完成的字段审核问题，不编造尚不可判断的比较问题。报告会保留“已证明不影响推荐”的未知字段，`needs_resolution=false`；不把它们填成零或审核通过。已有业务不可行原因（超预算、超期）不是字段填写错误，不应通过本接口自动更改采购约束。

## 3. 一次提交多个字段

`POST /api/v1/tasks/{task_id}/fields/corrections`

请求头提供新的 `Idempotency-Key`。请求体示例（ID 和版本替换为真实返回值）：

```json
{
  "expected_task_revision": 3,
  "corrections": [
    {
      "quote_id": "quote_supplier_b",
      "field_name": "shipping_fee_status",
      "expected_field_version": 1,
      "raw_value": "KNOWN_AMOUNT",
      "normalized_value": "KNOWN_AMOUNT",
      "unit": null,
      "reason": "已向供应商确认运费金额"
    },
    {
      "quote_id": "quote_supplier_b",
      "field_name": "shipping_fee_amount",
      "expected_field_version": 1,
      "raw_value": "SGD 200.00",
      "normalized_value": "200.00",
      "unit": "SGD",
      "reason": "已向供应商确认运费金额"
    }
  ]
}
```

金额使用字符串，不传浮点数；枚举和字段类型按数据字典填写，而非参考答案。允许 1–100 项，同一报价的同一字段不能重复。字段版本和当前文件身份均由服务器检查，审核人使用服务器登录身份，不能自行指定。

成功返回 `202`、新 `task_revision`、`graph_run_id` 和 `job_id`。这表示纠正已保存并排队，**不代表审核通过**。请求形状/类型错误返回 `422`；任务/字段/文件版本冲突返回 `409`；无权限或不存在的报价返回 `404`。批量字段错误在 `error.details.errors` 中列出。

完全相同的请求重试使用相同幂等键，只返回同一个任务；更改请求必须使用新键。原单字段路由仍兼容。

## 4. 运行重审

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe -m supplier_comparison.worker `
  run-job --job-id "替换为返回的job_id"
```

完成后重新读取 `GET review` 与结果接口。新任务复用已有提取批次，不重复调用模型提取；但重新运行完整字段审核和比较。多次修改会保留此前仍适用的人工纠正审计记录。未修改供应商也参与重审；历史结果和纠正记录不覆盖，旧结果不再是当前推荐。

当前通用批量接口提交的是**字段纠正**，不是任意 issue 的批量回答。原 `CONFIRM_MISSING`／`SHIPPING_AMOUNT` 单问题确认流程、制度问题重试流程继续保留。以后 Agent 的集中补问可复用本接口，但调查、批准、采购需求修改和制度冲突裁决不在本轮范围。

## 5. 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/backend/test_api.py tests/backend/test_workflow.py
.\.venv\Scripts\python.exe -m pytest -q
```

固定测试无需 API key，覆盖集中展示、跨报价修改、一次版本增长、原子拒绝、幂等、权限、过期版本、连续修改的审计继承、旧结果失效，以及错误枚举仍不能通过重审。默认跳过的 PostgreSQL 专项不算已验证真实数据库。
