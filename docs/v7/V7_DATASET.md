# V7 OCR 评测数据说明

## 1. 目标与边界

V7 为成员 B 后续页级原生文本/OCR 路由、字段提取、证据校验和安全门禁提供成员 A 独立制作的测试输入与参考答案。它不包含 OCR、PDF 解析、模型提示词、成本计算、数据库或 API 实现，也不能证明这些功能已经通过验收。

V7 继续使用 `data/contracts/quote_data_field.csv` 1.2.0。该表共有 31 个字段；B 的字段准确率评估排除系统权威字段 `supplier_id`，其余 30 个字段全部计入，`MISSING`、`CONFLICT` 和失败结果不从分母删除。

## 2. 数据分组

| 数据组 | 数量 | 原生文本 | 纯扫描 | 混合 | 低质量/冲突/对抗 | 答案可见性 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Development | 8 | 3 | 2 | 2 | 1 | 可用于开发和修复 |
| Calibration | 6 | 2 | 2 | 1 | 1 | 仅用于选择并冻结最终参数 |
| Holdout | 6 | 2 | 2 | 1 | 1 | 冻结并运行前不可见 |

全部 20 份 PDF 均为英文打印体合成报价，无真实供应商、器件或商业数据，不包含手写要求。每份 1–3 页，不超过 5 页/5 MB，并在页面上标记 `SYNTHETIC TEST QUOTE - NOT A REAL COMMERCIAL OFFER`。

输入目录：

```text
data/generated/inputs/development/quote_V7/
data/generated/inputs/calibration/quote_V7/
data/generated/inputs/holdout/quote_V7/
```

公开 manifest：`data/generated/manifests/quote_V7_manifest.json`。开发与校准案例记录文件、哈希、页数、页面路由、覆盖标签和答案可见性；holdout 的逐页路由和覆盖标签在释放前盲化，只公开输入元数据与答案哈希承诺。

## 3. 覆盖设计

20 个样本整体覆盖：

- 按单颗、50 颗、100 颗和 500 颗计价；
- `packaging_type`、`units_per_pack`、`order_multiple_units` 分别提供直接表达；
- MOQ 的 piece/tray 两类单位；
- 运费与其他费用的 `KNOWN_AMOUNT`、`FREE`、`INCLUDED`、`NOT_APPLICABLE`、`UNKNOWN` 及完全未提及；
- 明确到货日期、相对交期、自然日/工作日、到货/发货语义及完整起算事件；
- 报价有效期、重复一致证据、字段冲突和真实缺失；
- OCR 易混字符 `0/O`、`1/I/l`、小数点、连字符、日期数字和料号后缀；
- 图片内提示词注入；
- 可见扫描图片和隐藏文字层的冲突。

每个非空标准值都有当前 PDF 中的直接语义证据。参考答案不会只根据另一个字段推导包装、订购倍数、MOQ、费用、日期或料号。

## 4. 参考答案结构

开发和校准参考答案分别位于：

```text
evaluation/reference/quote_V7/development/reference_answers.json
evaluation/reference/quote_V7/calibration/reference_answers.json
```

每个案例记录：输入 SHA-256、页数、期望页面路由、全部 30 字段的标准化结果与字段状态、逐字段证据、表头和值的对应关系、OCR 页人工抄录和关键 token、安全预期及审阅结果。

- `MISSING`：值、单位、`origin`、原始表达和证据均为空。
- `EXTRACTED`：保留原始表达、标准化值、单位、`DOCUMENT` 来源和当前文件证据；不自动标记 `VERIFIED`。
- `CONFLICT`：标准化值为空，保存全部冲突原文及证据，不擅自选值。
- `UNKNOWN`：费用状态可从文档提取，但金额字段为 `MISSING/null`，不得填写零。
- 金额统一为两位小数的十进制字符串。

运行时不得挂载或读取 `evaluation/reference/`、生成器临时页面图或仓库外留出答案。

## 5. Holdout 隔离流程

仓库只提交：

```text
evaluation/reference/quote_V7/holdout_commitment.json
```

该文件包含完整留出答案文件的 SHA-256、文件名、案例 ID 和释放条件，不含任何字段答案。完整答案由 A 保存在仓库外：

```text
E:\QQFARM_PRIVATE\quote_V7\holdout_reference_answers.json
```

执行顺序：

1. B 冻结 parser、OCR engine/version、OCR 阈值、prompt、review policy 和依赖版本。
2. B 对 6 份 holdout PDF 运行一次并保存全部人工修正前结果。
3. A 提供仓库外完整答案。
4. 重新计算答案 SHA-256，必须与 `holdout_commitment.json` 一致。
5. A 在隔离环境离线评分。
6. 若 B 查看答案后修改代码，本批 holdout 立即降级为 development，并重新制作留出集。

## 6. 使用与评分

1. 开发集用于定位错误并修改实现。
2. 校准集只用于选择 OCR 引擎、阈值和最终版本，不能反复把它当开发集。
3. 冻结配置后才可运行 holdout。
4. 分别报告金额、计价基数、包装、MOQ、费用、交期、字段漏检/误报、来源定位和语义支持。
5. 另记录补问、人工纠正、人工核验时间、模型调用次数、重试、耗时和失败。

`READY_FOR_DOWNSTREAM` 只表示该合成样本在参考口径下没有阻塞性缺失、冲突或安全问题，不表示采购可行、最低成本或已批准。确定性成本和约束判断仍由成员 C 的权威模块完成。
