# 真实 LLM 制度解释使用指南

> 2026-09-17：真实解释客户端与 CLI 已实现。本功能解释制度，不核验供应商事实，不产生或修改成本、价格排名、合规决策或审批。

## 1. 输入与输出

输入一份完整、成功的 `PolicyEvidenceBundle`，以及由调用方确认的事实 JSON。CLI 再次根据固定哈希的新制度目录校验每条引用原文、版本和 24 项覆盖；失败、冲突、缺项、伪造或旧制度不能进入解释。已知成本、币种、类别、税模式和地区必须与冻结上下文一致。

调用方对事实真实性负责。演示输入 `data/examples/policy_rag/confirmed_facts_v2.json` 明确将供应商身份设为未确认、批准／RoHS 记录设为 null，不能拿它当成真实供应商证明。

每条说明包含：

- `text`：模型生成的简短中文制度说明。
- `citation_ids`：本次有效引用标识。
- `evidence_quotes`：从相应制度原文逐字复制的支持摘录。

`confirmed_facts`、`retrieval_id`、`explanation_id` 由应用程序写入，不让模型重新填写。模型只能返回 claims，额外 decision 等字段会被拒绝。CLI 按条款去重后分批解释，每次仍绑定原 retrieval ID，不建立假的综合检索结果。

输出同时保存 bundle、snapshot、revision、制度／索引版本及安全调用记录（模型 ID、尝试、延迟、token 用量和错误码）。失败输出 ERROR 和空 explanations，不将部分结果伪装成完整解释，也不更改原检索 bundle。

## 2. 配置

复用现有聊天服务和本地密钥。未设置 `SUPPLIER_EXPLANATION_MODEL_ID` 时，使用 `SUPPLIER_MODEL_MODEL_ID`；不自动换模型或服务商。

可独立设置：MODEL_ID、BASE_URL、API_KEY_ENV、TIMEOUT_SECONDS（默认 60）、MAX_ATTEMPTS（最多 2，含首次）、MAX_TOKENS（默认 4096），均使用 `SUPPLIER_EXPLANATION_` 前缀。参见 `.env.example`，不要提交 `.env` 或密钥。

当前适配为 OpenAI-compatible `/chat/completions`，采用 JSON 输出和关闭 thinking 的 SiliconFlow 参数；其他服务商的协议需单独验证，不能据此宣称 Bedrock 已接入。非本机端点必须 HTTPS。

## 3. 运行

先按 `guide_RAG_ORCHESTRATION.md` 生成新版检索 bundle，再执行：

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.rag explain-plan `
  --bundle evaluation/results/local/rag-v2/evidence-bundle.json `
  --facts data/examples/policy_rag/confirmed_facts_v2.json `
  --manifest data/policies/electronics-v2/manifest.json `
  --output evaluation/results/local/rag-v2/explanations.json
```

成功退出码 0，模型失败退出码 1。普通测试不访问网络：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/rag/test_explanation.py
.\.venv\Scripts\python.exe -m pytest -q
```

## 4. 安全与验证边界

程序校验 JSON 结构、引用 ID、原文摘录、版本和输入事实不变；提示词将制度与事实视为数据而不是命令。网络重试最多两次；截断、无效 JSON、遗漏引用、不匹配摘录均失败，不自动通过“修复”放宽约束。

修复更新（2026-09-17）：输出校验不合格时允许一次重新生成，向模型反馈细分错误码，原事实和引用保持不变，重新执行同一严格校验。网络重试与重新生成共用最多两次 HTTP 调用预算，不嵌套扩大次数；401 等认证错误不重试。多次不合格仍输出 ERROR，不发布部分解释。

诊断记录仅保存条款 ID、细分错误码、用量和耗时，不保存失败正文／提示词／密钥。错误码可区分 `explanation_language_invalid`、`explanation_evidence_quote_invalid`、`explanation_evidence_mapping_invalid`、`explanation_citation_coverage_invalid`、`explanation_output_truncated`、`explanation_json_invalid`、`explanation_shape_invalid` 与 `explanation_schema_invalid`。旧记录没有失败原文，无法据此确定历史具体原因。

`model_calls` 现在统计所有实际调用（包括不合格后的重新生成），可能大于 12；完整验收应检查 status=OK 与 explained_clauses=24，不要求调用数固定。可以仍用原命令验证，也可修改 output 路径保留旧失败记录。

修复后验证：原失败 CHG-003 批次单独真实调用通过；完整 explain-plan 返回 OK、12 次调用、24 条制度覆盖，另存 `evaluation/results/local/rag-explanation-check/explanations-fixed-zh.json`，原错误记录未覆盖。全仓库 467 passed、4 skipped；另行核对中文、原文摘录及输入事实不变。模型仍可能偶发不合格，最多两次后明确失败，并不保证每次调用成功。

**原文摘录匹配不等于自动证明中文主张被原文蕴含。**模型仍可能给出错误解读，不能声称解释正确率 100%。解释仅供展示，合规状态必须来自确定性服务；部署时如需以解释执行决策，必须另做语义支持评测与审核，本实现不允许这样使用。

现有事实与报价人工审查不因添加解释而被替代。本次未接入采购 LangGraph、审批、报告发布或跨进程预算恢复；调用方未来须保存解释 artifact 与调用账本，恢复时不得重置预算。

## 5. 本地真实验证记录（2026-09-17）

- 重新运行真实 PostgreSQL＋pgvector 检索编排：READY，24／24 项要求覆盖。
- 聊天模型：现有配置 `deepseek-ai/DeepSeek-V4-Flash`，SiliconFlow 实际调用；未更换检索模型或向量库。
- 初轮部分说明为英文，未计为中文验收通过；加强提示词并加入中文字符检查后重新运行。
- 最终中文结果：OK，12 次模型调用、31 条中文说明，引用覆盖全部 24 条制度；支持摘录均逐字匹配，事实对象未修改。
- 最终解释调用累计约 113.66 秒，9475 tokens；这是本次调用记录，不是性能承诺或总开发调用预算。
- 全仓库：463 passed，4 skipped（显式 PostgreSQL 专项未在这条普通测试命令启用）；解释测试覆盖失败检索、未知引用、伪造摘录、额外决策字段、截断、英文输出、密钥缺失、两次重试上限、CLI 失败封闭和输入事实冲突。
- 本地文件：`evaluation/results/local/rag-explanation-20260917/evidence-bundle.json` 与 `explanations-zh.json`，保持 Git 忽略。31 条说明全部经过结构／引用／摘录检查，不据此声称语义准确率 100% 或 AWS 已验收。
