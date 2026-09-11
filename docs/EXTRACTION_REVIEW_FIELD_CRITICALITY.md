# 提取结果关键字段与审查规则

## 1. 目的

本文件用于规定报价提取完成后，哪些字段必须严格审查，以及哪些字段暂时不影响第一周的供应商比较。

基本原则：

- 关键字段不能猜测。不确定时应标记为 `MISSING` 或 `CONFLICT`，并让报价进入 `PENDING`。
- 关键字段只有在值、单位、原文证据和来源状态均正确时，才能进入计算。
- 非关键字段可以暂不人工复核，但不能参与成本、可行性和推荐计算。
- 原始模型输出必须保留。人工纠正应保存为新版本，不能覆盖原始提取文件。

## 2. 始终关键字段

以下16个字段错误时，可能导致供应商、规格、数量、成本或报价有效性判断错误，因此进入下游前必须100%正确。

| 字段 | 中文含义 | 主要影响 |
| --- | --- | --- |
| `supplier_name` | 供应商名称 | 防止报价关联到错误供应商 |
| `manufacturer` | 制造商 | 判断商品是否符合采购要求 |
| `manufacturer_part_number` | 制造商料号 | 判断是否买错型号 |
| `package` | 器件封装 | 防止把 QFN-32 与 QFN-48 混用 |
| `condition` | 产品新旧状态 | 防止新品与二手品混比 |
| `currency` | 报价币种 | 决定金额的货币口径 |
| `unit_price` | 单价 | 直接影响商品成本 |
| `price_basis_quantity` | 单价覆盖数量 | 区分每颗、每100颗等计价方式 |
| `price_basis_unit` | 计价单位 | 决定价格单位换算 |
| `order_multiple_units` | 采购步长 | 决定实际采购数量 |
| `moq_quantity` | 最小起订数量 | 决定最低采购数量 |
| `moq_unit` | MOQ单位 | 区分颗、盘、盒等单位 |
| `shipping_fee_status` | 运费状态 | 决定运费是否已知、免费或已包含 |
| `other_fees_status` | 其他费用状态 | 防止遗漏必要费用 |
| `tax_mode` | 税费口径 | 防止含税与未税报价混比 |
| `valid_until` | 报价有效截止日期 | 判断报价是否已经过期 |

## 3. 条件关键字段

以下10个字段只有在对应业务条件出现时才参与计算。一旦参与计算，也必须100%正确。

| 字段 | 中文含义 | 变成关键字段的条件 |
| --- | --- | --- |
| `revision` | 产品版本 | 采购需求指定版本时 |
| `packaging_type` | 销售包装方式 | MOQ或价格按盘、盒、包计算时 |
| `units_per_pack` | 每个包装内的数量 | 需要把盘、盒、包转换为颗时 |
| `shipping_fee_amount` | 运费金额 | `shipping_fee_status=KNOWN_AMOUNT` 时 |
| `other_fees_amount` | 其他费用金额 | 存在明确额外费用时 |
| `lead_time_days` | 相对交期天数 | 需要根据天数推算到货日期时 |
| `day_basis` | 自然日或工作日 | 使用相对交期时 |
| `delivery_semantics` | 到货或发运 | 判断是否满足到货期限时 |
| `start_event` | 交期起算事件 | 交期从下单、付款等事件开始计算时 |
| `quote_date` | 报价日期 | 有效期采用“报价后若干天”等相对表达时 |

## 4. 当前 MCU-DEMO-001 的条件关键字段

- `revision`：采购需求指定 R1，因此三家都必须正确。
- `packaging_type`、`units_per_pack`：A、C 按盘报价和起订，因此必须正确；B 直接按颗计价和起订，可以保持 `MISSING`。
- `shipping_fee_amount`：C 明确运费 S$500，因此必须正确；B 初始报价未提供运费，应保持 `MISSING` 并进入 `PENDING`。
- `lead_time_days`、`day_basis`、`delivery_semantics`、`start_event`：当前报价通过相对天数表达交期，因此三家都必须正确。
- `other_fees_amount`：费用状态明确为 `NOT_APPLICABLE` 时不参与加总；如果出现非零金额，则必须检查冲突。
- `quote_date`：当前已有明确的 `valid_until`，因此报价日期不直接决定有效期计算。

## 5. 非关键字段

以下4个字段当前不参与规格、数量、成本、交期和最终推荐计算，可以不要求模型达到100%提取正确率。

| 字段 | 中文含义 | 当前处理方式 |
| --- | --- | --- |
| `supplier_country` | 供应商所在国家 | 可展示，但不参与当前评分 |
| `category` | 商品类别 | 可从采购需求关联，但不能冒充报价原文 |
| `item` | 商品名称 | 精确料号和规格齐全时可以缺失 |
| `payment_terms` | 付款条件 | 与价格或交期无联动时可以缺失 |

非关键字段出现错误时不会阻断第一周比较，但应满足以下要求：

- 不得被下游规则用于计算或筛选。
- 未核验的值不能显示为已确认事实。
- 发现错误后仍应记录并在后续版本纠正。

## 6. 系统与审计字段

`supplier_id` 由系统上下文提供，不依赖模型从报价文件提取，但必须100%正确。

以下系统与证据字段虽然不直接计算成本，也必须准确：

```text
quote_id
document_id
document_sha256
source_refs
origin
validation_status
quote_version
document_version
```

这些字段用于证明某个结果来自哪份文件、哪个版本、哪段原文，以及是否经过人工补充或纠正。

## 7. 关键字段通过条件

关键字段必须同时满足：

```text
normalized_value 正确
+ unit 正确
+ source_refs 能够支持该值
+ origin 与 validation_status 正确
```

具体要求：

- `normalized_value` 必须符合数据字典规定的类型和枚举范围。
- `unit` 必须与金额、数量或时间值对应。
- `source_refs` 必须能够定位到支持该值的原文，不能引用无关文字。
- 文档提取值使用 `origin=DOCUMENT`。
- 人工补充值使用 `origin=USER_INPUT`。
- 人工纠正值使用 `origin=USER_CORRECTION`。
- `validation_status=MISSING` 时，值、来源和证据都必须为空。

任何一项无法确认时，该字段不得进入正式计算，相关报价应进入 `PENDING`。

## 8. 审查与准确率口径

关键字段的目标不是要求模型每次都强行填写，而是要求进入下游的已接受值100%正确。

建议使用以下验收口径：

| 指标 | 验收要求 |
| --- | --- |
| 关键字段已接受值正确率 | 100% |
| 非法类型和非法枚举拦截率 | 100% |
| 关键字段静默错误数 | 0 |
| 无法确认的关键字段 | 必须进入 `MISSING`、`CONFLICT` 或 `PENDING` |
| 人工纠正记录 | 必须保留原值、新值、依据和来源 |

字段定义以 `data/contracts/quote_data_field.csv` 为准；本文件负责说明审查等级和下游使用规则。
