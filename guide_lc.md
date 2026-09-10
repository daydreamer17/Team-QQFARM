# 成员 B 第一周开发与交接说明

日期：2026-09-10

成员：LC（成员 B）

范围：报价 PDF／CSV 解析、模型适配、来源证据和候选字段

## 1. 本次更新概述

本次为成员 B 增加了可运行的 Python 模块：读取一种英文文本 PDF 开发版式和冻结 CSV 模板，生成稳定来源，调用固定输出或真实模型适配器，输出统一候选字段，并进行结构、来源和确定性格式校验。

本次使用 SiliconFlow 的 `deepseek-ai/DeepSeek-V4-Flash` 对 A、B、C 三份合成开发 PDF 做了真实本地 API 测试。逐字段开发对照结果为 89／90；A 为 30／30，B 按确认口径为 30／30，C 为 29／30。

本次不包含 C 负责的采购计算，也不包含 D 负责的 FastAPI、数据库、版本和 LangGraph 流程。

## 2. 主要做了什么

- 建立文件、来源、候选字段、模型运行和归一化事件的 Pydantic 契约。
- 使用 `pdfplumber` 解析机器生成的英文 PDF，保存页码、文本块 ID、坐标、文件哈希和解析器版本。
- 严格解析 A 冻结的宽表 CSV；错误表头、身份或版本会被拒绝。
- 实现固定输出适配器和 OpenAI-compatible HTTP 适配器。
- 限制每阶段最多 3 次尝试、每逻辑运行最多 8 次调用；恢复时调用计数不能重置。
- 校验字段集合、来源 ID、文件版本和引用原文；缺失字段不允许伪造来源。
- 使用按状态区分的模型 Schema：`EXTRACTED`、`MISSING`、`CONFLICT` 具有不同约束，模型不能自报 `VERIFIED`。
- 对当前 SGD 费用做确定性两位小数格式化，并保留归一化前后值和规则版本。
- 对运费和其他费用状态实施固定枚举校验。
- 增加单元测试、真实运行脚本和逐字段开发验收脚本。

## 3. 实现思路

处理顺序如下：

```text
文件限制与 SHA-256
→ PDF／固定 CSV 解析
→ 稳定来源 ID 和位置
→ 固定或真实模型适配器
→ Pydantic 结构化输出校验
→ 确定性金额格式化
→ 字段集合、枚举和来源校验
→ ExtractionBatch 交给下游
```

关键边界：

- `task_id`、报价／文件版本和业务身份由调用方提供，不信任模型或文件自报。
- 模型只生成候选，不能生成 `VERIFIED`，也不能执行采购计算。
- `MISSING` 的值和来源必须为空；`EXTRACTED` 必须有原始值、标准化值和来源。
- 网络超时、429 和 5xx 才允许有限重试；无效 JSON 或 Schema 输出立即停止，避免重复付费猜测。
- `raw_value` 保留文档表达；`normalized_value` 保存标准表达；格式调整不会改变 `origin=DOCUMENT`。
- B 的 “ordering by individual piece is allowed”只支持 `order_multiple_units=1`，不能据此推导实际包装。

## 4. 相比上一版本更新了什么

本次开始前仓库已有规划文档、A 的数据字典和合成输入，但没有成员 B 的可运行实现。本次新增：

- `src/supplier_comparison/extraction/` 模块。
- PDF／CSV 解析器、模型适配器、证据校验和统一服务入口。
- 严格的模型输出 Schema，以及调用次数和有限重试控制。
- SGD 费用金额确定性格式化与归一化审计事件。
- 费用状态允许值和非法枚举拒绝。
- 真实模型运行脚本、字段验收脚本和 34 项测试。
- A、B、C 三份人工修正前真实模型结果及开发验收记录。

模型 Schema 也经过两轮修正：最初的可空默认字段没有真正约束模型；随后改为必填仍不能表达“`EXTRACTED` 不得为 `null`”；最终改为按状态判别的联合 Schema，把条件直接写入提供方可见的 JSON Schema。

## 5. 为什么这样修改

- 防止模型把缺失值伪装成正常空报价，或为缺失内容伪造来源。
- 防止只靠提示词约束结构；关键限制由 Schema 和确定性代码执行。
- 保留文件哈希、版本、位置和引用，方便 D 持久化并供用户复核。
- 将模型理解与金额格式化分开，允许合法 Decimal 字符串，同时避免因展示精度重复调用模型。
- 将固定输出和真实模型结果显式区分，避免把模拟结果当成真实验收。
- 保留失败结果、请求 ID、trace ID、token 和错误分类，避免只报告成功样本。

## 6. 使用方法与交付物

### 基本环境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q
```

真实模型配置从 `.env.local` 读取。可以复制 `.env.example`，再只在本地填写 API Key；`.env.local` 不得提交。

单份真实调用示例（会产生模型费用）：

```bash
set -a
source .env.local
set +a
SUPPLIER_MODEL_MAX_ATTEMPTS=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_real_extraction.py \
  --supplier A \
  --output evaluation/results/local/2026-09-10/example.json
```

V2 三家供应商批量调用示例（会产生真实模型调用和费用；当前尚未执行）：

```bash
set -a
source .env.local
set +a
SUPPLIER_MODEL_MAX_ATTEMPTS=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_real_extraction.py \
  --all-suppliers \
  --dataset-version V2 \
  --output-dir evaluation/results/local/v2
```

批量模式共享一个 `ModelCallBudget`，三家供应商的调用次数累计计算。V2 的异构 CSV
目前仍会被固定模板解析器明确拒绝，尚未实现 CSV profile 适配。

重新生成开发字段对照：

```bash
PYTHONPATH=src .venv/bin/python scripts/evaluate_development_extractions.py
```

| 交付物名称 | 文件路径或链接 | 简单介绍 | 使用方法 |
| --- | --- | --- | --- |
| Python 项目配置 | [`pyproject.toml`](pyproject.toml) | Python 版本、运行依赖和测试依赖 | 使用 `pip install -e '.[dev]'` 安装 |
| 环境变量模板 | [`.env.example`](.env.example) | SiliconFlow／模型配置占位符，不含真实密钥 | 复制为 `.env.local` 后填写密钥 |
| 共享契约 | [`contracts.py`](src/supplier_comparison/extraction/contracts.py) | 来源、候选、批次、运行和归一化事件 | C/D 直接导入 Pydantic 类型 |
| PDF 解析器 | [`pdf_parser.py`](src/supplier_comparison/extraction/pdf_parser.py) | 解析一种英文文本 PDF 开发版式 | 创建 `DocumentContext` 后调用 `PdfQuoteParser.parse` |
| 固定 CSV 解析器 | [`csv_parser.py`](src/supplier_comparison/extraction/csv_parser.py) | 解析冻结宽表 CSV | 使用 `FixedCsvQuoteParser.parse_row` |
| 模型适配器 | [`adapters.py`](src/supplier_comparison/extraction/adapters.py) | 固定输出和真实 OpenAI-compatible 调用 | 传入配置、解析结果和 `ModelCallBudget` |
| 模型载荷 Schema | [`model_payload.py`](src/supplier_comparison/extraction/model_payload.py) | 约束三种候选状态 | 由适配器自动生成 JSON Schema |
| 来源与枚举校验 | [`evidence.py`](src/supplier_comparison/extraction/evidence.py) | 校验字段集合、来源、引用和费用状态 | 统一服务入口会自动调用 |
| 确定性归一化 | [`normalization.py`](src/supplier_comparison/extraction/normalization.py) | 当前 SGD 费用两位小数格式化 | 统一服务入口会自动调用并记录事件 |
| 统一提取入口 | [`service.py`](src/supplier_comparison/extraction/service.py) | 将模型载荷转换成公共 `ExtractionBatch` | 下游调用 `extract_quote_candidates` |
| 真实提取脚本 | [`run_real_extraction.py`](scripts/run_real_extraction.py) | 分别运行 A／B／C 合成 PDF | 指定 `--supplier` 和 `--output` |
| 开发验收脚本 | [`evaluate_development_extractions.py`](scripts/evaluate_development_extractions.py) | 按确认口径对照 90 个字段 | 设置 `PYTHONPATH=src` 后运行 |
| V2 开发输入 | [`quote_V2/`](data/generated/inputs/development/quote_V2/) | 三家不同版式报价及采购需求的 PDF/CSV | PDF 可用于解析与模型开发；异构 CSV 尚未支持 |
| 单元测试 | [`tests/extraction/`](tests/extraction/) | 覆盖解析、契约、证据、适配器和归一化 | 执行 `pytest -q` |
| 真实模型结果 | [`evaluation/results/local/2026-09-10/`](evaluation/results/local/2026-09-10/) | 成功、失败和人工修正前运行记录 | 用于开发复核，不作为真实供应商结论 |
| 字段验收报告 | [`DEVELOPMENT_PDF_FIELD_REVIEW.md`](evaluation/results/local/2026-09-10/DEVELOPMENT_PDF_FIELD_REVIEW.md) | A/B/C 逐字段矩阵和调用量 | 供 A 签字及 C/D 查看已知问题 |
| 完整验收 JSON | [`development_pdf_field_review.json`](evaluation/results/local/2026-09-10/development_pdf_field_review.json) | 90 个字段的期望、实际、来源形状和结果 | 可供脚本或 CI 读取 |

## 7. 结果分析

### 是否达到预期

事实：

- 首版测试为 34 项通过；选择性纳入 V2 并增加回归覆盖后为 45 项通过、0 项失败。
- 三份开发 PDF 都完成了真实本地模型调用，每份最终记录均为 1 次调用、0 次重试。
- A 为 30／30，B 按已确认 PDF 口径及 Decimal 比较为 30／30，C 为 29／30；合计 89／90，字段匹配率为 0.9889。
- B 的运费保持 `MISSING/null/无来源`；包装方式和每包数量按确认口径保持非阻塞缺失。
- B 的 `other_fees_amount` 模型值为 `"0"`，确定性重放输出为 `"0.00"`，并记录规则 `sgd-fee-amount-2dp/1.0.0`。
- C 的唯一失败是 `shipping_fee_status="PAID"`，契约期望为 `KNOWN_AMOUNT`；原始失败结果没有被改写。
- 结果文件未发现 API Key 标记。

结论：PDF／CSV 解析、结构化候选、来源校验和本地模型基线达到了可交接状态；三份 PDF 的逐字段开发验收尚未全部通过，C 仍有 1 个已登记错误。主办方 API、独立版式盲测和 C/D 端到端集成不在本次已完成结果内。

### 达到或未达到的原因

事实：判别联合 Schema 解决了 `EXTRACTED.normalized_value` 缺失或为 `null` 的问题；确定性归一化解决了合法金额字符串的展示精度问题。

推测：C 输出 `PAID` 很可能是因为调用时提示中没有列出费用状态的完整允许值。该判断不是新的实测结论；代码已增加允许值和非法枚举拒绝，但尚未再次调用 C 验证。

### 本次发现的问题与经验

- Pydantic 的运行时跨字段校验不会自动成为模型可见的条件 Schema。
- “字段为必填”不等于“字段在特定状态下不得为 `null`”。
- JSON 和来源定位通过，不代表字段业务语义一定正确，仍需逐字段参考对照。
- 业务缺失不应通过重复调用模型猜测；重试应主要用于瞬时网络或服务错误。
- 原始模型值、确定性格式化和人工纠正必须分开记录。
- 失败样本和 token 用量应保留，不能只提交最终成功结果。

建议：如需要三份开发 PDF 达到 90／90，只重跑 C 一次即可；运行前保持单次尝试。该建议尚未执行。

## 8. 下游说明

### 下游可以直接使用什么

- D 可以直接使用 `DocumentContext`、`ParsedInput`、`ExtractionBatch`、`ExtractionRun`、`NormalizationEvent` 和稳定错误代码。
- D 可以先用 `FixedOutputAdapter` 解耦联调，再接 `OpenAICompatibleAdapter`；恢复时必须延续同一个 `ModelCallBudget`。
- C 可以读取已经通过 B 校验的 `QuoteFieldCandidate`，使用 `validation_status`、`normalized_value`、`unit`、`origin` 和 `source_refs`。
- 下游首次集成建议使用 A 的 30／30 结果；随后使用 B 验证未知运费流程。

### 使用注意事项

- 不要把 `.env.local`、API Key 或凭据写入代码、日志、检查点或 Git。
- 不要把 B 的 `MISSING` 当成 0，也不要让模型结果直接变成 `VERIFIED`。
- C 的当前原始结果包含非法的 `shipping_fee_status="PAID"`，只能作为失败样本，不能作为已验收输入。
- B 的包装缺失是否阻塞、MOQ／成本和可行性如何计算归 C；B 不复制这些公式。
- 参考 CSV 只在模型调用完成后用于开发验收，没有进入模型提示。
- 所有 A/B/C 报价和器件标识均为合成数据，不能描述为真实供应商报价。
- 首版真实模型结果只覆盖一种 PDF 版式和一个冻结 CSV 表头；V2 PDF 已通过解析与固定适配器边界测试，但尚未完成真实模型字段验收，也不代表通用文档解析能力。

### 是否与上一版本兼容

事实：本实现读取 A 的 `quote_data_field.csv` v1.2.0，没有修改 A 的字段名称；固定输出和真实模型最终都进入同一 `ExtractionBatch`。

事实：`normalization_events` 是带默认空值的新字段；只读取既有候选字段的下游可以继续工作，但若保存完整批次，应同步接收该字段。

事实：当前仓库尚未出现 C/D 的公共实现，因此模块级契约可交接，但真实数据库、计算和 LangGraph 集成兼容性尚未验证。

建议：C/D 接入时先跑一份 A 报价完成“解析→候选→来源校验→计算→保存”，再用 B 验证未知运费和恢复调用计数。
