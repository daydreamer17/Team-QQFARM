# 三份开发 PDF 人工修正前字段验收

日期：2026-09-10

环境：LOCAL／SiliconFlow／`deepseek-ai/DeepSeek-V4-Flash`

数据：团队合成开发报价，不代表真实供应商

## 结论

- A：30／30 通过。
- B：30／30 通过确认后的 PDF 口径；`other_fees_amount` 的模型值 `"0"` 经确定性规则格式化为 `"0.00"`。
- C：29／30 通过；`shipping_fee_status` 实际为契约外的 `PAID`，预期 `KNOWN_AMOUNT`。
- 合计：89／90，字段匹配率 0.9889。
- 三份结果均为人工修正前真实模型结果；每份调用 1 次且无重试。Decimal 金额按数值比较，来源定位由确定性代码检查。

机器状态 `PASSED` 只代表当次运行的最小结构检查。逐字段验收结果以本报告及同目录的 `development_pdf_field_review.json` 为准。

## 逐字段矩阵

| 字段 | A | B | C |
| --- | --- | --- | --- |
| supplier_name | PASS | PASS | PASS |
| supplier_country | PASS | PASS | PASS |
| category | PASS（PDF 缺失） | PASS（PDF 缺失） | PASS（PDF 缺失） |
| item | PASS | PASS | PASS |
| manufacturer | PASS | PASS | PASS |
| manufacturer_part_number | PASS | PASS | PASS |
| package | PASS | PASS | PASS |
| revision | PASS | PASS | PASS |
| condition | PASS | PASS | PASS |
| currency | PASS | PASS | PASS |
| unit_price | PASS | PASS | PASS |
| price_basis_quantity | PASS | PASS | PASS |
| price_basis_unit | PASS | PASS | PASS |
| packaging_type | PASS | PASS（确认缺失） | PASS |
| units_per_pack | PASS | PASS（确认缺失） | PASS |
| order_multiple_units | PASS | PASS | PASS |
| moq_quantity | PASS | PASS | PASS |
| moq_unit | PASS | PASS | PASS |
| shipping_fee_status | PASS | PASS（确认缺失） | **FAIL：PAID，应为 KNOWN_AMOUNT** |
| shipping_fee_amount | PASS | PASS（确认缺失） | PASS |
| other_fees_status | PASS | PASS | PASS |
| other_fees_amount | PASS | PASS（确定性 0 → 0.00） | PASS |
| tax_mode | PASS | PASS | PASS |
| lead_time_days | PASS | PASS | PASS |
| day_basis | PASS | PASS | PASS |
| delivery_semantics | PASS | PASS | PASS |
| start_event | PASS | PASS | PASS |
| payment_terms | PASS | PASS | PASS |
| quote_date | PASS | PASS | PASS |
| valid_until | PASS | PASS | PASS |

## 调用记录

| 供应商 | 调用／重试 | Prompt tokens | Completion tokens | Total tokens | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| A | 1／0 | 3,398 | 2,887 | 6,285 | 30／30 |
| B | 1／0 | 3,358 | 2,717 | 6,075 | 30／30（确认口径） |
| C | 1／0 | 3,411 | 2,901 | 6,312 | 29／30 |

## 验收边界

- 参考基线为 A 的冻结开发 `quotes.csv`，并应用本轮已确认的 PDF 专属缺失和 Decimal 规则；参考数据没有进入模型请求。
- 引用 ID、文件版本和原文片段匹配已经由 B 的确定性校验完成；来源的最终业务语义签字仍归 A。
- C 的失败字段保留在原始结果中，不静默改写。代码已经拒绝契约外费用状态并增强提示；是否再次调用 C 需另行确认。
