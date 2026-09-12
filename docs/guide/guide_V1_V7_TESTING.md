# V1–V7 真实模型测试指南

本文用于本地 OpenAI-compatible 模型接口测试。只运行 development 数据；不得重新运行已经暴露的 V7/V7.2 holdout，也不得把参考答案放入模型提示或运行目录。

## 1. 通用准备

在 PowerShell 和仓库根目录执行。`python-dotenv run` 只向当前子进程注入 `.env`，不会打印 API Key：

```powershell
$runRoot = "evaluation/results/local/$(Get-Date -Format 'yyyyMMdd-HHmmss')-v1-v7"
New-Item -ItemType Directory -Force $runRoot | Out-Null

.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe -c "import os; from supplier_comparison.extraction.adapters import OpenAICompatibleConfig; c=OpenAICompatibleConfig.from_env(); assert c.api_key_env and os.getenv(c.api_key_env); print({'provider': c.provider, 'model_id': c.model_id, 'key_configured': True})"
```

每个 runner 先保存模型调用前不知道答案的原始结果，再由独立 evaluator 读取 development 参考答案评分。`evaluation/results/local/` 已被 Git 忽略。

## 2. V1：MCU 主场景基准

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_real_extraction.py `
  --all-suppliers --dataset-version V1 --input-format pdf `
  --output-dir "$runRoot/v1/pdf"

.\.venv\Scripts\python.exe scripts\evaluate_development_extractions.py `
  --a "$runRoot/v1/pdf/v1_pdf_supplier_a_pre_correction.json" `
  --b "$runRoot/v1/pdf/v1_pdf_supplier_b_pre_correction.json" `
  --c "$runRoot/v1/pdf/v1_pdf_supplier_c_pre_correction.json" `
  --output "$runRoot/v1/v1_score.json"
```

V1 要求三份 PDF 均产生 30 个候选字段，B 的运费保持 `MISSING`。最终应检查 90 个字段的匹配率；完整 FastAPI 两次中断演示另按 `guide_D.md` 第 4–5 节执行。

## 3. V2：现实别名、缺失字段和 PDF／CSV 差异

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_real_extraction.py `
  --all-suppliers --dataset-version V2 --input-format pdf `
  --output-dir "$runRoot/v2/pdf"

.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_real_extraction.py `
  --all-suppliers --dataset-version V2 --input-format csv `
  --output-dir "$runRoot/v2/csv"

.\.venv\Scripts\python.exe scripts\evaluate_v2_reference_answers.py `
  --results-root "$runRoot/v2" --selection latest-per-input `
  --output "$runRoot/v2/v2_score.json"
```

PDF 和 CSV 是同一报价的两种输入，不得当成六家供应商。B 的两种格式均应保留未知运费。失败输出没有可评分 batch，因此同时检查 runner 的 `error_code` 和 evaluator 的 `document_count`。

## 4. V3：31 字段直接表达

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_real_extraction.py `
  --all-suppliers --dataset-version V3 --input-format pdf `
  --output-dir "$runRoot/v3/pdf"

.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_real_extraction.py `
  --all-suppliers --dataset-version V3 --input-format csv `
  --output-dir "$runRoot/v3/csv"

.\.venv\Scripts\python.exe scripts\evaluate_v3_reference_answers.py `
  --results-root "$runRoot/v3" --output "$runRoot/v3/v3_score.json"
```

V3 evaluator 应发现 10 份输入。重点看 `successful_documents`、`conditional_field_match_rate` 和把失败文档计零的 `conservative_field_coverage_rate`。

## 5. V4：异常文件、限制和来源安全

V4 的两份有效来源 PDF 走真实模型；其余异常输入由确定性边界测试验证，避免把应在解析阶段拒绝的文件发送给模型：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_real_extraction.py `
  --all-suppliers --dataset-version V4 --input-format pdf `
  --output-dir "$runRoot/v4/pdf"

.\.venv\Scripts\python.exe -m pytest tests\extraction\test_v4_boundaries.py -q
```

验收包括空白、损坏、加密、超页数、超大小、错误 CSV 表头、未知引用和调用预算。

## 6. V5：多供应商泛化

V5 每个任务最多四份文件，因此分两个 batch；runner 内仍为每份文档独立 8 次调用预算：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_versioned_evaluation.py `
  --dataset-version V5 --batch-id V5-BATCH-1 --input-format pdf `
  --output-dir "$runRoot/v5/pdf"

.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_versioned_evaluation.py `
  --dataset-version V5 --batch-id V5-BATCH-2 --input-format pdf `
  --output-dir "$runRoot/v5/pdf"

.\.venv\Scripts\python.exe scripts\evaluate_versioned_results.py `
  --reference evaluation/reference/quote_V5/reference_answers.json `
  --results-root "$runRoot/v5/pdf" --media-type application/pdf `
  --output "$runRoot/v5/v5_pdf_score.json"
```

当前 stage-6 runner 只开放 V5 PDF 分支。若 `critical_silent_error_count` 大于 0，即使总体字段准确率达到 95% 也不能判定版本通过。

## 7. V6：分集、登记 CSV 和混合路径

日常开发只运行 development。不要在修复循环中使用 calibration 或 holdout：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_versioned_evaluation.py `
  --dataset-version V6 --split development `
  --output-dir "$runRoot/v6/development"

.\.venv\Scripts\python.exe scripts\evaluate_versioned_results.py `
  --reference evaluation/reference/quote_V6/development/reference_answers.json `
  --results-root "$runRoot/v6/development" `
  --output "$runRoot/v6/v6_development_score.json"
```

干净登记 CSV 应为 0 次模型调用；语义 CSV 和 PDF 才调用模型。检查 `route_model_call_checks_passed`、人工审核数量和关键静默错误。

## 8. V7：页级路由、OCR 和冲突

当前正式默认 `SUPPLIER_PDF_OCR_ENABLED=false`。没有已冻结的 Tesseract 运行时或 OCR 尚未重新通过发布门禁时，只运行三个原生文本 development 样本：

```powershell
.\.venv\Scripts\python.exe -m dotenv -f .env run -- `
  .\.venv\Scripts\python.exe scripts\run_v7_model_evaluation.py `
  --manifest data/generated/manifests/quote_V7_manifest.json `
  --split development `
  --case-id V7-DEV-01 --case-id V7-DEV-02 --case-id V7-DEV-03 `
  --output-dir "$runRoot/v7/development-native"

.\.venv\Scripts\python.exe scripts\evaluate_v7_model_results.py `
  --reference evaluation/reference/quote_V7/development/reference_answers.json `
  --results-root "$runRoot/v7/development-native" `
  --case-id V7-DEV-01 --case-id V7-DEV-02 --case-id V7-DEV-03 `
  --output "$runRoot/v7/v7_native_score.json"
```

扫描和混合 development 样本 V7-DEV-04 至 V7-DEV-08 在 OCR 关闭时必须返回 `pdf_page_requires_ocr`，不能变成正常的全字段缺失结果。OCR 运行时准备完成后，才使用显式 `--tesseract-binary` 和 `--tessdata-dir` 对整个 development split 运行，并用公开的 `evaluation/reference/quote_V7/development/reference_answers.json` 离线评分。

不得运行 `run_v7_holdout_once.py`。V7/V7.2 已暴露 holdout 不能再次作为盲测结果。

## 9. 判定规则

runner 的 `status=PASSED` 仅说明解析、模型结构和证据校验完成；evaluator 的顶层 `status=PASSED` 只说明评分程序执行成功。版本结论还必须同时检查：

- 文档是否全部成功；
- 字段准确率和失败字段；
- `critical_silent_error_count == 0`；
- review 状态是否与参考答案一致；
- 来源身份与定位率；
- 每文档调用预算是否独立且不超过 8；
- OCR 关闭时扫描页是否显式失败。
