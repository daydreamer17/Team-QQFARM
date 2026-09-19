# full_flow_demo 手工验收指南

## 结论

`full_flow_demo` 可以验证一条完整主链路：采购需求上传、三份报价解析、人工补充缺失费用、确定性比较、Policy 导入与检索、结果页、Summary 和审计记录。

它不能单独证明系统全面可行。主数据只有一个需求、三家供应商和一个成本优先场景。完整验收还要复用 V4、V6、V7、V9，并执行 Worker 恢复、重复上传、版本冲突和 Policy 故障等操作。补充输入清单位于 `data/generated/inputs/development/full_flow_demo/validation_inputs.json`。

## 测试前准备

1. 确认 `.env` 中模型、Embedding、Rerank 和数据库配置可用。真实解析、需求提取、RAG 与 Summary 会产生模型调用费用。
2. 在仓库根目录启动服务：

   ```powershell
   docker compose up -d --build postgres api worker
   docker compose ps -a
   ```

   `migrate` 和 `checkpoint-setup` 必须显示退出码 0；它们完成后 API 和 Worker 才会启动。
3. 另开终端启动前端：

   ```powershell
   Set-Location frontend
   npm.cmd run dev -- --host 127.0.0.1
   ```

4. 打开 `http://127.0.0.1:5173`。确认 API、PostgreSQL 和 Worker 都为运行状态。
5. 每轮使用新任务。PDF 与对应 CSV 是同一业务报价的两种输入格式，不要在主链路任务中同时上传。

## A. 主链路 PDF 验收

1. 在“规则资源库”上传 `policy/electronics_procurement_full_flow.txt`。
2. 根据 `policy/upload_metadata.json` 填写元数据。
3. 使用 `policy/reviewed_clauses.json` 核对条款和控制码，发布 Policy 索引。
4. 新建任务并上传 `requirement/procurement_requirement.pdf`。
5. 等待需求解析完成，用 `requirement/confirmed_requirement.json` 逐字段核对候选值和证据，再确认需求。
6. 创建任务时绑定刚发布的 Policy，分类选 `Electronics`，地区选 `SG`。
7. 依次上传 A、B、C 三份 PDF。供应商编号从 `manifest.json` 复制。
8. 每份报价等待自动审核完成。确认所有关键值有来源，缺失字段没有被填成 0。
9. 正式提交三份报价并启动分析。
10. 出现人工问题时，仅由操作员查看 `evaluation/reference/full_flow_demo/` 的回答。不要把该目录上传、挂载或复制给 Worker/Agent。
11. 回答问题后，确认同一个持久化流程继续运行，而不是新建一条互不关联的分析。
12. 检查以下页面：集中审核、调查记录、决策比较、入选差距、合规、Summary、版本/审计。

主链路通过条件：

- 需求解析完成，候选值、人工确认值和来源证据可追踪。
- 三份报价均形成正式版本；缺失运费保持未知，人工回答前不参与最终推荐。
- A 显示明确硬约束失败；B、C 在信息完整后进入可行比较；最终成本、原因码和推荐与独立参考一致。
- Policy 三个控制项均返回可定位引用。该结果只证明制度检索，不等于系统已经证明供应商获批或 RoHS 合规。
- Summary 不得改写确定性金额、供应商或原因码；审计页能看到 revision、人工修改和 Job 历史。

## B. CSV 替代路径

新建另一个任务，重复 A 的流程，但只上传 `quotes/` 下三份 CSV。不要同时上传对应 PDF。

通过条件：三份 CSV 都能识别其已注册表头并进入同一审核、计算和审计链路；PDF 与 CSV 的最终业务事实应一致。CSV 替代路径仍可能调用模型，不应当成零成本解析。

## C. 必须补跑的边界验证

| 编号 | 数据或操作 | 验证目标 |
| --- | --- | --- |
| C1 | 只上传 C，新建单报价任务 | 审核通过后能完成结果；不伪造“多供应商比较” |
| C2 | 只上传 A，新建单报价任务 | 无可行报价时明确返回无推荐，并列出失败原因 |
| C3 | 同一任务先后上传 B 的 PDF 和 CSV | 系统应提示重复业务报价；若被当成两家或两份独立报价，登记缺陷 |
| C4 | V9 A-E 五份一组；再尝试第六份 | 五份可处理；第六份必须被明确拒绝。若接受，登记上限未实现缺陷 |
| C5 | V9 成本组和边界组 | 验证并列、延期、超预算、封装不匹配；先读 V9 README 区分已实现与目标夹具 |
| C6 | V4 无效文件 | 空白、损坏、加密、超页数、超大小、错误 CSV 表头都应给出具体错误码，不创建正常报价 |
| C7 | V6 冲突和 prompt injection | 冲突必须进入审核；文档指令不得改变规则、权限或结论 |
| C8 | V7 development 数据 | OCR 关闭时扫描页显式失败；显式开启时进入 OCR 证据与人工审核，不能静默直通 |
| C9 | Worker 运行中停止再启动 | PENDING/RUNNING 状态可恢复；不得要求用户逐个复制 Job ID 执行一次性命令 |
| C10 | 两个浏览器标签同时编辑同一任务 | 旧 revision 返回 409，刷新后不会重放旧内容 |
| C11 | 不绑定、正确绑定、错误范围或不可用 Policy | 三种状态有清晰差异；检索失败不可伪装成“合规” |
| C12 | Agent 关闭与开启各跑一次 | 关闭时传统流程仍可完成；开启时调查 Case、工具记录和停止原因可审计 |

## D. Agent 开关验证

当前 Compose Worker 默认关闭调查 Agent。测试前先停掉常驻 Worker，避免两个 Worker 同时领取 Job：

```powershell
docker compose stop worker
docker compose run --rm -e SUPPLIER_AGENT_ENABLED=true worker `
  python -m supplier_comparison.worker run-loop --poll-interval 1
```

此终端保持运行。验收结束后按 `Ctrl+C`，再恢复默认 Worker：

```powershell
docker compose up -d worker
```

Agent 通过条件：调查只读取当前任务和当前 revision，工具调用有记录；它可以整理问题和证据，但不能自行写入事实、批准或下单。

## E. 自动检查与记录

开发依赖安装完整时执行：

```powershell
python -m pytest tests/backend/test_full_flow_demo_dataset.py -q
python -m pytest tests/extraction/test_v4_boundaries.py tests/extraction/test_pdf_layout.py -q
Set-Location frontend
npm.cmd run lint
npm.cmd run build
```

每个用例记录 `PASS`、`FAIL` 或 `PENDING`，并保存 Task ID、Job ID、Result Artifact ID、截图或错误响应。只有真实浏览器操作、真实 Worker、真实数据库和实际模型调用全部留有证据时，才可声称端到端通过。
