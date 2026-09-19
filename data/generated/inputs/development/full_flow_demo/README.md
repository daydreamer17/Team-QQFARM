# full_flow_demo

本目录是一套用于浏览器完整流程演示的自包含合成数据包。
其中的采购需求、供应商报价和采购制度均为虚构测试数据，不是真实商业资料。

## 推荐上传顺序

0. 在仓库根目录执行 `docker compose up -d --build postgres api worker`。
   Compose 会先把业务迁移升级到当前 Alembic head，并初始化 LangGraph checkpoint 表；
   `docker compose ps -a` 中 `migrate` 和 `checkpoint-setup` 应显示退出码 0。
1. 进入“规则资源库”，上传 `policy/electronics_procurement_full_flow.txt`。
   根据 `policy/upload_metadata.json` 填写 Policy 元数据，并使用
   `policy/reviewed_clauses.json` 核对所有自动拆分的条款和控制码，然后发布索引。
2. 创建采购任务，上传 `requirement/procurement_requirement.pdf`。
   使用 `requirement/confirmed_requirement.json` 核对自动填入的采购需求字段。
3. 在新任务中绑定刚刚发布的 Policy，分类选择 `Electronics`，地区选择 `SG`。
   Policy 索引版本会在发布时生成，请直接选择页面显示的版本，不要手动填写固定值。
4. 按 A、B、C 的顺序上传并正式提交三份报价。供应商编号记录在
   `manifest.json` 中。推荐上传 PDF；也可以在另一个新任务中使用对应 CSV 验证替代输入路径。
   同一任务不要同时上传同一报价的 PDF 与 CSV，以免把一种业务报价重复计数。
5. 正式提交全部报价后启动分析。流程暂停并要求人工输入时，使用
   `evaluation/reference/full_flow_demo/` 中仅供操作员查看的演示回答。

不要把 `evaluation/reference/full_flow_demo/` 挂载到运行时 Worker 或 Agent。
运行时必须只根据用户上传的文件解析字段、计算结果并形成推荐。

`validation_inputs.json` 列出补充边界数据和需要执行的操作，但不包含参考答案。
完整手工验收步骤见 `docs/FULL_FLOW_DEMO_VALIDATION.md`。
