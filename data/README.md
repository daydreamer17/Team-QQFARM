# Data 目录

交付版本只保留当前运行、演示和回归测试仍会使用的数据。不要再以 `quote_V1`、`quote_V2` 之类的版本号创建整套数据；新样本应按用途放入稳定目录。

## 目录职责

- `contracts/`：字段契约、Schema 和接口约束。
- `policies/`：可发布的采购制度及已审核条款。
- `examples/policy_rag/`：Policy RAG 的最小输入示例。
- `source/`：上游原始采购数据、许可证和来源说明。
- `generated/demos/`：可由用户按 README 完整走通的当前演示包。
- `generated/fixtures/extraction/`：解析器边界、版式、OCR、CSV 等专项测试夹具，不作为用户演示数据。
- `generated/fixtures/compliance/`：制度核验材料和闭环专项夹具。
- `generated/supplier_history/`：由原始采购数据确定性生成的供应商历史快照。

## 当前入口

- 完整流程：`generated/demos/full_flow_demo4/`
- 调查 Agent：`generated/demos/agent_investigation_demo/`
- 完整流程生成器：`generate_full_flow_demo4.py`
- 调查 Agent 生成器：`generate_agent_investigation_demo.py`

`generated/fixtures/` 下的内容只用于自动化测试。不要把参考答案或留出集挂载到运行时 Agent 可访问的位置。
