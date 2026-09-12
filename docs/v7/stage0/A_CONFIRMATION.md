# V7 阶段 0：成员 A 确认记录

- 确认日期：2026-09-11
- 确认依据：本轮任务中提供的 V7 阶段 0 评审要求
- 数据字典：`data/contracts/quote_data_field.csv` 1.2.0

A 确认 V7 阶段 0：

1. V7 沿用 `quote_data_field.csv` 1.2.0 的字段业务含义。
2. OCR 不改变 `validation_status`、`origin` 或字段标准化规则。
3. A 负责 V7 development、calibration、holdout 输入及独立参考答案。
4. holdout 答案在 B 冻结并完成运行前保持不可见。
5. 参考答案覆盖全部 30 个 B 提取字段，失败和缺失不从分母删除。
6. `supplier_id` 等系统字段不计入 B 的 30 字段准确率。
7. 同意进入阶段 1 的页级路由和契约实现。

确认人：seiran-q（按本次任务请求记录）

## 口径补充

- `validation_status` 只使用 `EXTRACTED`、`VERIFIED`、`MISSING`、`CONFLICT`。
- `origin` 只使用 `DOCUMENT`、`USER_INPUT`、`USER_CORRECTION`、`DERIVED`。
- OCR 是证据取得方式，不是新的字段来源枚举。
- OCR 不确定时不得猜测金额、日期、数量、料号或后缀。
- 本记录确认数据与评测职责，不表示 B 的 OCR、解析器或门禁已经实现。

仓库中没有用户所引用的外部路径 `docs/v7/stage0/CONTRACT_REVIEW.md`；本记录仅依据本轮明确提供的评审文本建立，不替代未来正式公共契约文件。
