# 成员 B 解析、OCR、模型、证据与审查交付说明（第三版）

日期：2026-09-12

成员：LC（成员 B）

范围：报价 PDF／CSV 解析、页级路由、原生表格、OCR、模型适配、来源证据、候选字段、自动审查、人工复核接口、B→C 安全交付及 V2–V7.2 评测

说明：本文依据当前代码重新编写，作为 `guide_lc2.md` 的后续交付版。所有报价、供应商和器件数据均为合成数据。文中严格区分“代码已实现”“本地验证通过”“真实模型评测结果”和“正式发布门禁通过”；当前 OCR 已实现，但成功率尚未达到100%，仍存在需要改进的地方，需要人工核验，因此不能宣称已经完成任意扫描报价或生产环境验收。

## 1. 本次更新概述

本次更新在第二版的解析、证据、自动审查和人工复核闭环之上，增加了 PDF 逐页质量分析、原生表格原子来源、受控 OCR、混合 PDF 双路径核对、模型失败审计和 V7/V7.2 严格评测工具。

OCR 位于 PDF 解析内部，不改变后续主流程。部署允许 OCR 后，解析器仍然先检查每一页，再仅对 `OCR` 和 `HYBRID` 页面执行本地 OCR；`NATIVE_TEXT` 页面继续走原生文本和表格解析。所有路径最终统一生成 `ParsedInput`、来源、候选字段和 `ReviewEnvelope`。

当前交付状态：

- CSV、原生文本 PDF、原生表格、候选字段、证据校验、人工复核和 B→C reviewed adapter 可以继续交给 C/D 联调。
- OCR 与混合 PDF 的代码、配置、契约、测试和合成数据可以交付；测试或演示环境可显式开启。
- `SUPPLIER_PDF_OCR_ENABLED` 正式默认值仍为 `false`。关闭时，疑似扫描页返回 `pdf_page_requires_ocr`，不会被静默遗漏或伪装为全字段 `MISSING`。
- V7.2 一次性 Holdout 达到 171/180，即 95.00%，但出现 1 个关键静默错误：同一文档同时出现 revision R4/R5，候选只选择 R4，门禁没有把该字段提升为冲突。因此阶段 6 未通过，OCR 尚不能按原定发布规则默认开启。

### 1.1 本次交付结构概览

```text
Team-QQFARM/
├── src/supplier_comparison/
│   ├── extraction/                 B 的解析、OCR 接入、模型、证据与审查实现
│   │   ├── pdf_quality.py          PDF 逐页质量分析与四类路由
│   │   ├── pdf_layout.py           原生文字、表格单元格和上下文分组
│   │   ├── pdf_ocr.py              受控渲染、OCR 限制、行聚合和双路径核对
│   │   ├── pdf_parser.py           统一 PDF 解析入口
│   │   ├── adapters.py             固定输出与真实模型适配器
│   │   ├── contracts.py            ExtractionBatch 1.0/1.1 与来源契约
│   │   ├── evidence.py             来源身份和引用校验
│   │   ├── review.py               确定性审查门禁
│   │   └── human_review.py/...      人工确认和纠正纯函数
│   ├── ocr/                        Tesseract/PP-StructureV3 统一引擎边界和 benchmark
│   └── rules/                      C 的确定性规则及 B→C 小型适配器
├── scripts/                        可复现的路由、OCR、模型和评测命令
├── tests/                          单元、回归、OCR、评测和 B→C 集成测试
├── data/generated/
│   ├── inputs/.../quote_V7/        V7 Development/Calibration/Holdout 合成输入
│   ├── inputs/.../quote_V7_2/      V7.2 Holdout 合成输入
│   └── manifests/                  V7/V7.2 文件哈希、拆分和覆盖标签
├── evaluation/
│   ├── ocr/                        可公开的 OCR development 定义
│   ├── reference/quote_V7*/        公开答案、manifest commitment；不含私有答案
│   └── results/local/              本地真实模型结果，不提交 Git
├── pyproject.toml                  主运行依赖
├── environment-ocr-tools.yml       Tesseract 工具环境版本
├── requirements-ocr-benchmark.txt  PaddleOCR 对照 benchmark 依赖
└── .env.example                    无密钥的配置占位符
```

交付原则：实现代码、脚本、测试、合成输入、manifest、公开 Development/Calibration 答案和 Holdout commitment 随分支提交；密钥、私有 Holdout 答案、本地模型结果、OCR 缓存、虚拟环境和内部阶段审查包只保留本地。

## 2. 主要做了什么

### 2.1 扩展统一报价提取契约

当前核心契约包括：

- `DocumentContext`：由后端提供 task、quote、document、supplier 的权威身份和版本。
- `ParsedInput`：保存文件哈希、parser 版本、fingerprint、页分析、原子来源和上下文分组。
- `PageAnalysis`：保存页码、路由、字符质量、图像面积、页面尺寸和页面图像哈希。
- `EvidenceSource`：保存 PDF/CSV 原子来源、位置和来源类型。
- `OcrMetadata`：保存 OCR 引擎、版本、置信度、DPI、预处理和页面图像 SHA-256。
- `QuoteFieldCandidate`：保存原始值、标准化值、状态、来源、生产者和字段版本。
- `ExtractionRun`：保存模型调用、重试、耗时、失败类别和脱敏指纹。
- `ExtractionBatch`：统一包装解析结果、30 个候选字段和运行记录。
- `ReviewEnvelope`：包装审查状态、finding、人工事件及是否允许进入下游。

`ExtractionBatch` 兼容读取 schema `1.0 | 1.1`：

- CSV 和旧解析产物可继续使用 `1.0`。
- 新 PDF 解析产物使用 `1.1`。
- `1.1` 新增 `PageAnalysis`、parser fingerprint、上下文分组、`PDF_TABLE_CELL` 和 `PDF_OCR_BLOCK`。

模型不能决定 `task_id`、`quote_id`、`document_id` 或版本。这些值由后端注入并在证据与审查阶段再次核验。

### 2.2 增加 PDF 逐页质量分析和安全路由

每份 PDF 在提取字段前逐页统计：

- 非空字符数；
- 可打印字符比例；
- 字母数字比例；
- 页面主要图像面积比例；
- 页面宽高；
- 质量原因码。

默认路由阈值：

- 非空字符不少于 80；
- 可打印字符比例不少于 0.90；
- 字母数字比例不少于 0.50；
- 图像面积达到页面面积的 50% 时视为主要图像。

路由结果：

| 路由 | 含义 | OCR 开启后的行为 |
| --- | --- | --- |
| `NATIVE_TEXT` | 原生文字层可靠，无主要页面图像 | 只执行原生文字和表格解析 |
| `OCR` | 文字层不足，但存在主要页面图像 | 渲染该页并执行 OCR |
| `HYBRID` | 可靠文字和主要图像同时存在 | 同时保留原生来源与 OCR 来源并核对 |
| `MANUAL_REQUIRED` | 空白、低质或无法可靠识别 | 显式失败或转人工，不进入正常候选流程 |

OCR 开关是部署授权，不是路由判断本身：

- `SUPPLIER_PDF_OCR_ENABLED=false`：仍然识别出 OCR/HYBRID 页，但返回 `pdf_page_requires_ocr`。
- `SUPPLIER_PDF_OCR_ENABLED=true`：自动仅处理需要 OCR 的页；原生页不额外 OCR。

每个有效 PDF 页面在 `page_analyses` 中必须恰好出现一次。损坏、加密、超页和超大小输入会在更早的安全边界显式拒绝。

### 2.3 增加原生 PDF 表格与组合证据

原生 PDF 不再只按整行切块，而是基于 word 和坐标生成原子来源：

- 普通文本区域使用 `PDF_TEXT_BLOCK`；
- 表格单元格使用 `PDF_TABLE_CELL`；
- 单元格保留 `table_id`、`row_index`、`column_index`、页码和 PDF point bbox；
- 同一行中的不同字段保持为不同原子来源；
- 重复页眉、跨页表格和多个候选值均可保留。

为帮助模型理解表头和值的关系，解析器另外生成不可引用的 `EvidenceContextGroup`：

- `FIELD_AND_VALUE`：标签与值；
- `TABLE_ROW`：同一表格行；
- `ADJACENT_EXPLANATION`：相邻说明。

模型只能引用真实原子来源，不能引用 context group，也不能把人为组合内容冒充原文。V5 A/E/F/H 的旧表格上下文错误已通过该设计恢复为 4/4 成功 batch，119/119 个候选引用均可定位。

### 2.4 接入受控 OCR 和混合 PDF

阶段 3 在同一 development 数据和目标硬件下比较了 Tesseract 与 PP-StructureV3。Tesseract 5.5.1 是唯一满足全部冻结门槛的候选，因此阶段 4 将其接入 `PdfQuoteParser`。

当前 OCR 范围：

- 英文打印体；
- 清晰扫描 PDF 和包含扫描页的混合 PDF；
- 最多 5 页／5 MB；
- 默认 300 DPI；
- 每页最多 12 MP、每份文档最多 50 MP；
- 每页 30 秒、每份文档 120 秒；
- OCR worker 内串行执行；
- 手写、严重透视、严重遮挡、极低清晰度和多语言转人工。

实现行为：

- 使用 `pypdfium2` Python API 渲染，不使用用户文件名拼接 shell 命令。
- Tesseract 使用参数数组执行，严格校验版本为 `tesseract 5.5.1`。
- OCR 单词在内部聚合为行后生成 `PDF_OCR_BLOCK`，避免 prompt 膨胀。
- OCR 来源使用像素坐标，保存行内最低 token 置信度、DPI、预处理步骤和页面图像 SHA-256。
- `HYBRID` 页面同时保留 native 与 OCR 原子来源。
- 金额、数量、日期和料号在 native/image 两条路径不一致时，生成 `pdf_native_image_conflict` 阻塞 finding。
- 关键字段引用的最低 OCR 置信度低于 0.90，或引擎不能提供置信度时，生成阻塞 finding。
- 不静默纠正 `0/O`、`1/I/l`、小数点、日期或料号字符。

稳定失败包括 OCR 引擎缺失、版本错误、超时、无有效内容、像素超限、输出过长、页面图像不一致和 OCR 崩溃。这些错误不会转换成一份全字段 `MISSING` 的正常报价。

### 2.5 保持 CSV 确定性与语义混合路径

CSV 继续只支持已登记的固定模板和版本化 profile：

- 明确的结构化字段由确定性解析器直接映射；
- 付款条款、费用语义、范围型交期和起算事件等复杂字段才交给模型；
- 干净的注册 CSV 可以 0 次模型调用完成；
- 未知或错误表头显式拒绝。

OCR 只影响 PDF 解析入口，不改变 CSV schema、来源类型或后续审查逻辑。

### 2.6 更新统一模型适配层

模型层提供固定输出和 OpenAI-compatible 两种适配器。当前真实评测配置为：

- provider：SiliconFlow；
- model：`deepseek-ai/DeepSeek-V4-Flash`；
- adapter：`openai-compatible/1.2.0`；
- prompt：`quote-extraction/2.1.0`；
- review：`extraction-review/1.2.0`。

适配器负责 provider 特有请求、结构化响应和错误映射；业务契约不依赖 provider。

每份文档/报价是独立 logical graph run，各自最多 8 次模型调用；每个适配器阶段最多 3 次尝试。V7.2 已验证 6 份 Holdout 各自拥有不同的 `logical_graph_run_id`，不会因批量处理共享预算而让后面的文档失去调用额度。

### 2.7 加强模型稳定性、审计和脱敏

当前模型运行记录包括：

- prompt 构造、等待响应、解码、结构校验、证据校验及总耗时；
- 每次 attempt 的调用序号、结果、HTTP 状态、请求 ID 和重试等待；
- 请求指纹、响应哈希和长度；
- 成功或失败状态；
- `PARSING / OCR / TRANSPORT / MODEL_STRUCTURE / EVIDENCE` 五类失败。

重试规则：

- 429 和临时 5xx 可有限重试；
- 支持受上限约束的 `Retry-After`；
- 退避加入随机抖动；
- 400、401、403、结构失败、来源伪造和证据失败不重试。

普通错误和日志不保存 prompt、报价全文、Authorization 或 API key。只有显式配置本地诊断目录时才保存完整 provider body 或原始模型输出，并强制目录 0700、文件 0600。该目录及 `evaluation/results/local/` 不提交 Git。

### 2.8 限制模型来源引用

解析器先把本文件来源映射为短句柄，如 `S001`。模型只能选择当前输入中的句柄，后端再绑定真实 `source_id` 和原文。

以下情况会被拒绝：

- 不存在的来源；
- 其他报价、文档、版本或文件哈希的来源；
- context group 被当作原子来源引用；
- 模型伪造 quoted text；
- 来源位置或语义不支持字段；
- 跨文件引用。

最终 `quoted_text` 由后端从解析器原文生成，不信任模型复述。

### 2.9 保持确定性归一化

确定性规则负责：

- 使用 `Decimal` 语义处理金额，并在 JSON 中使用十进制字符串；
- 合法金额统一为两位小数；
- 明确的日期、单位、枚举和部分 `Net N` 表达归一化；
- `MISSING` 保持空值、空来源；
- `UNKNOWN` 费用金额保持 `null`；
- 冲突保持 `CONFLICT`，不猜测唯一值。

解析模块不执行采购量、MOQ、成本、预算、可行性或排序公式，这些仍由 C 的确定性规则负责。

### 2.10 保持五层自动审查门禁

审查顺序仍为：

1. 候选字段集合和 Pydantic 结构；
2. 系统身份、版本和字段生产者；
3. 来源身份、引用范围和证据语义；
4. 字段值、单位、枚举及跨字段关系；
5. 当前适用关键字段是否已审查并可安全交付。

OCR 在第三、四、五层增加了来源元数据、置信度和 native/image 冲突检查，但没有绕过或替换原门禁。

顶层状态：

| 状态 | 含义 | 后续处理 |
| --- | --- | --- |
| `READY_FOR_DOWNSTREAM` | 当前门禁通过 | 可进入 B→C reviewed adapter |
| `REVIEW_REQUIRED` | 存在缺失、冲突、低置信度或语义风险 | 人工处理后重跑完整门禁 |
| `REJECTED` | 文件、身份、版本、契约或来源非法 | 修复输入/系统问题 |
| `MODEL_FAILED` | 模型调用没有得到合法候选 | 保留失败记录并按失败处理 |

`downstream_ready` 与 `calculation_inputs_complete` 不等价。人工确认真实缺失后，可以允许将报价安全交给 C，但 C 仍必须输出 `PENDING`。

### 2.11 保持人工复核闭环

当前提供领域层纯函数和 JSON 契约，不包含网页或数据库：

| 操作 | 使用场景 | 结果 |
| --- | --- | --- |
| `CONFIRM_MISSING` | 确认文档确实未提供字段 | 保留 `MISSING`，记录 `ReviewEvent` |
| `CONFIRM_CONFLICT` | 确认文档冲突且无法裁决 | 保留 `CONFLICT`，记录来源和事件 |
| `USER_INPUT` | 授权人员补充文档外信息 | 生成新字段版本，origin 为 `USER_INPUT` |
| `USER_CORRECTION` | 人工依据原文纠正提取错误 | 生成新字段版本，origin 为 `USER_CORRECTION` |

每个事件绑定 reviewer、时间、task revision、quote/document 版本、文件哈希和字段版本。人工事件不能直接绕过门禁；产生新 batch 或事件后必须重新执行完整审查。

### 2.12 保持 B→C 安全适配器

正式入口为：

- `quote_input_from_reviewed_extraction(...)`；
- `compare_reviewed_extractions(...)`。

只有 `ReviewEnvelope.downstream_ready=true` 才能构造 C 的 `QuoteInput`。OCR 的页码、坐标、置信度和图像哈希留在 B/D 证据层，不新增 C 的计算字段，也不改变 Decimal、MOQ、包装、费用或成本边界。

旧的 `quote_input_from_extraction(...)` 仍为兼容入口，但正式联调不应使用它绕过 readiness 保护。

### 2.13 建立 V7/V7.2 评测和冻结工具

当前新增并交付：

- 页路由审计；
- OCR development 集合构建和双引擎 benchmark；
- OCR 离线启动检查；
- V7 parser/OCR 运行与离线评分；
- Development/Calibration 真实模型运行与离线评分；
- 配置、组件和数据哈希冻结；
- Holdout 一次性 marker 和运行器；
- Holdout 答案发布后的承诺哈希核验与离线评分；
- 保存结果的确定性 review 重放。

运行时提取程序不会加载参考答案。Development/Calibration 参考答案只由离线评分器读取；私有 Holdout 答案不得提交仓库、挂载给运行时或发送给模型。

## 3. 实现思路

### 3.1 主流程

```text
文件校验、大小/页数限制和 SHA-256
                    ↓
          判断输入类型 PDF / CSV
                    ↓
        ┌───────────┴───────────┐
        │                       │
        │ PDF                   │ 注册 CSV
        ↓                       ↓
逐页质量分析与四类路由       固定模板/profile 校验
        ↓                       ↓
┌───────┼───────────┐       确定性字段映射
│       │           │           │
│NATIVE │ OCR       │HYBRID     │
│       │           │           │
↓       ↓           ↓           │
原生文字 受控渲染     原生文字+OCR │
和表格   Tesseract   双路径核对   │
│       │           │           │
└───────┴─────┬─────┘           │
              ↓                 │
  原子来源、坐标、页码、置信度   │
  parser fingerprint、上下文分组 │
              └────────┬────────┘
                       ↓
            确定性字段直接映射
                       ↓
            复杂语义字段交给模型
                       ↓
             模型选择本文件来源句柄
                       ↓
             后端绑定真实来源和原文
                       ↓
       Pydantic 结构校验与确定性归一化
                       ↓
       五层自动审查（含 OCR 风险门禁）
                       ↓
┌──────────────────────┬──────────────────┬──────────────┐
│ READY_FOR_DOWNSTREAM │ REVIEW_REQUIRED  │ REJECTED /   │
│                      │                  │ MODEL_FAILED │
└──────────┬───────────┴────────┬─────────┴──────┬───────┘
           │                    │                │
           ↓                    ↓                ↓
      安全适配给 C       人工确认或人工修正     修复或有限重试
                                ↓
                         重新执行完整门禁
                                ↓
                         通过后再交给 C
```

OCR 只扩展了“PDF 解析并形成可信来源”这一段。模型适配、候选字段、证据绑定、Pydantic、自动审查、人工复核和 B→C 接口仍沿用原架构。

### 3.2 自动审查和人工审查的职责

自动审查负责可重复判断：

- 文件、schema、字段集合和版本是否合法；
- 页面是否被路由、OCR 元数据是否完整；
- 来源是否属于当前文件并可定位；
- OCR 关键来源是否低于置信度阈值；
- native/image 是否出现关键 token 冲突；
- 值、单位、费用组合、交期组合和候选状态是否一致。

人工负责需要业务判断的内容：

- 文档是否确实缺失；
- 多个读数如何解释；
- 低置信度 OCR 原文是否可确认；
- 外部授权信息的补充；
- 模型或 OCR 错误的纠正。

当前 V7.2 暴露的瓶颈是：自动门禁尚未全面扫描“同一字段在全部原子来源中出现多个合法值”。这与已经存在的人工复核机制并不冲突；正确处理方式是先由自动规则发现并阻塞，再交给人工裁决，而不是期待人工无提示地重新阅读整份文档。

### 3.3 关键安全边界

- OCR feature flag 关闭时，扫描页必须显式报错，不能静默漏页。
- OCR feature flag 开启后，只 OCR 路由要求的页面，不把 OCR 作为所有 PDF 的强制前置步骤。
- 模型输出只能是候选，不能自行标记 `VERIFIED`。
- 原子来源可引用；上下文分组不可引用。
- `MISSING`、`UNKNOWN` 和数值 0 含义不同。
- 低置信度 OCR 候选可以保持 `EXTRACTED`，但不能直通。
- OCR 或模型失败不能转换成正常空报价。
- 人工补充不能伪造文档来源。
- 参考答案和人工纠正结果不得进入运行时 prompt。
- 每份文档独立计算 8 次模型调用预算。
- B 不实现 C 的采购计算，也不实现 D 的数据库和工作流。

## 4. 相比上一版本更新了什么

### 4.1 PDF 从文档级文本读取升级为逐页路由

上一版主要面向机器生成文本 PDF。当前版在解析前对每一页做质量分析，可以识别原生文字页、扫描页、混合页和需要人工处理的页面，并确保每页都有明确记录。

### 4.2 原生表格从整行证据升级为原子单元格

新增坐标化的 `PDF_TABLE_CELL` 和不可引用上下文组，修复表头和值分离造成的证据语义断裂，同时避免把同行多个字段拼成虚构原文。

### 4.3 新增 OCR，但没有改变后续业务接口

新增 Tesseract 5.5.1、本地受控渲染、OCR bbox/置信度/图像哈希和混合页核对。新信息保存在 schema 1.1 的解析和来源层，候选字段集合与 C 的计算输入没有变化。

### 4.4 新增 OCR 专用审查规则

- 关键字段 OCR 置信度低于 0.90 时阻塞；
- native 与可见图像关键内容冲突时阻塞；
- OCR 引擎、渲染、超时、像素和输出异常显式失败；
- 图片中的 prompt injection 只能作为数据，不能改变系统指令。

### 4.5 新增模型运行审计和脱敏

模型成功与失败现在都有统一运行记录，包含分段耗时、调用预算、attempt、请求 ID、哈希和失败分类；普通日志不再保存报价全文或 provider body。

### 4.6 模型预算改为每文档独立

V7.1 曾让 6 份 Holdout 共享 8 次预算，前 5 份重试后耗尽额度，导致第 6 份未调用。D 确认每份文档/报价是独立 logical graph run 后，V7.2 改为每文档独立预算，6/6 Holdout 均完成且各使用 1 次调用。

### 4.7 新增 V7/V7.2 数据与严格评测链

新增 Development、Calibration、Holdout 合成 PDF，覆盖 native、scan、hybrid、轻度低质、冲突、缺失和 prompt injection，并通过 manifest 固定文件哈希、数量和参考答案 commitment。

### 4.8 发布状态比上一版更明确

当前工程能力更强，但没有用“95%”掩盖关键静默错误。V7.2 达到最低字段准确率仍未通过发布门禁，OCR 正式默认值保持关闭。下游可以进行接口和测试环境联调，但不能把它描述为已完成生产级 OCR 验收。

## 5. 为什么这样修改

### 5.1 杜绝扫描页静默遗漏

旧文本解析器可能无法从图片页取得内容。逐页路由先发现页面类型，OCR 关闭时也会显式返回 `pdf_page_requires_ocr`，因此不会把扫描页错误理解为报价字段全部缺失。

### 5.2 OCR 应是解析能力，不应污染下游业务契约

OCR 的作用是把页面图像转换为可定位的原子来源。它不应该改变报价字段、费用语义、MOQ 或成本公式。把 OCR 放在 PDF parser 内部，可以让后续证据、模型、审查和人工闭环复用同一套接口。

### 5.3 仅有 OCR 文本仍不足以安全计算

扫描识别可能出现小数点、`0/O`、`1/I/l`、日期和料号错误。保存 token 置信度和页面坐标，并在关键字段低置信度时阻塞，能够让人工直接查看对应区域，而不是盲目相信纯文本结果。

### 5.4 混合 PDF 必须核对可见图像和隐藏文字层

隐藏文字层可能过期或与可见扫描不同。`HYBRID` 双路径可以发现价格、数量、日期和料号冲突，并将其转为人工复核，而不是默认信任其中一条路径。

### 5.5 表格语义需要上下文，但证据必须保持原子性

模型需要知道“哪个表头对应哪个值”，审计又要求引用真实原文。因此当前设计把阅读上下文和可引用来源分开：context group 帮助理解，候选仍引用真实单元格。

### 5.6 人工复核不能替代自动发现问题

人工复核是处理已识别风险的闭环，不应要求审查者每次重新发现所有隐藏冲突。V7.2 的 revision 错误说明当前还需要增加确定性的同字段多值扫描；在此之前保持 OCR 默认关闭是对既定发布门槛的遵守。

### 5.7 保持评测和失败记录诚实

失败文档和错误字段全部保留在分母中，一次性 Holdout 不因失败重跑，Development/Calibration、固定输出、真实 SiliconFlow 和主办方环境结果分别报告。

## 6. 使用方法与交付物

### 6.1 安装主 Python 环境

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

`Pillow` 和 `pypdfium2` 已是主运行依赖。Tesseract 是独立系统工具，不由 pip 安装。

### 6.2 安装 OCR 工具

冻结的 Tesseract 工具版本记录在：

```text
environment-ocr-tools.yml
```

可使用 Conda 创建工具环境：

```bash
conda env create -f environment-ocr-tools.yml
```

`requirements-ocr-benchmark.txt` 只用于复现 PP-StructureV3 对照 benchmark，不是默认运行 OCR 所必需。

### 6.3 配置 PDF 和 OCR

默认安全配置见 `.env.example`：

```env
SUPPLIER_PDF_OCR_ENABLED=false
SUPPLIER_PDF_OCR_RENDER_DPI=300
SUPPLIER_PDF_OCR_MAX_PIXELS_PER_PAGE=12000000
SUPPLIER_PDF_OCR_MAX_PIXELS_PER_DOCUMENT=50000000
SUPPLIER_PDF_OCR_PAGE_TIMEOUT_SECONDS=30
SUPPLIER_PDF_OCR_DOCUMENT_TIMEOUT_SECONDS=120
SUPPLIER_PDF_OCR_MAX_CHARACTERS=50000
SUPPLIER_PDF_OCR_TESSERACT_BINARY=/path/to/tesseract
SUPPLIER_PDF_OCR_TESSDATA_DIR=/path/to/tessdata
```

测试或演示环境需要扫描 PDF 时，显式设置：

```env
SUPPLIER_PDF_OCR_ENABLED=true
```

开启后由页级路由自动决定哪些页面执行 OCR，无需调用者先判断文件类型。

### 6.4 运行全量测试

```bash
.venv/bin/python -m pytest -q
```

本次最终验证结果为 `349 passed`。测试覆盖 PDF/CSV、页路由、表格、OCR、模型适配、脱敏、证据、审查、人工复核、评测工具和 B→C 集成。真实 provider 和真实 OCR benchmark 不由普通单元测试自动调用。

### 6.5 审计 PDF 页面路由

```bash
PYTHONPATH=src .venv/bin/python scripts/audit_pdf_page_routes.py \
  data/generated/inputs/development/quote_V7 --pretty
```

该命令不调用模型，也不主动执行 OCR；适合确认页面路由和显式错误。

### 6.6 在代码中解析 PDF

```python
from supplier_comparison.extraction import DocumentContext
from supplier_comparison.extraction.pdf_parser import PdfQuoteParser

context = DocumentContext(
    task_id="TASK-001",
    task_revision=1,
    scenario_id="MCU-DEMO-001",
    quote_id="QUOTE-001",
    quote_version=1,
    document_id="DOC-001",
    document_version=1,
    supplier_id="SUPPLIER-001",
)

parsed = PdfQuoteParser().parse("supplier_quote.pdf", context)
```

当 OCR flag 开启时，此入口会自动处理 OCR/HYBRID 页；调用方不需要另建 OCR parser。

### 6.7 提取候选字段

```python
from supplier_comparison.extraction.adapters import ModelCallBudget
from supplier_comparison.extraction.service import extract_quote_candidates

budget = ModelCallBudget(max_calls=8)
batch = extract_quote_candidates(
    parsed,
    dictionary,
    adapter,
    budget,
    extraction_run_id="extract-run-001",
)
```

批量处理时，每份文档必须创建独立 `ModelCallBudget(max_calls=8)` 和独立 logical graph run，不能在多份报价之间共享预算。

### 6.8 自动审查

```python
from supplier_comparison.extraction import CriticalityContext, review_extraction_batch

envelope = review_extraction_batch(
    batch,
    dictionary,
    CriticalityContext(
        required_revision=requirement.revision,
        base_unit=requirement.base_unit,
    ),
    input_is_synthetic=True,
)
```

上层系统应读取：

```python
envelope.review_status
envelope.downstream_ready
envelope.calculation_inputs_complete
envelope.review.blocking_fields
envelope.review.findings
```

### 6.9 人工确认和纠正

确认真实缺失或未裁决冲突：

```python
review_event = create_review_event(
    batch,
    field_name="shipping_fee_status",
    action=HumanReviewAction.CONFIRM_MISSING,
    reviewer_id="reviewer-001",
    reviewed_at=reviewed_at,
)
```

补充或纠正字段：

```python
corrected_batch, correction = apply_candidate_correction(
    batch,
    field_name="shipping_fee_amount",
    action=CorrectionAction.USER_INPUT,
    raw_value="S$200 supplied by authorized user",
    normalized_value="200.00",
    unit="SGD",
    reason_code="SUPPLIER_FOLLOW_UP",
    reason="Authorized follow-up confirmed shipping fee.",
    reviewer_id="reviewer-001",
    reviewed_at=reviewed_at,
)
```

之后必须携带事件重新调用 `review_extraction_batch(...)`。

### 6.10 安全交给 C

```python
from supplier_comparison.rules.integration import (
    compare_reviewed_extractions,
    quote_input_from_reviewed_extraction,
)

quote_input = quote_input_from_reviewed_extraction(envelope)
```

未达到 `READY_FOR_DOWNSTREAM` 时，该入口会拒绝构造计算输入。

### 6.11 V7/V7.2 数据和评测命令

运行 parser/OCR 边界：

```bash
PYTHONPATH=src .venv/bin/python scripts/run_v7_parser_ocr.py \
  --manifest data/generated/manifests/quote_V7_manifest.json \
  --split development \
  --output-dir evaluation/results/local/v7-parser-ocr
```

运行真实模型需要用户授权并从 `.env.local` 注入密钥：

```bash
set -a
source .env.local
set +a

PYTHONPATH=src .venv/bin/python scripts/run_v7_model_evaluation.py \
  --manifest data/generated/manifests/quote_V7_manifest.json \
  --split development \
  --output-dir evaluation/results/local/v7-development-model
```

Holdout runner 带一次性 marker 和 freeze 校验。V7/V7.2 现有 Holdout 已使用，不能通过更换目录再次运行；这些命令仅作为可复现实现交付，不授权重跑已暴露 Holdout。

### 6.12 Git 交付和本地保留范围

应上传：

- `src/` 中本次实现；
- `scripts/` 中所有源代码工具；
- `tests/` 中所有新增和修改测试；
- V7/V7.2 合成输入和 manifest；
- V7 Development/Calibration 参考答案；
- V7/V7.2 Holdout commitment；
- `evaluation/ocr/v7_stage3_development_set.json`；
- `pyproject.toml`、`.env.example`、OCR 环境声明和本指南。

只留本地：

- `.env.local` 和任何密钥；
- `evaluation/results/local/`；
- `.ocr-cache/`、`.ocr-tools/`、`.venv*`；
- `docs/v7/` 阶段执行审查包；
- `docs/assets/`；
- `holdout_reference_answers.json`；
- 本地备份和个人计划。

## 7. 结果分析

### 7.1 当前验证结果

阶段 3 OCR benchmark：

| 指标 | Tesseract 5.5.1 | PP-StructureV3 |
| --- | ---: | ---: |
| 关键 token 精确率 | 64/64，100% | 62/64，96.875% |
| 表头/值关联 | 31/32，96.875% | 21/32，65.625% |
| P95 单页时间 | 1.299 秒 | 26.781 秒 |
| 20 页耐久 | 20/20 | 20/20 |
| 选型结论 | 通过并选用 | 准确率门槛失败 |

V7.2 冻结前真实模型结果：

| 数据组 | 文档成功 | 字段结果 | 关键静默错误 | 来源身份/定位 |
| --- | ---: | ---: | ---: | ---: |
| Development | 8/8 | 234/240，97.50% | 0 | 245/245，100% |
| Calibration | 6/6 | 174/180，96.67% | 0 | 178/178，100% |

V7.2 一次性 Holdout：

| 指标 | 结果 | 门槛 | 判定 |
| --- | ---: | ---: | --- |
| 文档运行成功率 | 6/6，100% | 扫描 PDF ≥90% | 通过 |
| 页面路由覆盖 | 13/13 | 100% | 通过 |
| 来源身份合法率 | 174/174，100% | 100% | 通过 |
| 来源坐标可定位率 | 174/174，100% | 100% | 通过 |
| 字段准确率 | 171/180，95.00% | ≥95% | 临界通过 |
| 关键静默错误 | 1 | 0 | 失败 |
| 模型调用 | 6 次，0 重试 | 每文档 ≤8 | 通过 |

按 Holdout 页面类型统计：

- 原生文本：56/60，93.33%；
- OCR：56/60，93.33%；
- 混合 PDF：59/60，98.33%。

真实 Holdout 使用 SiliconFlow `deepseek-ai/DeepSeek-V4-Flash`，OCR 使用本地 Tesseract 5.5.1；没有固定输出，没有向模型发送参考答案或人工纠正结果，也没有在 AWS/主办方环境运行。

### 7.2 是否达到预期

已经达到：

- 图片页静默遗漏为 0；
- OCR、原生和混合页面统一进入既有候选及审查流程；
- 页码、bbox、OCR 置信度和图像哈希可追溯；
- OCR 失败不会伪装为正常空报价；
- 每份文档独立模型预算；
- V7.2 文档成功率、来源合法率、坐标定位率和最低字段准确率达标；
- 提示注入样本未改变来源权限或审批状态。

尚未达到：

- Holdout 关键静默错误必须为 0；
- 关键字段证据语义人工支持率 100% 的最终确认；
- 主办方/AWS 环境验证；
- 阶段 7 的 C/D 四条正式集成路径；
- OCR 默认开启的发布批准。

因此当前不能表述为“阶段 6 通过”或“生产环境 OCR 已验收”。

### 7.3 当前瓶颈

当前主要瓶颈不是 OCR 能否读出文字，也不是模型调用预算。V7.2 的直接阻塞是确定性门禁没有对所有原子来源进行同字段多值冲突检测：

```text
文档中同时出现 revision R4 与 R5
        ↓
模型只选择 R4 来源并输出 EXTRACTED
        ↓
现有门禁只检查候选引用和部分跨字段规则
        ↓
没有发现未被引用的 R5
        ↓
revision 成为关键静默错误
```

该问题应通过小范围、可解释的确定性规则修复：至少覆盖 revision、料号、金额、数量和日期。当多个合法原子来源给出不同关键值且规则不能裁决时，强制 `CONFLICT` 并进入人工复核。

不应采用以下方式绕过：

- 删除或降低 revision 的关键性；
- 放松关键静默错误为 0 的门槛；
- 人工改写 V7.2 原输出后宣称 Holdout 通过；
- 重跑已经暴露的 V7.2 Holdout；
- 对所有报价永久强制人工全文复读。

### 7.4 当前现实交付建议

可以立即交付：

- native PDF、注册 CSV 和原生表格路径；
- schema 1.0/1.1；
- `ReviewEnvelope`、人工事件和 reviewed adapter；
- OCR 代码、数据、配置和测试环境能力；
- D 的页面证据和 OCR 元数据持久化接口联调。

建议运行策略：

- 正式默认继续 `SUPPLIER_PDF_OCR_ENABLED=false`；
- 开发、测试和演示可设为 `true`；
- 如果业务必须在正式环境提前试用 OCR，应由团队明确批准受限发布，并暂时将所有包含 `OCR/HYBRID` 页的报价强制进入人工复核，不能自动直通 C；这属于新的策略变更，需要代码和 D 流程确认，当前版本没有默认实施。

## 8. 下游说明

### 8.1 C 可以直接使用什么

- `ReviewEnvelope` 判断当前报价是否经过 B 的完整门禁；
- `quote_input_from_reviewed_extraction(...)` 构造单个安全计算输入；
- `compare_reviewed_extractions(...)` 比较多份已审查报价；
- 候选字段中的标准化值、单位、状态和 origin；
- `calculation_inputs_complete` 区分完整计算与 `PENDING`。

C 不需要解析 OCR 元数据，不需要识别 PDF，也不需要改变 Decimal、MOQ、包装、费用、交期或成本规则。

### 8.2 D 需要注意的接口变化

D 需要兼容读取：

- `ExtractionBatch.schema_version = 1.0 | 1.1`；
- `ParsedInput.parser_fingerprint`；
- `ParsedInput.page_analyses`；
- `ParsedInput.context_groups`；
- `SourceKind.PDF_TABLE_CELL`；
- `SourceKind.PDF_OCR_BLOCK`；
- `EvidenceSource.coordinate_space`；
- `EvidenceSource.ocr_metadata`；
- `ExtractionRun` 新增的分段耗时、attempt、失败分类和请求指纹字段。

D 的持久化与 UI 应做到：

- 不丢弃未知的 1.1 字段；
- 按 parser fingerprint 创建新解析版本，不覆盖旧来源；
- OCR 页面图像使用不可覆盖版本路径并记录 SHA-256；
- 人工复核时展示原文、页码、bbox、坐标空间和 OCR 置信度；
- 每份 document/quote 建立独立 logical graph run 和独立 8 次预算；
- 恢复运行时延续该文档的累计调用数；
- 普通 API 错误不返回报价全文或 provider body；
- 受控诊断工件与普通日志、数据库字段隔离。

### 8.3 人工审查对接流程

```text
读取 REVIEW_REQUIRED envelope
        ↓
显示 blocking_fields 和 findings
        ↓
根据 source_ids 展示 PDF/CSV 原文
OCR 来源同时展示页面区域和 confidence
        ↓
审查人确认缺失/冲突，或补充/纠正字段
        ↓
D 调用 B 的纯函数生成事件或新 batch
        ↓
D 保存原 batch、事件和新 batch
        ↓
重新运行 review_extraction_batch
        ↓
READY 后使用 reviewed adapter 交给 C
```

当前仓库不包含人工审查网页、FastAPI 路由、数据库任务队列、权限系统或 LangGraph 中断恢复节点；这些仍由 D 实现。D 不需要重新实现字段门禁，只需持久化并调用 B 的契约和纯函数。

### 8.4 使用注意事项

- OCR 开启后由 parser 自动逐页路由，调用方不要先把整份 PDF 强制转图片。
- `NATIVE_TEXT` 页面保持原生解析，不受 OCR 引擎性能影响。
- OCR 关闭时正确处理 `pdf_page_requires_ocr`，不要转成全字段 `MISSING`。
- 只有 `READY_FOR_DOWNSTREAM` 可以进入 reviewed adapter。
- `REVIEW_REQUIRED` 必须人工处理并重新审查。
- `REJECTED` 需要修复输入、身份、版本或来源问题，不能点击批准绕过。
- `MODEL_FAILED` 必须保留失败和调用记录。
- `downstream_ready=true` 不一定代表计算信息齐全，同时检查 `calculation_inputs_complete`。
- OCR confidence 是风险信号，不是模型置信度，也不能单独证明语义正确。
- `USER_INPUT` 不得伪造文档来源；`USER_CORRECTION` 应绑定支持纠正的原子来源。
- 每份文档使用独立模型预算，批次只负责汇总。
- 私有 Holdout 答案不得上传或挂载给运行时。

### 8.5 与第二版的兼容性

保持不变：

- 30 个业务候选字段及其核心语义；
- `validation_status` 和 `origin`；
- Decimal 字符串、MOQ、包装和费用边界；
- `ReviewEnvelope`、人工事件和 reviewed adapter 的总体使用方式；
- C 的确定性计算职责。

新增但向后兼容：

- PDF batch 从 schema 1.0 扩展为 1.1；
- 新来源类型和 OCR 元数据；
- 页级分析和 parser fingerprint；
- 模型运行审计字段。

需要注意：新 parser 版本为 `pdfplumber-ocr/3.0.0`，fingerprint 和来源 ID 会相应改变。D 不能让新解析复用旧 parser 的来源缓存，也不能把 OCR 重跑覆盖为同一解析版本。

### 8.6 建议的最小联调场景

1. 原生文本 PDF：不调用 OCR，正常生成 schema 1.1 并进入 C。
2. OCR 关闭的扫描 PDF：返回 `pdf_page_requires_ocr`，没有正常空报价。
3. OCR 开启的清晰英文扫描 PDF：生成 `PDF_OCR_BLOCK`、bbox、confidence 和图像哈希。
4. 混合 PDF：native 与 image 冲突时进入 `REVIEW_REQUIRED`。
5. OCR 低置信度关键料号：候选可保留，但不能直通 C。
6. 文档缺运费：人工确认缺失后进入 C 并保持 `PENDING`。
7. 授权补充 S$200 运费：重新审查后由 C 得到 Supplier B 总成本 `7000.00`。
8. OCR/模型失败：保存稳定错误和运行审计，不生成全字段 `MISSING`。

最终交付结论：当前版本可以交给 C/D 做代码、schema、证据展示、人工复核和测试环境 OCR 联调；native PDF 与 CSV 路径可继续使用。OCR 已经作为 PDF 解析内部的自动页级分支实现，但因 V7.2 仍有 1 个关键静默多值冲突，正式默认开关保持关闭。
