# 成员 B 解析、模型与证据交接说明

日期：2026-09-10

成员：LC（成员 B）

范围：报价 PDF／CSV 解析、模型适配、来源证据、候选字段与开发评测

## 1. 本次更新概述

本次完成了可运行的 B 模块，并将 A 提供的 V2、V3、V4 合成数据和参考答案接入开发测试。模块可以解析已登记的文本 PDF／CSV，调用固定或真实模型适配器，输出统一候选字段，并校验文件、版本、来源和引用。

事实：真实模型为 SiliconFlow `deepseek-ai/DeepSeek-V4-Flash`；当前全量自动化测试为 120 项通过。V3 已按统一的提示词 `1.7.0` 和适配器 `1.1.0` 冻结，不再针对 V3 调参。

## 2. 主要做了什么

- 建立 `ParsedInput`、`EvidenceSource`、`QuoteFieldCandidate` 和 `ExtractionBatch` 等 Pydantic 契约。
- 使用 `pdfplumber` 提取文本型 PDF，记录 SHA-256、页码、文本块、坐标和稳定来源 ID。
- 支持 V1 固定 CSV，以及 V2/V3 已登记的异构 CSV profile；未知或错误表头会被拒绝。
- 实现固定输出与 OpenAI-compatible 真实模型适配器，记录请求、token、错误和调用次数。
- 实现 3 次阶段尝试、8 次图运行调用上限；缺失业务字段不会通过重试猜测。
- 校验字段全集、引用原文、来源范围、报价版本以及运费／计价基数的证据语义。
- 确定性处理 SGD 费用两位小数、`Net N` 付款条款和冲突占位符。
- 让模型只选择当前文件中的短来源句柄，由后端映射真实来源 ID 并生成权威引用原文。
- 证据校验失败时保存不含凭据的候选载荷和运行元数据，支持定位失败字段。
- 接入 V2/V3 参考答案评分；V4 覆盖异常输入、来源稳定性、伪造／跨供应商引用和调用预算。

## 3. 实现思路

```text
文件限制与哈希
→ PDF／CSV 解析
→ 稳定来源与位置
→ 模型选择当前文件的来源句柄
→ 后端映射真实来源并绑定原文
→ Pydantic 结构校验
→ 确定性归一化
→ 引用和证据语义校验
→ ExtractionBatch 交给下游
```

关键边界：

- 业务 ID 和版本由调用方提供，不信任模型自报。
- 模型只生成候选，不生成 `VERIFIED`，也不执行采购计算。
- `MISSING` 必须是空值且无来源；`EXTRACTED` 必须有值和合法引用。
- 引用必须来自当前文件；伪造或跨供应商来源会被拒绝。
- `raw_value` 保留原文，`normalized_value` 保存标准表达，归一化事件单独记录。

## 4. 相比上一版本更新了什么

- 数据路径集中到 `quote_V1/quote_V2/quote_V3/quote_V4`。
- 模型提示词更新至 `quote-extraction/1.7.0`，真实适配器更新至 `openai-compatible/1.1.0`。
- JSON Schema 动态限制模型只能选择 `S001` 等当前文件来源句柄；未知句柄立即拒绝。
- `quoted_text` 由后端使用解析器原文生成，不再依赖模型逐字复制。
- 证据失败记录新增校验前候选载荷，但不记录 API Key。
- 明确 `INCLUDED` 且没有单列金额时，金额保持 `MISSING/null`，不能写成 0。
- 运费证据支持 `delivery fee`，但仍拒绝其他费用证据冒充运费证据。
- 将 `N30 - payment due 30 days after invoice` 等直接表达归一化为 `Net 30`。
- 新增 V2、V3 评分器和 V4 真实来源 PDF 运行能力。
- 新增 V3 五家 PDF/CSV 与 V4 极端边界测试。

## 5. 为什么这样修改

- 防止未知金额被误写成 0，影响下游成本和排序。
- 防止模型生成不存在或属于其他文件的来源。
- 避免正确来源因模型改写标点、空格或换行而被误拒绝，同时不使用模糊引用匹配。
- 将同义付款条款统一，减少只因展示形式造成的评测误差。
- 将文档成功率与字段准确率分开，避免只统计成功样本而高估整体效果。
- 保留真实失败记录，便于判断是模型、传输、解析还是证据门禁问题。

## 6. 使用方法与交付物

安装并运行测试：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
PYTHONPATH=src .venv/bin/python -m pytest -q
```

真实模型配置只放在 `.env.local`，不得提交。单份 V3 调用示例：

```bash
set -a
source .env.local
set +a
SUPPLIER_MODEL_MAX_ATTEMPTS=1 PYTHONPATH=src .venv/bin/python \
  scripts/run_real_extraction.py \
  --supplier A --dataset-version V3 --input-format pdf \
  --output evaluation/results/local/v3-a.json
```

离线评分，不会调用模型：

```bash
PYTHONPATH=src .venv/bin/python scripts/evaluate_v2_reference_answers.py

PYTHONPATH=src .venv/bin/python scripts/evaluate_v3_reference_answers.py \
  --results-root evaluation/results/local/2026-09-10/v3_prompt_1_7_0/frozen \
  --output evaluation/results/local/2026-09-10/v3_prompt_1_7_0/frozen/v3_reference_score.json
```

| 交付物名称 | 文件路径或链接 | 简单介绍 | 使用方法 |
| --- | --- | --- | --- |
| 公共契约 | [`src/supplier_comparison/extraction/contracts.py`](src/supplier_comparison/extraction/contracts.py) | 解析、来源、候选和运行对象 | C/D 直接导入类型 |
| PDF 解析器 | [`src/supplier_comparison/extraction/pdf_parser.py`](src/supplier_comparison/extraction/pdf_parser.py) | 文本 PDF 解析和来源定位 | 调用 `PdfQuoteParser.parse` |
| CSV 解析器 | [`src/supplier_comparison/extraction/csv_parser.py`](src/supplier_comparison/extraction/csv_parser.py) | 固定模板和 profile CSV 解析 | 使用登记的 profile 调用 `parse_row` |
| 模型适配器 | [`src/supplier_comparison/extraction/adapters.py`](src/supplier_comparison/extraction/adapters.py) | 固定输出、真实 API、来源句柄约束和后端引用绑定 | 传入配置和 `ModelCallBudget` |
| 证据校验 | [`src/supplier_comparison/extraction/evidence.py`](src/supplier_comparison/extraction/evidence.py) | 校验引用、版本、范围和语义 | 统一入口自动调用 |
| 确定性归一化 | [`src/supplier_comparison/extraction/normalization.py`](src/supplier_comparison/extraction/normalization.py) | 金额、付款条款和冲突占位符处理 | 统一入口自动调用并记录事件 |
| 统一入口 | [`src/supplier_comparison/extraction/service.py`](src/supplier_comparison/extraction/service.py) | 生成 `ExtractionBatch` | 下游调用 `extract_quote_candidates` |
| 真实运行脚本 | [`scripts/run_real_extraction.py`](scripts/run_real_extraction.py) | 运行 V1–V4 已支持样本 | 指定版本、供应商和格式 |
| V2 评分器 | [`scripts/evaluate_v2_reference_answers.py`](scripts/evaluate_v2_reference_answers.py) | 对照 A 的 V2 开发答案 | 指定结果目录离线运行 |
| V3 评分器 | [`scripts/evaluate_v3_reference_answers.py`](scripts/evaluate_v3_reference_answers.py) | 分开统计文档成功率和字段准确率 | 对 V3 结果目录运行 |
| V3 冻结评分 | `evaluation/results/local/2026-09-10/v3_prompt_1_7_0/frozen/v3_reference_score.json` | 统一 1.7.0 冻结运行的本地评分 | 仅本地复核，不提交 Git |
| V2–V4 输入 | [`data/generated/inputs/development/`](data/generated/inputs/development/) | 合成开发报价与异常夹具 | 只用于开发和评测 |
| V2–V4 参考答案 | [`evaluation/reference/`](evaluation/reference/) | A 提供的开发答案和边界期望 | 必须与运行时隔离 |
| 自动化测试 | [`tests/extraction/`](tests/extraction/) | 解析、模型、证据、评测与边界测试 | 运行 `pytest -q` |
| 本地真实结果 | `evaluation/results/local/2026-09-10/` | 人工修正前输出、失败和评分 | 只保留本地，不提交 Git |

## 7. 结果分析

### 是否达到预期

事实：

- 自动化测试：120 项通过，0 项失败。
- V1 三份开发 PDF：89/90；这是早期开发基线。
- V2 六份最新结果：172/180，字段匹配率 95.56%；结果混用了多个提示词版本。
- V3 修复验证中，每个输入取最新结果得到 10/10、300/300；该结果混用提示词 1.6.0 和 1.7.0，只用于证明三个失败点已修复。
- V3 冻结运行统一使用提示词 1.7.0、适配器 1.1.0：PDF 5/5 通过，CSV 3/5 通过，合计 8/10，文档成功率 80.00%。
- V3 冻结运行成功的 8 份全部为 240/240，条件字段匹配率 100%；失败文档按零分计入后的保守字段覆盖率为 80.00%。
- V3 冻结运行没有字段、Schema 或证据失败；C/D CSV 均在首次和第二次尝试中发生 `model_transport_failed`。
- V3 冻结运行实际调用 13 次：PDF 5 次；CSV 首轮 5 次，加 C/D/E 各 1 次传输重试；按本次 CSV 冻结批次累计达到 8 次调用上限后停止。
- V4 两份有效来源 PDF 均一次真实调用通过，各输出 30 个候选字段，全部引用属于各自输入文件。
- V4 的损坏、空白、加密、超页数、超大小 PDF 和错误 CSV 表头均在模型调用前拒绝。

结论：B 模块已达到开发交接状态。V3 冻结版在获得模型响应时为 240/240，但端到端成功率受提供方传输超时影响为 80%；这些开发指标不能表述为生产准确率。

### 达到或未达到的原因

事实：来源句柄和后端原文绑定后，A PDF 的精确引用问题消失，D CSV 的未知来源问题在限定复测中消失；D PDF 的费用状态与金额达到参考答案。

事实：冻结运行剩余失败均为提供方传输超时，系统保留失败和第二次尝试记录，并在 CSV 累计 8 次调用后停止。

推测：C/D CSV 连续超时可能与当时的提供方服务状态或网络有关；现有证据不能归因于某个具体外部组件。

### 本次发现的问题与经验

- 结构正确不代表引用或业务语义正确。
- 条件字段准确率必须和端到端成功率一起报告。
- CSV 列名也是证据语义的一部分，例如 `Delivery fee` 可以说明金额用途。
- 来源句柄和后端绑定比放宽引用匹配更安全，也更稳定。
- 失败、重试、token 和人工修正前输出都应保留。

建议：V3 已冻结，不再通过重跑提高分数。下一轮使用新的留出数据验证泛化，并单独统计模型准确率和提供方传输可用性。

## 8. 下游说明

### 下游可以直接使用什么

- D 可使用 `DocumentContext`、`ParsedInput`、`ExtractionBatch`、`ExtractionRun` 和稳定错误代码。
- C 可读取通过 B 门禁的 `QuoteFieldCandidate`，使用状态、标准化值、单位、来源和原文。
- 联调时可先用 `FixedOutputAdapter`，再切换真实适配器；两种结果必须显式区分。

### 使用注意事项

- `MISSING` 不是 0，模型结果也不能直接升级为 `VERIFIED`。
- 参考答案目录不能提供给运行时解析器或模型。
- `.env.local`、API Key 和本地真实模型结果不得提交。
- V3 冻结版的 100% 是成功返回的 8 份结果的条件准确率；完整端到端成功率是 80.00%。
- 冻结运行的 C/D CSV 没有得到模型结果，不能将旧版成功结果补入统一版本冻结分数。
- V4 是边界和证据安全测试，不是完整 31 字段报价准确率测试。
- 所有报价、供应商和器件标识均为合成数据。

### 是否与上一版本兼容

事实：字段名称和 `ExtractionBatch` 主结构保持兼容；来源句柄只存在于真实适配器内部，输出给 C/D 的仍是真实稳定来源 ID。新增归一化事件有默认空值。

建议：C/D 接入时先用一份通过门禁的 V3 结果验证完整链路，再使用缺失或失败样本验证 `PENDING`、错误处理和恢复逻辑。
