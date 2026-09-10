# Extraction reference sets

本目录只保存独立评测参考答案，不是运行时输入目录。部署、模型提示和 Agent 工具不得读取这里的内容。

参考答案由 A 创建和复核；B 只提供格式、哈希绑定和评测工具。V2 在 A 完成逐字段复核前必须保持
`review_status=DRAFT`，不得据此宣称字段准确率。正式计分时必须使用 `--require-approved`。

每份文档记录：

- 数据集、开发／留出划分和模板 ID；
- 字典版本、输入相对路径和 SHA-256；
- 权威的报价／文档版本；
- 每个 B 提取字段的期望状态、标准化值、单位和语义证据；
- `DRAFT` 或 `A_APPROVED`，批准状态必须记录复核人。

字段规则：

- `expected_status=null`：仅用于 `DRAFT` 中尚未复核的字段，值、单位和证据必须为空；
- `MISSING`：值、单位和证据必须为空；
- `EXTRACTED`：必须有原始表达、标准化值和语义证据；
- `CONFLICT`：必须记录冲突／歧义原文和语义证据，标准化值允许为空；
- 一个文档必须覆盖当前字典中全部 B 提取字段，不能用遗漏字段缩小评测分母。
- `A_APPROVED` 不允许存在 `expected_status=null` 的未复核字段。

校验命令：

```bash
PYTHONPATH=src .venv/bin/python scripts/validate_extraction_reference.py \
  --reference evaluation/reference/<reference-set>.json \
  --require-approved
```

当前 `mcu_demo_001_v2_supplier_b_csv_draft.json` 是由 A 的旧版 V2 示例迁移得到的
B-CSV 草稿：30 个字段均已列出，其中 13 个带待复核期望，17 个明确保持未复核。
它通过草稿结构和文件哈希校验，但会被 `--require-approved` 拒绝，不能用于正式计分。

重新迁移旧示例时使用：

```bash
PYTHONPATH=src .venv/bin/python scripts/migrate_v2_system_evidence.py \
  --legacy <system_evidence_v2.json> \
  --input data/generated/inputs/development/quote_V2/supplier_b_quote_v2.csv \
  --output evaluation/reference/mcu_demo_001_v2_supplier_b_csv_draft.json
```

留出答案一旦用于调试，该样本必须转为开发集；随后重新制作未见过的留出集。
