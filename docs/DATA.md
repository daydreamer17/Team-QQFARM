# 数据、证据与演示集

本文描述当前交付版本的数据组织、运行时边界和演示数据。目录入口另见 [`data/README.md`](../data/README.md)。所有演示供应商、物料、报价、制度材料和采购历史均为合成数据。

## 1. 目录职责

```text
data/
├── contracts/                  字段字典、Schema 和接口约束
├── policies/                   可发布的采购制度与集合清单
├── examples/policy_rag/        Policy RAG 最小示例
├── source/                     上游原始数据、许可证和来源说明
└── generated/
    ├── demos/                  当前可人工走通的演示包
    ├── fixtures/extraction/    解析器边界与格式测试夹具
    ├── fixtures/compliance/    制度核验专项夹具
    └── supplier_history/       确定性生成的供应商历史发布

evaluation/
└── reference/                  参考答案和离线验收数据
```

`data/generated/demos/` 可以作为用户上传输入；`evaluation/reference/` 只能供测试程序和验收人员读取，不得上传或挂载给运行时 Agent。

## 2. 报价字段契约

报价审核字段以 `data/contracts/quote_data_field.csv` 和后端 `GET /api/v1/quote-field-schema` 为准，前端不维护重复字段表。当前字段覆盖：

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

字段候选至少包含原始值、标准化值、单位、校验状态、来源引用以及适配器/提示词版本。业务 ID 和报价版本由后端生成或校验，不能信任模型自报。

### 状态与来源

| 维度 | 值 | 含义 |
| --- | --- | --- |
| `validation_status` | `EXTRACTED` | 已抽取，尚未达到确定性核验条件 |
|  | `VERIFIED` | 已通过结构、范围、单位和证据核验 |
|  | `MISSING` | 原文未提供或无法可靠识别 |
|  | `CONFLICT` | 同一字段存在相互冲突的候选 |
| `origin` | `DOCUMENT` | 来自上传原件 |
|  | `USER_INPUT` | 用户补充的新事实 |
|  | `USER_CORRECTION` | 用户纠正模型或解析结果 |
|  | `DERIVED` | 由明确规则和已确认输入计算 |

费用状态为 `KNOWN_AMOUNT | FREE | INCLUDED | NOT_APPLICABLE | UNKNOWN`。`UNKNOWN` 的金额必须为空，不能用 0 代替。金额使用十进制字符串；包装数量、订购倍数和 MOQ 是三个不同概念。

## 3. 文件与证据

- PDF 证据包含文件/报价版本、哈希、页码、稳定文本块 ID、原文片段和可用坐标。
- CSV 证据包含文件/报价版本、哈希、行号和列名。
- 模型只能引用本次解析生成且属于正确文件版本的来源 ID。
- 人工纠正保留原提取值；派生值记录输入字段和规则版本。
- 文件替换生成新版本，旧文件和引用继续用于历史快照。

## 4. 制度与合规材料

制度数据分为三层：

1. 原始制度文件及范围元数据；
2. 人工审核后的条款、控制码和可执行参数；
3. 发布后的 Embedding 索引及版本清单。

供应商证明材料是任务级、供应商级的版本化证据。当前闭环支持供应商准入、RoHS 合规和金额审批等控制项；材料事实由解析器辅助填写，必须由用户核对确认。合规 assessment 同时冻结制度引用、材料引用、评估时间和确定性状态。

## 5. 当前完整演示：full_flow_demo4

入口：`data/generated/demos/full_flow_demo4/`。

该数据集包括：

- 一份采购需求及确认参考；
- Electronics/SG 主制度和一个不匹配范围的反例制度；
- 四家供应商的 PDF 与等价 CSV 报价；
- 首轮供应商准入/RoHS 材料及三份替换材料；
- 缺运费、冲突价格、错误料号、MOQ、并列、提示注入等隔离变体；
- 与 `2026-08-06-v1` 合成供应商历史发布的绑定信息。

主场景固定评估日期为 2026-11-02。PDF 和 CSV 是两条等价输入路径，同一任务不要混传。每个 `variants/` 用例必须创建新任务，只替换指定供应商的一份报价。

完整人工步骤见 [TESTING.md](TESTING.md)，具体上传顺序见数据包内的 `README.md`。离线预期结果位于 `evaluation/reference/full_flow_demo4/`，不得提供给运行时 Agent。

## 6. 供应商历史

运行时历史发布位于 `data/generated/supplier_history/mcu9/{dataset_version}/`，由 `data/source/purchase_orders.csv` 确定性生成。发布包含内容哈希、manifest 哈希、统计期间、样本门槛、评级方法版本和合成数据标识。

任务通过 `task_history_bindings` 固定具体版本。旧结果必须读取冻结快照，不能用最新历史数据补齐。

## 7. 数据维护规则

- 新的用户演示包放入 `generated/demos/`；专项边界样本放入 `generated/fixtures/`。
- 不再以 `quote_V1`、`quote_V2` 等版本号复制整套数据。
- 生成数据必须有可复现生成器、固定种子或内容哈希，以及明确的合成数据标识。
- 开发集、校准集和留出集隔离；根据留出结果调试过的样本不再算最终留出样本。
- 参考答案、模拟回答和未遮蔽真值不得进入运行时目录。
- 修改生成文件后必须同步 manifest 的文件大小和哈希，或使用对应生成器重建。
