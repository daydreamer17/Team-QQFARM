> **当前范围（2026-09-24）**：检索编排提供准入与 RoHS 制度依据，结构化记录由制度检查接口判定；外部记录核验和金额审批仍不实现。

# 新制度与检索编排使用指南

> 2026-09-16：本地实现与真实 SiliconFlow／PostgreSQL 验证完成。向量存储仍为 PostgreSQL＋pgvector；没有新增向量数据库。本指南覆盖制度修订、导入和检索编排，不包含供应商合规服务、采购主图接入或真实解释模型。

## 1. 本轮完成内容

- 新制度目录：`data/policies/electronics-v2/`，制度版本 `2026.09.2`，文档版本 `1.1.0`，5 份英文文档、24 条条款。
- 旧目录 `electronics-v1` 与其已发布版本保持不变；不能自动把旧采购快照切换到新制度。
- 修订报价日期／截止边界、系统供应商身份、过期批准／RoHS 的三态，以及审批和行政更正边界。
- `src/supplier_comparison/rag/orchestration.py`：结构化计划、按六类控制码分别检索、逐条要求覆盖、有限补检索、受控失败输出。
- 目录使用制度原文和 manifest 构建，固定 `catalog-2026.09.2/1` 与审阅后的内容哈希，拒绝旧版本或同版本正文被更改。评测标签不参与目录或查询构造。
- 每条完整条款为一个保守要求；即使 control_code 已命中，仍需覆盖它的全部条款。补检索最多每项一次，总请求上限 6＋24＝30；模型内部重试仍由现有客户端限制为最多 2 次。

## 2. 输入和输出

维护的输入示例：`data/examples/policy_rag/planning_context_v2.json`。输入包含快照标识、revision、制度／索引版本、类别、采购地区、带时区评估时间、已计算总成本、币种及税模式。

这是当前检索编排的最小输入适配，不是完整供应商合规上下文。它不携带资质记录、不核验供应商。原全流程设计中的完整业务对象留给后续集成。

输出 `PolicyEvidenceBundle` 包含：冻结计划、制度内容哈希、要求与结构化参数、是否需要经理审批、各次完整检索结果、24 项覆盖状态、请求数量和原因。

- `READY`：制度范围、成本前提及全部要求引用通过；**不等于供应商 COMPLIANT、已审批或已发布最终推荐**。
- `REVIEW_REQUIRED`：范围外、旧版本、未知总成本、不支持的币种／税模式、检索故障、冲突、伪造引用或覆盖不完整。
- 未知总成本的经理审批要求为 null，不按零判定；SGD 10000.00（含）要求经理审批，低于阈值仍需普通人工审批。
- 子检索 ERROR／CONFLICT 不进行无上限重试，保留原因；不会用 BM25 诊断候选放行。

## 3. 初始化与导入

```powershell
docker compose up -d postgres
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m supplier_comparison.rag import-policies `
  --manifest data/policies/electronics-v2/manifest.json --publish
```

当前真实 SiliconFlow 配置生成的索引是 `pidx-1e949b8d56785cede49f712c`。更换模型或配置后必须以导入输出为准，并更新运行输入的索引版本。不能复用不匹配的索引。

重复相同导入返回 `replayed=true`，不重复 embedding，也不覆盖原版本。

Windows 上如果 localhost 连接卡住，可将本地 DATABASE_URL 的主机改为 `127.0.0.1` 并配置 `connect_timeout=5`；本轮只在验证子进程中使用该设置，未修改用户 `.env`，不要输出数据库密码。

## 4. 运行检索编排

```powershell
.\.venv\Scripts\python.exe -m supplier_comparison.rag retrieve-plan `
  --context data/examples/policy_rag/planning_context_v2.json `
  --manifest data/policies/electronics-v2/manifest.json `
  --output evaluation/results/local/rag-v2/evidence-bundle.json
```

成功退出码为 0；待审查为 1。模型密钥仍复用现有 `.env`，不要写进命令或 Git。

## 5. 本轮验证记录

| 检查 | 结果 |
| --- | --- |
| 本地真实 PostgreSQL＋pgvector 新制度发布 | PUBLISHED，24 条条款 |
| SiliconFlow 真实检索编排 | READY；12 次子检索，24／24 要求覆盖 |
| 原 8 题开发集用于新版本回归 | BM25／向量／融合 Recall@10 均 100%；rerank Recall@3 为 91.67% |
| 开发集引用支持／状态准确率 | 均 100%，0 个 ERROR |
| 显式 PostgreSQL 检索与新连接恢复测试 | 1 passed |
| 全仓库普通测试 | 445 passed，4 skipped；真实数据库项目须显式开启 |
| 新版相同内容重复导入 | PUBLISHED，replayed=true；不重复生成向量 |

真实本地结果位于 `evaluation/results/local/rag-v2-20260916/`，保持 Git 忽略。开发集数字是相对于其已定义支持条款的结果，不代表所有自然语言主张均已证明，也不代表留出集或官方 AWS 验收。

回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
$env:RUN_POSTGRES_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -q tests/rag/test_postgres_retrieval.py
Remove-Item Env:RUN_POSTGRES_TESTS
```

## 6. 仍未实施

2026-09-17 更新：下面的“真实解释模型”现已完成独立客户端与 CLI，见 [guide_RAG_EXPLANATION.md](guide_RAG_EXPLANATION.md)。采购主图、资质核验、页面与恢复集成仍未实施。

人工补证接口、供应商数据库／ComplianceMatrix、采购 LangGraph 接入、真实解释模型和页面／报告仍未实施。当前 plan 与 bundle 可序列化保存，但 CLI 不提供跨进程预算账本或自动恢复；集成方须通过现有 artifact 服务冻结输出与预算，不能宣称本模块已实现崩溃恢复。

政策要求引用已完整不等于事实证据齐全。下一阶段必须将 bundle 交给确定性资质核验服务，再决定最终推荐资格。
