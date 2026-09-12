# Supplier Comparison 数据与测试指南（JUNNAN）

- 更新日期：2026-09-12
- 数据字典版本：`1.2.0`
- 范围：成员 A（数据、答案与验收）和成员 B（PDF/CSV 解析、OCR、模型适配与证据审查）

## 1. 当前成果

目前已经完成并提交：

1. 将原混合宽表拆分成供应商报价、采购需求、系统与证据三份数据字典。
2. 整理 `quote_V1` 至 `quote_V7.2` 测试数据，覆盖基准输入、现实字段差异、31 字段直接表达、极端边界、分集泛化和 OCR 路由测试。
3. 建立版本化参考答案；V2 至 V6 已提交完整答案。`lc` 分支对 V7 只提交开发、校准答案和 V7/V7.2 留出答案哈希，运行时不得读取任何参考答案。

这些条目描述数据、测试输入和验收口径，不应单独用来推断代码或生产验收状态。B 的解析、OCR、模型、证据和审查实现现状见 [`guide_lc3.md`](guide_lc3.md)；API、数据库和 AWS/主办方环境仍需由对应负责人另行集成和验证。

## 2. 三份数据字典

| 文件 | 字段数 | 用途 | 状态 |
| --- | ---: | --- | --- |
| [`quote_data_field.csv`](../../data/contracts/quote_data_field.csv) | 31 | 供应商身份、规格、价格基数、包装、MOQ、费用、交期和商务条件 | 1.2.0 |
| [`procurement_requirement_fields.csv`](../../data/contracts/procurement_requirement_fields.csv) | 19 | 买方规格、数量、预算、交付要求和比较偏好 | 1.2.0 |
| [`system_evidence_fields.csv`](../../data/contracts/system_evidence_fields.csv) | 29 | 任务、版本、文件、字段候选、来源、核验、快照和测试溯源 | 1.2.0 |

三份 CSV 均使用相同的 19 列说明结构，包括字段名称、类型、业务含义、示例、必填级别、缺失处理、别名、标准化规则、歧义边界、证据要求、来源分类、责任方和字典版本。

| 文件 | 数据行 | 列数 | SHA-256 |
| --- | ---: | ---: | --- |
| `quote_data_field.csv` | 31 | 19 | `2d011d64c821ae4d124d4dcf046732b7f3f85449b82312d78f5dadc1b878fb55` |
| `procurement_requirement_fields.csv` | 19 | 19 | `b2d7087a60d7fe681f3a7f5a015a8245ccec1480d92663ce540e03837b9a002b` |
| `system_evidence_fields.csv` | 29 | 19 | `2ed5892b38cdc0ad8114987e4682e8b0b5479b6d8e961c2050c5d8bf858893bf` |

`quote_data_field.csv` 的权威哈希按 Git 仓库中的 LF 字节计算。Windows 工作区若启用 CRLF 转换，直接对工作副本计算可能得到不同结果，不能作为冻结标识。

### 2.1 数据归属

| 对象 | 回答的问题 | 分工 |
| --- | --- | --- |
| 采购需求 | 买方要买什么、数量预算多少、何时到货 | A 定义；C 校验；D 保存 |
| 供应商报价 | 供应商提供什么、如何计价、有哪些订购和交付条件 | A 定义；B 提取；C 校验 |
| 系统与证据 | 数据属于哪个任务、文件和版本，来源在哪里，是否已核验 | A 定义；B/C 产生；D 持久化 |

同名的采购需求字段和报价字段必须分别保存。不得用采购需求、历史订单、其他供应商、示例值或参考答案填补当前报价缺失项。

## 3. V1–V7.2 测试数据

V1–V5 开发输入放在 `data/generated/inputs/development/quote_Vx/`。V6、V7 按开发、校准和留出集隔离存放。这里的版本号表示测试设计演进，不等同于业务 `quote_version`。

| 版本 | 已提交内容 | 主要用途 |
| --- | --- | --- |
| V1 | 3 份供应商 PDF、3 份异构 CSV、1 份固定模板 `quotes.csv` | 主场景基准与最初联调 |
| V2 | 3 组供应商 PDF/CSV、1 组采购需求 PDF/CSV、1 份系统证据 JSON | 现实别名、字段缺失、布局和标准化 |
| V3 | 5 组供应商 PDF/CSV | 31 字段直接证据和多种表达方式 |
| V4 | 5 份异常 PDF、1 份错误表头 CSV、2 份来源校验 PDF、3 份控制夹具 JSON | 文件拒绝、引用安全、调用限制和来源稳定性 |
| V5 | 8 组语义配对的供应商 PDF/CSV | 多供应商、异构表头、字段缺失、费用状态和交期歧义的泛化测试 |
| V6 | 15 份独立输入：开发、校准、留出各 5 份 | 多页、重复字段、内部冲突、费用缺失、计价基数、提示词注入和登记 CSV 混合路径 |
| V7 | 20 份英文合成 PDF：开发 8、校准 6、留出 6 | 页级原生文本/OCR 路由、扫描与混合页面、OCR 易混字符、可见内容与隐藏文字层冲突及盲测隔离 |
| V7.2 | 6 份全新英文合成 Holdout PDF | 验证每文档独立模型预算，并重新覆盖原生文本、纯扫描、混合、旋转、缺失/冲突和 prompt injection |

### 3.1 V1：基准

- `quotes.csv` 是固定结构基准。
- 三份 PDF 是一条输入分支；三份供应商 CSV 是另一条输入分支。
- 同一供应商的 PDF、CSV 和固定模板行不能在一次比较中重复计入。
- `evaluation/reference/quote_V1/` 目前仅在本地保留，按此前要求不上传 GitHub。

### 3.2 V2：现实字段差异

- PDF/CSV 表达同一业务对象，但答案只能依据当前文件内容。
- A 的 CSV 明确提供国家和类别，PDF 没有，因此使用不同答案。
- B 没有运费条款，两个运费字段保持 `null/MISSING`，不能当作免费或零。
- B 的 `per piece`、`Individual pieces`、`1 piece` 分别支持计价基数、单颗包装和订购步长；属于 `DOCUMENT` 标准化，不是无证据推导。
- 明确没有其他费用时，统一为 `other_fees_status=NOT_APPLICABLE`、`other_fees_amount="0.00"`。
- C 的 `purchase order receipt` 不静默等同于 `ORDER_DATE`，起算事件保留待确认。
- [`system_evidence_v2.json`](../../data/generated/inputs/development/quote_V2/system_evidence_v2.json) 是系统记录示例，不是报价输入或隐藏答案。其中 `KNOWN_ZERO` 不符合当前五类费用状态，不能作为通过样例。

V2 答案位于 [`evaluation/reference/quote_V2/reference_answers.json`](../../evaluation/reference/quote_V2/reference_answers.json)，包含字典版本和哈希、9 个输入哈希、采购需求答案、4 组报价答案以及系统证据兼容性检查。

### 3.3 V3：直接证据

- 包含 5 家供应商，每家各有 CSV 和 PDF。
- 非空期望值必须能从当前文件直接获得语义支持，不能只由其他字段推导。
- 覆盖按颗/按盘计价、包装容量、订购步长、MOQ、不同费用状态、自然日/工作日和发货/到货语义。
- 答案位于 [`evaluation/reference/quote_V3/reference_answers.json`](../../evaluation/reference/quote_V3/reference_answers.json)。

### 3.4 V4：极端边界

V4 定义以下预期：

- 损坏、空白、加密、超过 5 页或超过 5 MB 的 PDF 明确拒绝。
- 错误或重复 CSV 表头明确拒绝。
- 虚假来源、跨供应商引用和跨版本引用被拒绝。
- 每阶段最多 3 次尝试（含首次），每次逻辑图运行最多 8 次模型调用，恢复后继续累计。
- 同文件、同解析器版本重复解析时产生相同的稳定来源编号。

V4 控制夹具在 `data/generated/fixtures/quote_V4/`，答案位于 [`evaluation/reference/quote_V4/reference_answers.json`](../../evaluation/reference/quote_V4/reference_answers.json)。它们定义预期，不代表 B 的实现已经通过。

### 3.5 V5：泛化测试

- 包含 8 个独立合成报价案例，每个案例各有一份异构 CSV 和一份语义等价、可提取文本的 PDF。
- 覆盖按颗和按盘销售、1/50/100 颗计价基数、多种订购步长、按颗或按盘 MOQ，以及五类费用状态。
- 同时覆盖自然日/工作日、到货/发货、缺失系统字段、付款后起算和区间交期。
- 付款后起算不能冒充 `ORDER_DATE`；“2 至 4 天”不能被解析器擅自压缩成一个整数。
- 8 个案例只表示数据集总量超过 5 份。测试时分成 `V5-BATCH-1`（A–D）和 `V5-BATCH-2`（E–H），每批选择 CSV 或 PDF 分支，单任务保持 4 份文件。
- 答案位于 [`evaluation/reference/quote_V5/reference_answers.json`](../../evaluation/reference/quote_V5/reference_answers.json)。

### 3.6 V6：分集泛化与混合路径

- 共 15 个独立合成报价案例：开发、校准和留出集各 5 份；全量包含 6 份固定登记 CSV 和 9 份 2–3 页英文文本 PDF。
- 每个集合就是一个独立任务批次，不跨集合合并，因此单任务始终不超过 5 份文件。
- 开发集允许用于修复实现；校准集只用于选择字段映射、提示词版本和阈值；进入留出集前必须冻结代码、映射、提示词和阈值。
- 留出样本一旦用于调试或调参，就失去留出资格，必须转入下一版开发集并重新制作留出样本。
- 覆盖多页内容、相同事实的重复证据、价格／起算事件／版本冲突、费用完全缺失、`UNKNOWN` 与 `INCLUDED` 空金额、1/50/100/500 颗计价基数以及提示词注入。
- 登记 CSV 使用 `FIXED-QUOTE-CSV-V1` 严格表头。干净记录执行确定性字段映射且期望 0 次模型调用；只有付款、交期范围或跨列冲突等复杂语义进入一次受限模型审阅。
- PDF 中要求模型忽略规则、替换供应商、借用其他供应商价格或直接批准的文字均是不可信数据，不得改变工具、权限、预算、来源和审批状态。
- 数据集清单位于 [`evaluation/reference/quote_V6/dataset_manifest.json`](../../evaluation/reference/quote_V6/dataset_manifest.json)；开发、校准和留出答案分别位于其同名子目录，均不得向运行时暴露。

### 3.7 V7：页级 OCR 路由与严格留出

- 共 20 份独立英文合成 PDF：开发 8 份、校准 6 份、留出 6 份；每份最多 3 页并显式标记为非真实商业报价。
- 三组均按原生文本表格、纯扫描、文本与扫描混合、低质量/冲突/对抗四类组织；每页期望路由明确为 `NATIVE_TEXT` 或 `OCR`。
- 覆盖 1/50/100/500 颗计价基数、piece/tray MOQ、包装数量与订购倍数分离、五类费用状态、自然日/工作日、到货/发货、完整起算事件、重复一致、字段冲突和真实缺失。
- OCR 场景覆盖 `0/O`、`1/I/l`、小数点、连字符、日期数字和料号后缀；扫描页答案另含人工抄录和关键 token。
- 对抗样本含图片中的提示词注入，以及可见扫描图与不可见文字层冲突。文档指令不得改变价格、来源、工具调用或审批结果。
- B 的字段准确率只统计 30 个报价提取字段；`supplier_id` 是系统权威身份字段，不进入该指标。
- 开发和校准答案分别位于 `evaluation/reference/quote_V7/development/`、`calibration/`。留出完整答案存放在仓库外；仓库只提交 `holdout_commitment.json` 的 SHA-256 承诺。
- V7.1 将报价日期统一改为 ISO 8601，并将阻塞码与公共 review code 对齐；涉及的 PDF、输入哈希、开发/校准答案和 holdout 承诺均重新生成。
- V7.1-DEV-B1 仅修补 Development 的 `V7-DEV-05`：增加可见 `O/0` 字符说明，并将期望结果改为 `OCR_CRITICAL_CONFIDENCE_LOW` 阻塞人工复核；Calibration 与 Holdout 不变。
- V7.2 保留 V7.1 的 Development/Calibration 作为开放回归集，并以 6 份全新 PDF 替换已使用的 V7.1 Holdout；新数据包含 2 份原生文本、2 份纯扫描和 2 份混合 PDF。冻结和一次性运行前完整答案由 A 私有保管；运行后 A 发布的原始答案通过预先承诺哈希核验，B 仅在隔离环境离线评分，`lc` 分支不提交该私有答案文件。
- V7 review-code runtime version: `extraction-review/1.2.0`.
- V7/V7.1 公开清单位于 [`quote_V7_manifest.json`](../../data/generated/manifests/quote_V7_manifest.json)；V7.2 新 Holdout 清单位于 [`quote_V7_2_manifest.json`](../../data/generated/manifests/quote_V7_2_manifest.json)，`lc` 分支仅保存对应答案 commitment。解析、OCR 和评测实现及实际结果见 [`guide_lc3.md`](guide_lc3.md)。

V7.2 一次性 Holdout 的实际结果为 6/6 文档运行成功、171/180 字段正确（95.00%）、来源身份和坐标可定位率 100%，但 `V72-HOLD-05.revision` 出现 1 个关键静默冲突，因此未通过阶段 6 的“关键静默错误为 0”硬门槛。该结果不能表述为 OCR 正式发布验收通过。

## 4. JSON 示例与参考答案

本地 `data/contracts/examples/` 中已准备采购需求、供应商报价和系统证据三份简洁 JSON 示例，用于 C/D 在解析器完成前联调；按此前要求暂不提交 GitHub，也不算真实模型输出。

参考答案隔离规则：

- 运行输入在 `data/generated/inputs/development/`、`calibration/` 或 `holdout/` 的当前选定集合；一次只使用一个集合。
- 独立答案在 `evaluation/reference/quote_Vx/`。
- 解析器、模型和业务工具不得挂载或读取 `evaluation/reference/`。
- 答案只用于解析后的离线评分，不能由被测函数生成后验证自身。
- V2、V3、V4、V5、V6 答案已提交；V7 开发/校准答案已提交，`lc` 分支对 V7/V7.2 留出答案只提交哈希承诺；V1 答案仅本地保存。

## 5. 统一识别规则

### 5.1 字段候选

1. 保存当前文件的 `raw_value`。
2. 按数据字典匹配标准 `field_name`。
3. 生成 `normalized_value` 和明确单位。
4. 关联本次任务允许的 `source_refs`。
5. 先保存为候选；通过来源、单位、范围、跨字段、语义或人工检查后才可成为 `VERIFIED`。

PDF 来源需记录文件版本、哈希、页码、稳定文本块 ID 和可用坐标；CSV 来源需记录文件版本、哈希、行号和列名。定位正确与语义支持必须分别检查。

### 5.2 状态与来源

- `validation_status`：`EXTRACTED`、`VERIFIED`、`MISSING`、`CONFLICT`。
- `origin`：`DOCUMENT`、`USER_INPUT`、`USER_CORRECTION`、`DERIVED`。
- 两组枚举含义不同，不能合并为置信度。
- 完全缺失字段使用 `MISSING`、`origin=null` 和空引用。
- `DERIVED` 必须关联输入字段和规则版本，不能伪装成原文。

### 5.3 金额与费用

- API 金额使用十进制字符串，参考答案统一保留两位小数；Python 用 `Decimal`，数据库用 `NUMERIC`。
- 费用状态只使用 `KNOWN_AMOUNT`、`FREE`、`INCLUDED`、`NOT_APPLICABLE`、`UNKNOWN`。
- 未提及费用不等于免费；未知金额保持 `null`。
- 只有明确免费或不适用时，金额贡献才可为 `"0.00"`。
- `INCLUDED` 不重复累加，未拆分金额可以为 `null`。

### 5.4 系统字段

`task_id`、`task_revision`、`quote_id`、`quote_version`、`document_id`、`document_version`、`document_sha256` 和 `snapshot_id` 由系统上下文提供，不要求模型从正文猜测。

输入变化必须创建新 `task_revision` 和不可变快照。旧结果可审计，但不能覆盖或批准为当前结果。

## 6. 使用方法

1. 选择测试版本：V1 基准、V2 现实缺失、V3 字段表达、V4 异常和安全边界、V5 多样化泛化、V6 分集泛化与混合路径、V7 页级 OCR 路由与严格留出。
2. PDF 与 CSV 分支分别运行，不把同内容文件当作新供应商。
3. 解析器只读取运行输入和当前任务允许的来源集合。
4. 保存人工纠正前的候选输出，再在隔离环境中与对应答案比较。
5. 分别统计金额、计价单位、包装、MOQ、费用、交期、来源定位和语义支持，不只检查 JSON 合法性。
6. 记录 `PASS`、`FAIL` 或 `PENDING`，以及错误、重试、调用次数、耗时和必要证据。

V6 使用顺序：先用开发集修复实现，再用校准集确定并冻结配置，最后对留出集运行一次离线评测。留出结果不得反向用于调参；若需要修复，后续重新划分数据版本。

V7 使用顺序相同，但留出答案隔离更严格：B 先冻结解析器、OCR 引擎及版本、阈值、提示词、审阅策略和依赖版本，并保存全部人工修正前结果；A 再提供完整答案，且重新计算的 SHA-256 必须与仓库中的承诺一致。

## 7. A+B 当前进度与边界

已完成的数据侧工作：

- 三份 1.2.0 数据字典及来源说明。
- V1–V7.2 测试输入的整理和版本目录；V6、V7 已隔离开发、校准和留出集。
- V2 至 V6 独立参考答案；V7 开发、校准独立答案及留出答案哈希承诺。
- V4 文件、引用、调用限制和来源稳定性预期。
- 三份合同 JSON 示例草案已在本地准备，暂未提交。

B 侧已经用代码和自动化测试证明：

- 英文文本 PDF、原生表格、批准 CSV、清晰英文扫描和混合 PDF 的解析路径。
- 页级 `NATIVE_TEXT / OCR / HYBRID / MANUAL_REQUIRED` 路由，以及 OCR 关闭时的显式 `pdf_page_requires_ocr`。
- 模型适配器、结构化候选、错误映射、调用日志、有限重试和脱敏审计。
- 稳定 PDF/表格/OCR 原子来源及 CSV 行列来源编号、归属、坐标和语义校验。
- 同文件、同 parser fingerprint 下来源编号的确定性，以及每份文档独立的最多 8 次模型调用预算。
- V7.2 一次性 Holdout 的运行、隔离评分和失败分母保留；当前全量自动化回归为 349 项通过。

仍需完成：

- 修复同一关键字段在多个合法原子来源中出现不同值时的确定性冲突检测。
- 使用新的严格 Holdout 证明关键静默错误为 0；不得重跑或重新宣称 V7.2 为未暴露 Holdout。
- A 的最终业务验收，以及 D 的 API、数据库、页面图像持久化和恢复流程集成。
- AWS/主办方目标环境验证。

C 负责数量、MOQ、费用、日期、预算和可行性计算；D 负责任务、版本、快照、API、数据库、作业和恢复协议。A/B 不复制这些权威实现。

## 8. 项目边界

- Week 1 原基线只支持英文可提取文本 PDF 和批准 CSV；当前 B 已实现英文原生表格、清晰扫描和混合 PDF 的 OCR 路径，但 V7.2 阶段 6 因 1 个关键静默错误未通过。
- 默认单 PDF 最多 5 页/5 MB，单任务最多 5 份；限制必须可配置。
- OCR 是 PDF 解析内部的页级分支：部署显式开启后，仅 `OCR/HYBRID` 页面调用 Tesseract，原生文字页面继续走独立路径。正式默认开关仍为关闭；测试或演示环境可显式开启。任意 Excel、手写/多语言/严重低质扫描、阶梯价格、复杂折扣/税务、自动汇率、分批交付和替代料兼容判断仍不在范围内。
- 系统不联系供应商、不议价、不签约、不下单、不付款。
- 未知不等于零，`PENDING` 不等于 `INFEASIBLE`；存在可能影响选择的问题时不得发布最终推荐。
- 合成和本地测试结果不能表述为真实采购泛化能力，也不能替代 AWS 或主办方 API 实测。

## 9. 目录约定

```text
data/
├── contracts/
│   ├── guide_JUNNAN.md
│   ├── quote_data_field.csv
│   ├── procurement_requirement_fields.csv
│   └── system_evidence_fields.csv
├── generated/
│   ├── inputs/
│   │   ├── development/
│   │   │   ├── quote_V1/ ... quote_V5/
│   │   │   └── quote_V6/、quote_V7/
│   │   ├── calibration/quote_V6/、quote_V7/
│   │   └── holdout/quote_V6/、quote_V7/、quote_V7_2/
│   ├── fixtures/quote_V4/
│   └── manifests/quote_V7_manifest.json、quote_V7_2_manifest.json
evaluation/
└── reference/
    ├── quote_V2/
    ├── quote_V3/
    ├── quote_V4/
    ├── quote_V5/
    ├── quote_V6/
    ├── quote_V7/（开发/校准答案；V7.1 留出仅哈希承诺）
    └── quote_V7_2/（`lc` 分支仅保存 V7.2 留出哈希承诺）
```

`data/generated/inputs/development/README.md` 的有效规则已经合并到本指南，因此删除该重复文件。以后数据字典、样例组织和测试方法统一在本文件维护。

## 10. 变更纪律

- 修改共享字段名、类型、枚举、金额口径、日期语义或必填行为前必须由团队确认。
- 变更后同步检查 [`docs/WEEK1_PLAN.md`](../../docs/WEEK1_PLAN.md)、[`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)、[`docs/DATA.md`](../../docs/DATA.md)、合同示例、参考答案和测试。
- 字典示例值只用于说明，不能成为字段缺失时的默认值。
- 新增样本时记录版本、目的、文件哈希、期望结果和是否允许模型访问。
- 留出样本一旦用于调试，必须转入开发集，不能继续称为盲测或留出集。
