# 成员 B 解析、模型、证据与审查交付说明（第二版）

日期：2026-09-11

成员：LC（成员 B）

范围：报价 PDF／CSV 解析、模型适配、来源证据、候选字段、自动审查、人工复核接口、B→C 安全交付与 V2–V6 评测

说明：本文是基于当前代码重新整理的完整交付说明，不替代或修改原 `guide_lc.md`。本文中的“事实”“推测”和“建议”明确分开；所有报价、供应商和器件数据均为合成数据。

## 1. 本次更新概述

本次将成员 B 的工作从“解析并输出候选字段”升级为“解析、证据校验、风险审查、人工复核闭环和安全交付”。当前模块不仅能从已支持的 PDF／CSV 中提取字段，还能判断结果是否可以直接交给 C，或者必须转人工复核、拒绝或记录为模型失败。

事实：当前已完成计划中的阶段 1–6：

1. 冻结字段关键性策略和 `ReviewEnvelope` 契约。
2. 实现条件关键性解析器。
3. 实现确定性五层审查门禁。
4. 实现人工确认与人工修正纯函数。
5. 实现 B→C 安全适配器。
6. 完成 V2–V6 离线回归和真实模型评测。

事实：真实模型使用 SiliconFlow `deepseek-ai/DeepSeek-V4-Flash`，当前冻结的提取提示词为 `quote-extraction/1.7.0`，审查策略为 `extraction-review/1.1.0`。

事实：阶段 6 收尾时，全量自动化测试为 245 项通过、0 项失败。V5/V6 本轮实际使用 20 次模型调用，失败样本没有从评测分母中删除。

事实：V6 calibration 初次审查出现 4 个关键字段静默错误。增加确定性门禁规则后，对原结果重新审查，不再次调用模型，关键静默错误由 4 个降为 0。

事实：当前代码已经提供人工复核所需的数据契约和 Python 函数，但还没有人工复核网页、FastAPI 路由、任务队列和数据库持久化。这些界面和流程可由 D 在现有接口上封装。

## 2. 主要做了什么

### 2.1 建立统一报价提取契约

事实：建立了以下核心对象：

- `DocumentContext`：保存任务、报价、文档和供应商的权威 ID 与版本。
- `ParsedInput`：保存解析后的文件信息、文件哈希和来源集合。
- `EvidenceSource`：保存 PDF 页码、文本块、坐标，或 CSV 行号和列名。
- `QuoteFieldCandidate`：保存字段原文、标准化值、状态、来源和字段版本。
- `ExtractionRun`：保存适配器、模型、提示词、调用次数、token 和错误信息。
- `ExtractionBatch`：将当前文件的解析结果、30 个可提取字段和运行记录组成统一输出。

事实：模型不能决定 `task_id`、`quote_id`、`document_id` 和版本。这些身份信息由后端传入并在审查时再次核验。

### 2.2 实现文本型 PDF 解析

事实：使用 `pdfplumber` 读取英文机器生成 PDF，并记录：

- 原始文件名和文件大小；
- SHA-256 文件哈希；
- 页码；
- 稳定文本块 ID；
- 原始文本；
- 可用的 PDF 坐标；
- 稳定来源 ID。

事实：损坏、空白、加密、超页数和超大小 PDF 会在适当阶段被拒绝，不会伪装成字段全部缺失的正常报价。

事实：当前不承诺扫描 PDF OCR。扫描件或无法可靠提取文本的 PDF 应明确报错或转人工录入。

### 2.3 实现 CSV 解析和混合路由

事实：支持已登记的固定 CSV 模板和版本化 profile。未知表头、错误表头或未登记格式会被拒绝。

事实：V6 新增注册 CSV 混合路径：

- 明确、结构化的字段使用确定性代码直接映射；
- 付款条款、起算事件、范围型交期和复杂费用语义等字段才调用模型；
- 干净注册 CSV 可在 0 次模型调用下完成；
- 纯确定性 CSV 不需要 `.env.local` 或模型凭据。

### 2.4 实现统一模型适配层

事实：模型适配层支持：

- 固定输出适配器，用于单元测试和下游解耦联调；
- OpenAI-compatible 真实模型适配器；
- provider、模型、base URL、环境、超时和重试的后端配置；
- 每阶段最多 3 次尝试；
- 每个逻辑运行最多 8 次模型调用；
- 请求 ID、token、错误、调用次数和提示词版本记录；
- 固定输出、真实模型输出和人工修改的明确区分。

事实：API Key 只从环境变量读取，不写入代码、日志、测试夹具或评测参考答案。

### 2.5 限制模型来源引用

事实：解析器先生成当前文件可用的短来源句柄，例如 `S001`。模型只能选择这些句柄，后端再把句柄映射回真实 `source_id` 和原文。

事实：以下引用会被拒绝：

- 不存在的来源；
- 其他供应商的来源；
- 其他报价或文档版本的来源；
- 文件哈希不匹配的来源；
- 引用字段和证据语义不一致的来源；
- 模型伪造的 quoted text。

事实：模型只负责选择来源，最终 `quoted_text` 由后端使用解析器原文生成。

### 2.6 实现确定性归一化

事实：对不需要模型判断的表达使用确定性归一化，包括：

- SGD 金额统一保存为两位小数字符串；
- 合法输入 `"0"`、`"0.0"` 和 `"0.00"` 可统一为 `"0.00"`；
- 金额比较使用 `Decimal`；
- 部分明确的 `Net N` 付款条款归一化；
- 冲突占位符保持 `CONFLICT/null`；
- `MISSING` 保持空值、空来源；
- `UNKNOWN` 费用金额保持 `null`。

事实：“ordering by individual piece is allowed”只支持 `order_multiple_units=1`，不能推导 `packaging_type=piece` 或 `units_per_pack=1`。

事实：“Other fees None”支持 `other_fees_status=NOT_APPLICABLE` 和 `other_fees_amount="0.00"`，两项均可引用同一句原文。

### 2.7 冻结字段关键性策略

事实：根据 C 提供的字段关键性说明，将字段分为：

- 16 个始终关键字段；
- 10 个条件关键字段；
- 4 个非关键字段；
- 系统身份和审计字段单独管理。

事实：条件字段是否阻塞，不由模型置信度决定，而由确定性上下文判断。例如：

- 采购要求指定 revision 时，revision 成为关键；
- 需要包装换算时，包装字段成为关键；
- 费用状态要求金额时，对应金额成为关键；
- 使用相对交期时，交期四元组共同成为关键；
- 使用相对有效期时，quote date 成为关键。

### 2.8 实现确定性五层审查门禁

事实：审查依次覆盖：

1. 候选字段集合和 Pydantic 结构；
2. 系统身份、版本和字段生产者；
3. 来源身份、引用范围和证据语义；
4. 字段值、单位、枚举和跨字段关系；
5. 当前适用关键字段是否已经审查并可安全交付。

事实：审查输出四种顶层状态：

| 状态 | 含义 | 后续操作 |
| --- | --- | --- |
| `READY_FOR_DOWNSTREAM` | 当前审查已经通过 | 可进入 B→C reviewed adapter |
| `REVIEW_REQUIRED` | 存在需要人工判断的缺失、疑似错误或冲突 | 人工确认或修正后重新审查 |
| `REJECTED` | 文件、身份、版本、来源或契约存在不可直接放行的问题 | 修复输入或系统问题，不能直接人工放行 |
| `MODEL_FAILED` | 模型调用未得到合法结果 | 保留失败和调用记录，按模型失败处理 |

事实：`downstream_ready` 和 `calculation_inputs_complete` 是不同概念：

- `downstream_ready=true` 表示已经审查，可以安全交给 C；
- `calculation_inputs_complete=true` 表示当前计算所需关键字段都有确定值；
- 人工确认“原文确实缺失”后，可以前者为 true、后者为 false，此时 C 必须输出 `PENDING`，不能给出最终推荐。

### 2.9 增加高风险静默错误规则

事实：根据 V6 calibration 暴露的问题，增加了以下规则：

- 费用状态为 `UNKNOWN` 时阻塞并要求确认；
- “文档没有费用说明”不能被当作免费、已包含或不适用的正向证据；
- `unit_price` 的标准化金额必须出现在引用证据中，禁止模型自行把整包价格除算成单价；
- 同一文档同时出现订单日期和到账日期作为交期起算条件时，进入 `DOCUMENT_CONFLICT`；
- 明确费用金额必须由费用语义证据支持；
- 价格基数来源不能冒充订购步长来源。

### 2.10 实现人工复核接口

事实：当前实现的是代码层接口和 JSON 契约，不是完整网页。

自动审查会在 `ReviewEnvelope.review.findings` 中提供：

- 问题字段；
- 是否关键、是否阻塞；
- 问题代码和说明；
- review reason；
- 相关 `source_ids`；
- 是否已经由人工事件解决。

事实：人工可以执行四类操作：

| 操作 | 使用条件 | 是否修改候选值 | 审计结果 |
| --- | --- | --- | --- |
| `CONFIRM_MISSING` | 人工确认文档确实没有该字段 | 否 | 生成 `ReviewEvent` |
| `CONFIRM_CONFLICT` | 人工确认文档冲突且暂时无法得到单一值 | 否 | 生成 `ReviewEvent` 并保留冲突来源 |
| `USER_INPUT` | 文档缺失，由授权人员补充外部信息 | 是 | 生成新字段版本和 `CorrectionEvent` |
| `USER_CORRECTION` | 模型提取错误，人工依据原文纠正 | 是 | 生成新字段版本和 `CorrectionEvent` |

事实：每个人工事件绑定：

- reviewer ID 和时间；
- task revision；
- quote ID 和 quote version；
- document ID、document version 和 SHA-256；
- 当前 candidate field ID 和 field version；
- 修改前后值和修改原因；
- 必要时绑定依据来源。

事实：旧版本报价、旧文件哈希或旧字段版本上的人工操作会被拒绝，避免过期复核覆盖新版报价。

### 2.11 实现 B→C 安全适配器

事实：新增 reviewed 入口，只有 `ReviewEnvelope.downstream_ready=true` 才能构造 C 的 `QuoteInput`。

事实：交给 C 时会隔离 `supplier_country`、`category`、`item` 和 `payment_terms` 四个非计算字段，避免其被意外用于成本、可行性或推荐。

事实：如果报价已经人工确认确实缺少关键信息，可以安全进入 C，但 C 只能得到 `PENDING/PENDING_INPUT`。如果缺失信息被授权人员补齐并重新审查通过，C 才能执行完整计算。

### 2.12 建立 V2–V6 评测体系

事实：新增或接入：

- V2 历史开发答案评分；
- V3 冻结提示词回归；
- V4 文件、来源和执行边界测试；
- V5 PDF 泛化测试；
- V6 development、calibration 和 holdout 三段数据；
- 输入文件和字段字典 SHA-256 校验；
- 逐字段状态和值比较；
- 模型调用预算检查；
- 自动直通、人工复核和关键静默错误统计；
- 不重新调用模型的保存结果门禁重放。

事实：运行时提取代码不会读取 A 的参考答案。参考答案只由离线评分器使用。

## 3. 实现思路

### 3.1 主流程

```text
文件校验和 SHA-256
        ↓
PDF 文本解析 / 注册 CSV 解析
        ↓
生成稳定来源 ID 和位置
        ↓
确定性字段直接映射
        ↓
仅把复杂语义字段交给模型
        ↓
模型选择本文件来源句柄
        ↓
后端绑定真实来源和原文
        ↓
Pydantic 结构校验与确定性归一化
        ↓
五层自动审查门禁
        ↓
┌──────────────────────┬──────────────────┬──────────────┐
│ READY_FOR_DOWNSTREAM │ REVIEW_REQUIRED  │ REJECTED /   │
│                      │                  │ MODEL_FAILED │
└──────────┬───────────┴────────┬─────────┴──────┬───────┘
           │                    │                │
           ↓                    ↓                ↓
      安全适配给 C       人工确认或人工修正     修复或重试
                                ↓
                         重新执行完整门禁
                                ↓
                         通过后再交给 C
```

### 3.2 自动审查和人工审查的职责

事实：自动审查负责机械且可重复判断的内容，例如结构、枚举、金额格式、来源身份、版本、费用组合和关键字段缺失。

事实：人工负责文档歧义、确实缺失、冲突解释、外部授权信息和模型错误纠正。

事实：人工操作不会直接绕过门禁。生成事件或新 batch 后，必须重新运行全部审查规则。

### 3.3 关键安全边界

- 模型输出只能是候选，不能自行标记 `VERIFIED`。
- `MISSING` 不是 0，`UNKNOWN` 也不是自动不合格。
- 人工补充与文档证据必须区分：`USER_INPUT` 不得伪造文档来源。
- 人工纠正必须保留 before/after 和版本。
- 参考答案不得进入运行时目录。
- 评测失败、重试和人工修正前结果必须保留。
- B 不执行 C 的 MOQ、成本、预算和排序公式。
- B 不实现 D 的数据库、任务恢复或 HTTP 工作流。

## 4. 相比上一版本更新了什么

上一版本主要完成了解析、模型适配、证据校验和 V2–V4 开发评测。当前版本在其基础上新增或强化了以下内容。

### 4.1 从“提取结果”升级为“可交付结果”

- 新增 `ReviewEnvelope`，不再把任意 `ExtractionBatch` 直接交给 C。
- 新增四种顶层审查状态。
- 新增 `downstream_ready` 和 `calculation_inputs_complete` 双标志。
- 新增逐字段 finding、原因、严重程度和阻塞字段集合。

### 4.2 新增人工复核闭环

- 新增 `CONFIRM_MISSING` 和 `CONFIRM_CONFLICT`。
- 新增 `USER_INPUT` 和 `USER_CORRECTION`。
- 人工修改递增字段版本并保存不可变审计记录。
- 人工操作与当前 task、quote、document、hash 和字段版本绑定。
- 人工处理后必须重新执行完整门禁。

### 4.3 新增 B→C reviewed 接口

- 未通过门禁的报价会在进入 C 前被拒绝。
- 已确认的合法缺失可以交给 C，但保持计算不完整。
- 非关键展示字段与 C 的计算输入隔离。
- 支持多个 reviewed envelope 进入现有比较规则。

### 4.4 新增 V5/V6 数据和评测路径

- 接入 V5/V6 输入、控制夹具和参考答案。
- 修正 V5/V6 参考文件中的字段字典 SHA-256 元信息。
- 新增 V5/V6 通用真实运行脚本。
- 新增 V5/V6 通用离线评分器。
- 新增保存结果审查重放工具。
- 新增注册 CSV 混合解析，降低无意义模型调用。

### 4.5 新增校准后的高风险规则

- 识别费用否定句误读。
- 阻止模型自行推导单位价格。
- 识别多个交期起算事件冲突。
- 对 UNKNOWN 费用状态进行阻塞处理。

### 4.6 增强运行脚本易用性

事实：纯确定性 CSV 现在不再强制读取真实模型配置，可在没有 `.env.local` 时运行，并明确记录 `model_calls_used=0`。

## 5. 为什么这样修改

### 5.1 防止“模型有输出”被误认为“可以计算”

事实：字段齐全、JSON 合法，只能证明结构正常，不能证明来源正确、语义正确或满足 C 的计算要求。增加门禁后，模型结果必须经过关键性和证据审查才能交付。

### 5.2 防止关键错误静默进入下游

事实：V6 calibration 曾发现 4 个关键字段错误仍被自动放行。确定性规则升级后，这些错误被转入人工复核，关键静默错误降为 0。

### 5.3 正确处理“确实缺失”

事实：文档确实缺少运费，不代表解析器出错，也不能填写 0。人工 `CONFIRM_MISSING` 可以证明缺失已被看过，同时仍让 C 输出 `PENDING`。

### 5.4 保证人工修改可以审计

事实：只保存修改后的值无法回答“谁改的、为什么改、针对哪个版本”。新增事件和 before/after 后，可以防止旧审查覆盖新报价，也便于 D 后续持久化。

### 5.5 降低成本和外部服务风险

事实：明确的注册 CSV 字段不需要模型理解。确定性映射可以减少调用、缩短时间，并避免模型传输失败影响简单输入。

### 5.6 保持 B 和 C 的职责边界

事实：B 负责把报价转换成经过审查的类型化候选，C 负责确定性采购计算。安全适配器只检查交付条件，不复制 C 的公式。

### 5.7 保持评测诚实

事实：失败文档按零字段计入保守指标，参考答案问题和模型问题分别报告，非盲 holdout 不冒充严格盲测。

推测：当前端到端成功率的主要剩余损失来自 provider 传输稳定性和 PDF 表格来源切块，而不是所有字段的普遍理解失败。

建议：下一轮优先修复传输诊断和 PDF 组合来源，不应为了提高分数放松来源校验。

## 6. 使用方法与交付物

### 6.1 安装和全量测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
PYTHONPATH=src .venv/bin/python -m pytest -q
```

当前验证结果：245 项通过，0 项失败。

### 6.2 运行纯确定性注册 CSV

该路径不需要 `.env.local`：

```bash
PYTHONPATH=src .venv/bin/python scripts/run_versioned_evaluation.py \
  --dataset-version V6 \
  --split holdout \
  --case-id V6-HOLD-01 \
  --output-dir evaluation/results/local/manual-check
```

预期：`model_calls_used=0`。

### 6.3 运行真实模型样本

真实模型配置只放在 `.env.local`：

```bash
set -a
source .env.local
set +a

PYTHONPATH=src .venv/bin/python scripts/run_versioned_evaluation.py \
  --dataset-version V6 \
  --split development \
  --case-id V6-DEV-03 \
  --output-dir evaluation/results/local/manual-check
```

`.env.local`、API Key 和 `evaluation/results/local/` 不得提交 Git。

### 6.4 离线评分

离线评分不会调用模型：

```bash
PYTHONPATH=src .venv/bin/python scripts/evaluate_versioned_results.py \
  --reference evaluation/reference/quote_V6/development/reference_answers.json \
  --results-root evaluation/results/local/manual-check \
  --output evaluation/results/local/manual-check/score.json
```

### 6.5 对保存结果重新执行审查门禁

```bash
PYTHONPATH=src .venv/bin/python scripts/refresh_saved_reviews.py \
  --results-root evaluation/results/local/manual-check
```

该操作只重跑确定性审查，不再次调用模型，也不修改模型原始候选字段。

### 6.6 自动审查

```python
from supplier_comparison.extraction import (
    CriticalityContext,
    review_extraction_batch,
)

envelope = review_extraction_batch(
    batch,
    quote_dictionary,
    CriticalityContext(
        required_revision=requirement.revision,
        base_unit=requirement.base_unit,
    ),
    input_is_synthetic=True,
)
```

人工或上层系统应先读取：

```python
envelope.review_status
envelope.downstream_ready
envelope.calculation_inputs_complete
envelope.review.blocking_fields
envelope.review.findings
```

### 6.7 人工确认缺失或冲突

```python
from supplier_comparison.extraction import (
    HumanReviewAction,
    create_review_event,
)

review_event = create_review_event(
    batch,
    field_name="shipping_fee_status",
    action=HumanReviewAction.CONFIRM_MISSING,
    reviewer_id="reviewer-001",
    reviewed_at=reviewed_at,
)
```

随后把事件传回 `review_extraction_batch(..., review_events=(review_event,))`，重新执行门禁。

### 6.8 人工补充或纠正字段

```python
from supplier_comparison.extraction import (
    CorrectionAction,
    apply_candidate_correction,
)

corrected_batch, correction_event = apply_candidate_correction(
    batch,
    field_name="shipping_fee_amount",
    action=CorrectionAction.USER_INPUT,
    raw_value="S$200.00",
    normalized_value="200.00",
    unit="SGD",
    reason_code="AUTHORIZED_SHIPPING_ANSWER",
    reason="Buyer supplied the missing shipping amount.",
    reviewer_id="reviewer-001",
    reviewed_at=reviewed_at,
)
```

随后把新 batch 和事件传回：

```python
reviewed = review_extraction_batch(
    corrected_batch,
    quote_dictionary,
    criticality_context,
    input_is_synthetic=True,
    corrections=(correction_event,),
)
```

### 6.9 安全交给 C

```python
from supplier_comparison.rules import quote_input_from_reviewed_extraction

quote_input = quote_input_from_reviewed_extraction(reviewed)
```

如果 `downstream_ready=false`，适配器会抛出稳定错误，不会构造 C 的输入。

### 6.10 交付物清单

| 交付物名称 | 文件路径或链接 | 简单介绍 | 使用方法 |
| --- | --- | --- | --- |
| 公共提取契约 | [`src/supplier_comparison/extraction/contracts.py`](src/supplier_comparison/extraction/contracts.py) | 定义解析、来源、候选和运行对象 | B/C/D 导入类型或读取 JSON |
| PDF 解析器 | [`src/supplier_comparison/extraction/pdf_parser.py`](src/supplier_comparison/extraction/pdf_parser.py) | 文本 PDF 解析、文件限制和来源定位 | 调用 `PdfQuoteParser.parse` |
| 固定 CSV 解析器 | [`src/supplier_comparison/extraction/csv_parser.py`](src/supplier_comparison/extraction/csv_parser.py) | 已登记 CSV 模板解析 | 使用匹配 profile 调用 `parse_row` |
| CSV 混合解析器 | [`src/supplier_comparison/extraction/hybrid_csv.py`](src/supplier_comparison/extraction/hybrid_csv.py) | 确定性映射简单字段，只把复杂字段交给模型 | V6 runner 自动选择路由 |
| 模型适配器 | [`src/supplier_comparison/extraction/adapters.py`](src/supplier_comparison/extraction/adapters.py) | 固定输出、真实 API、调用预算和来源句柄 | 传入配置与 `ModelCallBudget` |
| 统一提取入口 | [`src/supplier_comparison/extraction/service.py`](src/supplier_comparison/extraction/service.py) | 生成统一 `ExtractionBatch` | 调用 `extract_quote_candidates` |
| 证据校验 | [`src/supplier_comparison/extraction/evidence.py`](src/supplier_comparison/extraction/evidence.py) | 校验来源、版本、引用和语义 | 统一入口自动调用 |
| 确定性归一化 | [`src/supplier_comparison/extraction/normalization.py`](src/supplier_comparison/extraction/normalization.py) | 处理金额、付款条款和冲突表达 | 统一入口自动调用 |
| 字段关键性策略 | [`src/supplier_comparison/extraction/criticality.py`](src/supplier_comparison/extraction/criticality.py) | 实现 16+10+4 分类和条件适用性 | 审查门禁自动调用 |
| 审查契约 | [`src/supplier_comparison/extraction/review_contracts.py`](src/supplier_comparison/extraction/review_contracts.py) | 定义 envelope、finding、人工事件和状态 | D 可据此建立 API Schema |
| 五层审查门禁 | [`src/supplier_comparison/extraction/review.py`](src/supplier_comparison/extraction/review.py) | 输出逐字段 finding 和整批 readiness | 调用 `review_extraction_batch` |
| 人工确认接口 | [`src/supplier_comparison/extraction/human_review.py`](src/supplier_comparison/extraction/human_review.py) | 生成确认缺失或冲突的 `ReviewEvent` | 调用 `create_review_event` |
| 人工修正接口 | [`src/supplier_comparison/extraction/corrections.py`](src/supplier_comparison/extraction/corrections.py) | 生成新候选和不可变 `CorrectionEvent` | 调用 `apply_candidate_correction` |
| readiness 保护 | [`src/supplier_comparison/extraction/readiness.py`](src/supplier_comparison/extraction/readiness.py) | 阻止未就绪结果进入下游 | reviewed adapter 自动调用 |
| B→C 适配器 | [`src/supplier_comparison/rules/integration.py`](src/supplier_comparison/rules/integration.py) | 将通过门禁的结果转换为 C 输入 | 调用 reviewed 入口 |
| 单样本真实运行 | [`scripts/run_real_extraction.py`](scripts/run_real_extraction.py) | 运行较早版本的已支持样本 | 指定版本、供应商和格式 |
| V5/V6 运行脚本 | [`scripts/run_versioned_evaluation.py`](scripts/run_versioned_evaluation.py) | 运行版本化样本和 CSV 混合路由 | 指定版本、split 和 case ID |
| V2 评分器 | [`scripts/evaluate_v2_reference_answers.py`](scripts/evaluate_v2_reference_answers.py) | 评估 V2 历史结果 | 对本地结果目录运行 |
| V3 评分器 | [`scripts/evaluate_v3_reference_answers.py`](scripts/evaluate_v3_reference_answers.py) | 评估 V3 冻结结果 | 对 V3 结果目录运行 |
| V5/V6 评分器 | [`scripts/evaluate_versioned_results.py`](scripts/evaluate_versioned_results.py) | 逐字段评分、哈希和静默错误统计 | 指定 reference 与结果目录 |
| 审查重放工具 | [`scripts/refresh_saved_reviews.py`](scripts/refresh_saved_reviews.py) | 对已保存 batch 重跑新门禁 | 不调用模型 |
| 字段字典 | [`data/contracts/quote_data_field.csv`](data/contracts/quote_data_field.csv) | 30 个 B 可提取字段及业务规则 | 解析、审查和评分共同加载 |
| C 字段关键性说明 | [`data/EXTRACTION_REVIEW_FIELD_CRITICALITY.md`](data/EXTRACTION_REVIEW_FIELD_CRITICALITY.md) | 定义始终关键、条件关键和非关键字段 | 作为关键性策略事实来源 |
| V2–V6 输入 | [`data/generated/inputs/`](data/generated/inputs/) | 合成开发、校准和 holdout 数据 | 仅用于开发和评测 |
| V2–V6 参考答案 | [`evaluation/reference/`](evaluation/reference/) | A 提供的离线答案和边界预期 | 与运行时严格隔离 |
| 自动化测试 | [`tests/extraction/`](tests/extraction/) | 解析、证据、关键性、门禁和人工闭环测试 | 运行 `pytest -q` |
| B→C 集成测试 | [`tests/rules/test_reviewed_extraction_integration.py`](tests/rules/test_reviewed_extraction_integration.py) | 验证未就绪阻断、合法缺失和修正后计算 | 运行 rules 测试 |
| 阶段 6 本地报告 | `evaluation/results/local/2026-09-11/stage6/STAGE6_EVALUATION_REPORT.md` | V2–V6 详细评测和问题清单 | 仅本地复核，不提交 Git |

## 7. 结果分析

### 是否达到预期

事实：全量自动化测试为 245 项通过、0 项失败。

事实：阶段 1–5 的契约、门禁、人工事件和 B→C 适配功能已经达到接口联调要求。

事实：阶段 6 结果如下：

| 数据集 | 文档结果 | 保守字段结果 | 自动直通／人工复核 | 关键静默错误 |
| --- | --- | --- | --- | --- |
| V2 历史开发 | 6/6 | 172/180（95.56%） | 未按新门禁重算 | 不适用 |
| V3 冻结开发 | 8/10 | 240/300（80%） | 未按新门禁重算 | 不适用 |
| V4 边界 | 12/12 测试通过 | 不做字段准确率汇总 | 不适用 | 0 |
| V5 真实 PDF | 4/8 | 115/240（47.92%） | 1／3 | 0 |
| V6 development | 4/5 | 119/150（79.33%） | 3／1 | 0 |
| V6 calibration | 5/5 | 140/150（93.33%） | 2／3 | 0 |
| V6 holdout（非盲） | 3/5 | 85/150（56.67%） | 2／1，另 2 份模型失败 | 0 |

事实：V3 成功返回的 8 份文档为 240/240；完整保守结果为 240/300，因为两份传输失败必须计入分母。

事实：V5/V6 本轮真实模型调用共 20 次。注册干净 CSV 为 0 次调用，语义 CSV 只处理需要模型判断的字段。

事实：当前门禁在本轮成功输出中没有发现仍被自动直通的关键错误。

结论：B 模块达到了本地开发和 B→C 接口交付目标，但没有达到“生产稳定性验收”或“严格盲测通过”的条件。

### 达到或未达到的原因

事实：达到接口交付目标的原因包括：

- 输出和审查对象都有明确 Pydantic 契约；
- 来源、文件哈希和版本能够追溯；
- 人工确认与修改都有可验证审计事件；
- 未通过门禁的结果无法进入 reviewed adapter；
- C 能正确区分可计算报价和 `PENDING` 报价；
- 校准发现的关键静默错误已由确定性规则阻断。

事实：尚未达到生产验收的原因包括：

- V5 的 4 份 PDF 因费用表头和金额行被拆成不同来源块而失败；
- V3 和 V6 仍出现真实模型传输失败；
- V6 holdout 的参考答案结构在运行前已被查看，不是严格盲测；
- A 的部分参考答案与当前字段契约不一致；
- 当前没有扫描 PDF OCR 真实样本和部署环境验证。

推测：V5 的效果下降主要反映 PDF 表格证据上下文不足，而不是简单增加 prompt 文本就能稳定解决。对 V5-A 的一次更强提示词试验仍然失败，支持这一判断，但样本数量不足以形成最终结论。

### 本次发现的问题与经验

1. 模型返回成功不代表字段可以安全进入采购计算。
2. 字段准确率、文档成功率和自动直通率必须分开报告。
3. 关键静默错误比普通非关键字段差异更值得优先处理。
4. 已保存模型结果可以重跑确定性门禁，无需重复花费模型调用。
5. PDF 表格的表头与当前行应组成联合证据，不能通过放松来源校验解决。
6. 干净 CSV 应优先使用确定性映射，减少费用和传输不确定性。
7. 参考答案也可能违反契约，不能把所有差异都归因于模型。
8. holdout 一旦被用于调试或提前查看答案，就必须降级为开发或非盲诊断集。
9. 人工确认“缺失”不等于补值，更不等于填 0。
10. 人工纠正必须绑定具体文件、报价和字段版本。

建议：下一步优先级应为：

1. 增强 PDF 表格组合来源；
2. 增加模型 HTTP 状态、请求 ID 和阶段延迟诊断；
3. 按上限实现有限重试，同时继续保留首次失败统计；
4. 请 A 修正参考答案中的契约问题；
5. 由 A 新建未暴露 V7 holdout；
6. 再根据新盲测结果决定是否增加模型 reviewer 或 OCR。

建议：目前不优先增加第二个模型 reviewer。现有确定性门禁已将已观察到的关键静默错误降为 0，优先解决解析和传输问题更直接。

## 8. 下游说明

### 下游可以直接使用什么

#### C 可以使用

- `ReviewEnvelope`：判断报价是否已经通过 B 审查。
- `quote_input_from_reviewed_extraction(...)`：安全构造单个 C 输入。
- `compare_reviewed_extractions(...)`：将多个已审查报价交给现有比较规则。
- `ExtractionBatch` 中的字段值、单位、状态和来源。
- `calculation_inputs_complete`：区分可以完整计算和只能输出 `PENDING` 的报价。

#### D 可以使用

- `ReviewEnvelope`、`FieldReviewFinding`、`ReviewEvent` 和 `CorrectionEvent` 的 JSON Schema。
- `review_status` 和 `blocking_fields` 建立人工复核任务。
- `source_ids` 展示 PDF 页码、原文和坐标，或 CSV 行列位置。
- `create_review_event(...)` 处理确认缺失或冲突。
- `apply_candidate_correction(...)` 处理人工输入和人工纠正。
- 稳定错误代码、task/quote/document 版本和哈希做幂等、过期检测与持久化。

### 人工审查目前如何对接

事实：目前已经具备完整的领域层接口，推荐 D 按以下方式包装：

```text
读取 REVIEW_REQUIRED envelope
→ 页面显示 blocking_fields 和 findings
→ 根据 source_ids 展示对应原文
→ 审查人选择确认缺失、确认冲突、补充或纠正
→ D 调用 B 的纯函数生成事件或新 batch
→ D 保存原 batch、事件和新 batch
→ 重新调用 review_extraction_batch
→ READY 后调用 reviewed adapter 交给 C
```

事实：当前仓库尚未实现以下部分：

- 人工审查网页；
- FastAPI 人工复核路由；
- reviewer 登录和权限；
- 数据库人工任务队列；
- ReviewEvent/CorrectionEvent 的数据库持久化；
- LangGraph 中断和恢复节点。

这些属于 D 的职责范围。D 不需要重新编写字段审查规则，只需要调用并持久化 B 已提供的契约和纯函数。

### 使用注意事项

- 只有 `READY_FOR_DOWNSTREAM` 可以进入 reviewed adapter。
- `REVIEW_REQUIRED` 必须人工处理并重新审查，不能直接发送给 C。
- `REJECTED` 需要修复文件、身份、版本、来源或契约问题，不能简单点击“批准”。
- `MODEL_FAILED` 不能转换成所有字段 `MISSING` 的报价。
- `downstream_ready=true` 不一定代表所有计算字段有值；同时检查 `calculation_inputs_complete`。
- `MISSING`、`UNKNOWN` 和 0 是三种不同状态。
- `USER_INPUT` 不能引用文档来源。
- `USER_CORRECTION` 应保留支持纠正的来源。
- 人工事件必须使用带时区时间，并绑定当前版本和哈希。
- 参考答案不能挂载给运行时模型或解析器。
- `.env.local` 和真实模型本地结果不得提交。
- V2 是混合旧 prompt 的历史结果；V3 的 100% 只适用于成功返回的 8 份文档。
- V4 是边界回归，不是完整字段准确率测试。
- V6 holdout 是非盲诊断，不能作为正式盲测成绩。

### 是否与上一版本兼容

事实：`ExtractionBatch` 主结构、字段名称、来源 ID 和 C 原有 `quote_input_from_extraction(...)` 所需候选结构保持兼容。

事实：新版本通过 envelope 包装现有 batch，没有另建第二套报价字段模型。C 现有确定性计算逻辑未被复制或替换。

事实：原有直接入口仍可用于旧测试，但正式联调应切换到 `quote_input_from_reviewed_extraction(...)`，否则会绕过 readiness 保护。

事实：C 旧 post-correction JSON 中的 batch 可以加载；但旧 correction 记录缺少 reviewer、时间、版本、哈希和字段版本时，会被新审查策略识别为审计信息不足。

建议：C/D 联调至少验证以下四条路径：

1. 完整且正确的报价直接进入 C；
2. 缺少运费的报价在人工确认前被阻断；
3. 人工确认确实缺失后进入 C 并得到 `PENDING`；
4. 人工补充 S$200 运费后重新审查，C 得到 Supplier B 总成本 `7000.00`。

最终交付结论：当前版本可以交给 C 和 D 做接口联调；不能描述为生产模型已经验收。正式效果冻结仍需要修正参考答案、解决 PDF 表格来源问题，并运行新的严格盲测集。
