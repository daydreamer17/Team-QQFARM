# 成员 D 交接：后端编排、版本与恢复

本文说明如何迁移数据库、启动 FastAPI、执行一次性 worker，并复现 MCU-DEMO-001 的两次 LangGraph 中断。代码入口是 `supplier_comparison.backend.api:app`、`supplier_comparison.worker` 和 `supplier_comparison.checkpoints`。

## 1. 已实现范围

- FastAPI、同步 SQLAlchemy／psycopg、PostgreSQL 16 和 Alembic 业务表。
- 原始报价按 64 KiB 分块计算大小与 SHA-256，并写入不可覆盖的 ID 路径；保存文件版本、媒体类型和原始文件名元数据。
- LangGraph 使用 `graph_run_id` 作为 `thread_id`；checkpoint 只保存业务 ID、artifact ID 和冻结时间，不保存 requirement 正文、报价内容或磁盘路径。checkpoint 表由独立命令初始化。
- `ParsedInput`、`ExtractionBatch`、`ReviewEnvelope`、人工事件、输入快照和比较结果以不可变 JSON artifact 保存。
- 每份文档独立持久化最多 8 次模型调用预算；中断恢复复用已完成提取。
- 问题回答和字段纠正检查 `task_revision`；主动纠正创建新图运行。上传新报价会推进 revision，并使旧图、未决 issue、待执行 job 和旧当前结果指针失效；迟到 worker 不能改回当前状态。
- 本地身份固定来自后端配置 `TEST_USER_ID`，请求体中的同名字段不会覆盖操作者。

本轮不包括 React、正式登录／审批、RAG 运行、常驻 worker、任意崩溃窗口自动恢复和报告。OCR 代码保持兼容，但 `SUPPLIER_PDF_OCR_ENABLED=false` 是正式默认值。

## 2. 初始化本地环境

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

`.env` 中的模型 Key 只能保存在本机，不要提交。`SUPPLIER_MODEL_API_KEY_ENV` 保存环境变量名，真正的 Key 放在该环境变量中。本地开发使用 OpenAI-compatible 接口；部署到主办方环境时只替换 provider、base URL、model ID 和对应凭据。

启动数据库并初始化两类表：

```powershell
docker compose up -d --wait postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m supplier_comparison.checkpoints setup
```

Alembic 只管理业务表。LangGraph checkpoint 表由第二条 Python 命令管理；不要把 checkpoint 表加入 Alembic migration。

## 3. 启动方式

直接在本机运行 API：

```powershell
.\.venv\Scripts\python.exe -m uvicorn supplier_comparison.backend.api:app --host 127.0.0.1 --port 8000
```

也可以完全使用 Compose：

```powershell
docker compose build api worker
docker compose run --rm api python -m alembic upgrade head
docker compose run --rm api python -m supplier_comparison.checkpoints setup
docker compose up -d --wait api
```

检查地址：

- Swagger：`http://127.0.0.1:8000/docs`
- 存活：`GET http://127.0.0.1:8000/health/live`
- 数据库就绪：`GET http://127.0.0.1:8000/health/ready`

## 4. MCU-DEMO-001 输入

创建任务时在 Swagger 调用 `POST /api/v1/tasks`，请求头加入唯一的 `Idempotency-Key`，例如 `demo-create-001`，请求体如下：

```json
{
  "scenario_id": "MCU-DEMO-001",
  "requirement": {
    "manufacturer": "QQ Demo Components",
    "manufacturer_part_number": "QW-MCU9-DEMO",
    "package": "QFN-32",
    "revision": "R1",
    "condition": "NEW",
    "allow_substitutes": false,
    "base_unit": "piece",
    "required_quantity": 1000,
    "quantity_unit": "piece",
    "budget_amount": "8000.00",
    "currency": "SGD",
    "includes_shipping": true,
    "tax_mode": "NOT_APPLICABLE",
    "other_fees_required": true,
    "planned_order_date": "2026-09-14",
    "delivery_deadline": "2026-09-19",
    "delivery_location": "SG-DEMO-01",
    "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
    "secondary_preference": null
  }
}
```

记下返回的 `task_id` 和 `task_revision`。随后依次调用 `POST /api/v1/tasks/{task_id}/quotes` 上传三份 PDF。每次上传使用最新返回的 revision 和新的幂等键。

| supplier_id | 文件 |
| --- | --- |
| `SUP-022` | `data/generated/inputs/development/quote_V1/supplier_a_quote_v1.pdf` |
| `SUP-023` | `data/generated/inputs/development/quote_V1/supplier_b_quote_v1.pdf` |
| `SUP-024` | `data/generated/inputs/development/quote_V1/supplier_c_quote_v1.pdf` |

表单字段为 `expected_task_revision`、`supplier_id`、`is_synthetic=true` 和 `file`。原始文件名只作为元数据；服务端文件名由 ID 构成。

## 5. 两次 interrupt 与恢复

1. 调用 `POST /api/v1/tasks/{task_id}/runs`，提交当前 `expected_task_revision` 和新幂等键，记下 `job_id`。
2. 在新的终端执行一次 worker：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.worker run-job --job-id <job_id>
```

Compose 方式为：

```powershell
docker compose --profile worker run --rm worker python -m supplier_comparison.worker run-job --job-id <job_id>
```

3. worker 返回 `WAITING_INPUT` 和 `CONFIRM_MISSING` issue 后退出。调用 `POST /api/v1/tasks/{task_id}/issues/{issue_id}/answers`：

```json
{
  "expected_task_revision": 4,
  "answer": {"answer_type": "CONFIRM_MISSING"}
}
```

这里的 revision 以实际任务返回值为准。回答接口返回一个 `RESUME` job；再次用上面的 worker 命令执行它。
4. 第二次 worker 保存比较草稿后，在 `SHIPPING_AMOUNT` issue 处再次返回 `WAITING_INPUT`。此时结果应为：A 不可行、已知总成本 S$12,800；B 待确认、已知小计 S$6,800；C 可行、总成本 S$7,100；由于 B 仍为 pending，`final_recommendation_allowed=false`。
5. 回答第二个 issue：

```json
{
  "expected_task_revision": 5,
  "answer": {
    "answer_type": "SHIPPING_AMOUNT",
    "amount": "200.00",
    "currency": "SGD"
  }
}
```

6. 执行返回的第二个 `RESUME` job。最终 B 总成本为 S$7,000，并成为唯一推荐。草稿和最终结果均可通过 `GET /api/v1/tasks/{task_id}/results` 查询。

每个回答都必须使用新的 `Idempotency-Key`。使用相同 key 和相同请求会返回原响应；相同 key 配不同请求、旧 revision 或已解决 issue 返回 409。

## 6. 人工纠正与历史结果

通过 `POST /api/v1/tasks/{task_id}/quotes/{quote_id}/fields/{field_name}/corrections` 提交：

```json
{
  "expected_task_revision": 6,
  "raw_value": "S$0.00",
  "normalized_value": "0.00",
  "unit": "SGD",
  "reason": "Correct a confirmed extraction error."
}
```

纠正会创建 `CorrectionEvent`、新 task revision、新 graph run 和 START job。新运行复用三份已提取文档，不再次调用模型。旧结果仍可按 result ID 查询，但迟到旧运行不能写入 `current_result_id`。

## 7. API 清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/tasks` | 创建需求与任务 |
| GET | `/api/v1/tasks/{task_id}` | 当前状态、revision、current issue/job、snapshot 和 result ID |
| POST | `/api/v1/tasks/{task_id}/quotes` | 上传 PDF／注册 CSV |
| POST | `/api/v1/tasks/{task_id}/runs` | 创建一次性 START job |
| GET | `/api/v1/tasks/{task_id}/quotes/{quote_id}/fields` | 字段、审核状态和安全证据片段 |
| GET | `/api/v1/tasks/{task_id}/issues` | 当前及历史问题 |
| POST | `/api/v1/tasks/{task_id}/issues/{issue_id}/answers` | 提交判别联合类型回答 |
| POST | `/api/v1/tasks/{task_id}/quotes/{quote_id}/fields/{field_name}/corrections` | 人工纠正并创建新图运行 |
| GET | `/api/v1/tasks/{task_id}/results` | 当前及历史结果 |
| GET | `/api/v1/tasks/{task_id}/results/{result_id}` | 读取指定历史结果 |

错误统一为 `{error: {code, message, details, request_id}}`。API 和中断内容不会返回磁盘路径、完整报价、provider body、Authorization 或 API Key。

## 8. 自动化验收

无模型 Key 时先运行确定性验收；它通过 `FixedOutputAdapter`／固定解析边界验证业务编排，不访问网络：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:RUN_POSTGRES_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest tests\backend\test_postgres_recovery.py -q
Remove-Item Env:RUN_POSTGRES_TESTS
```

该 PostgreSQL 测试文件会用四个独立 processor／checkpointer 实例执行两次中断、恢复、最终发布和主动纠正，验证三份文档只提取一次；同时验证两个同 revision 上传并发时只有一个成功，另一个得到版本冲突。

模型 Key 配置完成后，再用第 4–5 节的 V1 PDF 走一次真实模型 smoke test。当前固定输出测试通过只说明编排和规则通过，不能作为真实模型提取已验收的证据。

2026-09-12 已使用本地真实模型完成一次第 4–5 节的完整端到端验收：三份 PDF 各调用模型一次，两次 interrupt 后分别重启 API 并由新 worker 恢复，最终 Supplier B 为 S$7,000 且唯一推荐；Compose 下线并保留卷后重建，任务、结果、checkpoint 和三份报价文件仍可验证。该记录只代表本地环境，不代表 Lightsail／主办方 Claude API 已验收。

V1–V7 各数据版本的真实模型 runner、离线评分命令和 holdout 隔离规则见 `docs/guide/guide_V1_V7_TESTING.md`。

注册 CSV 要求文件内 authority ID 与任务、quote 和 document 上下文完全一致。API 会为上传对象生成这些 ID，因此当前主演示使用 PDF；CSV 路径由 B 的模块测试和 D 的固定集成测试覆盖。若第二周需要从 API 上传标准 CSV，应先加入服务端 CSV 模板导出或 ID 预留协议。

## 9. 持久化检查

先完成一次中断，然后执行：

```powershell
docker compose down
docker compose up -d --wait api
```

不要加 `-v`。重启后查询原任务、问题和结果，并用原 `job_id` 对应的恢复作业继续执行。`database_data` 保存业务和 checkpoint，`quote_files` 保存上传文件。

## 10. 常见问题

- `database_unavailable`：检查 `docker compose ps` 和 `.env` 的 `DATABASE_URL`；本机使用 `localhost`，Compose 内部固定使用 `postgres`。
- `relation ... does not exist`：重新执行 Alembic upgrade；checkpoint 表缺失则执行 checkpoint setup。
- `idempotency_key_reused` 或 `task_revision_conflict`：读取最新任务状态，换新 key，并使用当前 revision；不要用换 key 绕过一次有效操作。
- `pdf_page_requires_ocr`：输入是扫描页且 OCR 默认关闭。换用原生文本 PDF；不要把它当成全字段缺失。
- 模型配置错误：确认 `SUPPLIER_MODEL_*` 和 `SUPPLIER_MODEL_API_KEY_ENV`，但不要把 Key、provider 原响应或诊断文件放进日志、文档或 Git。
- worker 失败：查询 job 和 issue 状态；Week1 只保证成功写入 interrupt 后可跨进程恢复，不保证任意节点崩溃自动续跑。
- 未预期的 worker 异常只保存并输出通用错误说明；provider 原始异常、Authorization 和凭据不会写入 job 错误正文或 CLI traceback。
