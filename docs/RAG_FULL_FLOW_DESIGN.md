# RAG 全流程设计与接口基线

> 设计版本：`rag-flow-v1`；日期：2026-09-16。第一步（接口设计）与第二步（制度审查）已完成；本文不是运行时功能已实现的声明。实现范围以 `WEEK2_PLAN.md`、`WORKFLOW.md` 为准。本次不沿用旧 A／B／C／D 分工。

## 1. 目标和边界

RAG 回答“当前采购要遵守什么制度，依据在哪里”，不回答“这家供应商事实上是否获批”。后者必须由结构化证据与确定性规则判断。

```text
已审核报价与需求 → 确定性数量／成本／可行性比较 → 冻结采购快照
  → 结构化制度范围生成检查计划 → 按 control_code 分别检索
  → 引用核验及要求级覆盖检查 → 供应商身份和资质精确核验
  → 合规矩阵 → 按原排名选择候选 → 引用受控解释 → 审批／报告
```

报价仍为 PENDING 时不发布最终推荐。制度检索可以提前展示，但不能代替报价审核。审批是独立步骤，`COMPLIANT` 不等于已批准或已下单。

## 2. 现有接口与新增设计接口

现有接口直接复用 `src/supplier_comparison/rag/contracts.py`，不复制或修改其 Pydantic 类。下面的 `PolicyEvaluationContext`、`ControlPlan`、`PolicyEvidenceBundle`、`ComplianceMatrix` 是本次冻结的设计契约，尚未新增运行时代码或公开 API。

| 对象 | 必须字段 | 约束 |
| --- | --- | --- |
| `PolicyEvaluationContext` | `schema_version`、`task_id`、`task_revision`、`graph_run_id`、`snapshot_id`、`snapshot_sha256`、`policy_set_id`、`policy_set_version`、`policy_index_version`、`supplier_registry_version`、`control_catalog_version`、`category`、`region`、`evaluated_at`、`event_type`、`candidates` | 系统生成，不能用用户自由文本决定版本或适用范围；时间必须带时区 |
| `candidates[]` | `quote_id`、`quote_version`、`review_artifact_id`、`declared_supplier_id`、`manufacturer_part_number`、`feasibility`、`price_rank`、`currency`、`total_cost` | 金额为 Decimal 字符串或 null；排名来自确定性比较，RAG 不改动 |
| `ControlPlan` | `plan_id`、`context`、`controls`、`preconditions`、`status` | `status=READY|REVIEW_REQUIRED`；空适用范围不能返回 READY |
| `controls[]` | `control_code`、`required_requirement_ids`、`query_template_id`、`rule_parameters`、`evaluation_phase`、`applies_to_quote_ids` | 参数来自已审阅的结构化目录，不从检索文本或 LLM 推导 |
| `PolicyEvidenceBundle` | `bundle_id`、`plan_id`、`snapshot_id`、`task_revision`、制度及索引版本、`retrieval_results`、`requirement_coverage`、`status`、`reasons` | `status=READY|REVIEW_REQUIRED`；保留所有子检索状态，不掩盖错误 |
| `requirement_coverage[]` | `control_code`、`requirement_id`、`status`、`retrieval_id`、`citation_ids` | `status=SUPPORTED|MISSING|CONFLICT|ERROR`；身份／哈希匹配不等于语义支持 |
| `ComplianceMatrix` | `schema_version`、`assessment_id`、`snapshot_id`、`task_revision`、所有版本、`evaluated_at`、`evidence_bundle_id`、`supplier_assessments`、`selection` | 一次评估全部 FEASIBLE 候选，不直接查询当前浮动版本 |
| `supplier_assessments[]` | `quote_id`、`supplier_master_id`、`identity_match_id`、`status`、`checks`、`reasons` | `status=COMPLIANT|NON_COMPLIANT|REVIEW_REQUIRED`；每个检查绑定制度引用与事实记录 ID／版本 |
| `selection` | `status`、`selected_quote_id`、`blocking_quote_ids` | `status=ELIGIBLE_FOR_APPROVAL|REVIEW_REQUIRED|NO_COMPLIANT_SUPPLIER|NO_FEASIBLE_SUPPLIER`；除首种外 selected_quote_id=null |

`event_type=INITIAL|QUOTE_UPDATED|REQUIREMENT_UPDATED|REGISTRY_UPDATED|POLICY_UPDATED`。标识均为系统 ID，不接受服务器路径。未确认的供应商 ID 只是声明，不是供应商主体匹配结果。

## 3. 必需控制码生成规则

新增实现必须提供版本化 `ControlCatalog`：绑定制度版本、文档／条款 ID、适用品类／地区／时间、要求 ID、参数、查询模板和语义审查状态。当前 manifest 提供范围与 control code，但多数参数为空，不能独自充当完整执行规则目录。

生成顺序冻结为：

1. 校验快照、制度集合、索引与目录版本匹配，目录审查无未解决阻塞项。
2. 按已确认的需求品类、采购地区及评估时点匹配范围；不能从供应商国家推断采购地区。制度有效期采用 `[effective_from, effective_to)`。
3. 从范围匹配的目录读取所有必需控制码及要求，不依赖 Top-k 是否找到它们。
4. 校验运行前提，生成控制计划；品类、地区、制度或参数缺失时保持 REVIEW_REQUIRED，不能返回空计划并通过。
5. 将检查计划冻结进采购快照关联的不可变 artifact。数据更新创建新 revision／快照，不覆盖旧计划。

当前 `Electronics / SG / 2026.09.1` 的目录设计如下：

| control_code | 何时检索 | 确定性执行要求 | 阶段 |
| --- | --- | --- | --- |
| `QUOTE_COMPLETENESS` | 每次评估 | 商务完整性、报价主体、有效期及费用状态；字段冲突见语义审查报告 | 推荐前 |
| `TOTAL_COST` | 每次评估 | 已知且完整的成本；非 SGD 或非 NOT_APPLICABLE 税模式未支持时阻塞 | 推荐前 |
| `APPROVED_SUPPLIER` | 每次评估，不设 S$5,000 门槛 | 确认主体后精确查批准记录 | 推荐前 |
| `ROHS_COMPLIANCE` | 每次 Electronics 评估，不设金额门槛 | 供应商／物料匹配及有效证据 | 推荐前 |
| `AMOUNT_APPROVAL` | 每次评估，低于阈值也要有判断依据 | total landed cost SGD >= 10000.00 需要经理审批；低于阈值仍走普通人工审批 | 审批资格 |
| `QUOTE_CHANGE_REVIEW` | 每次评估 | INITIAL 检查无旧版本继承；变更时检查新 revision、重新审核及旧结果失效 | 版本完整性 |

阈值触发的是经理审批要求，不是是否检索审批制度。总成本未知时 `manager_approval_required=null`，不得按零判为不需要。预算 S$8,000 与经理审批阈值 S$10,000 是两个不同概念。

## 4. 检索调度与引用覆盖

每个控制码生成一个现有 `RetrievalRequest`，`required_control_codes` 只放当前控制码。原因：现有 `RetrievalResult.citations` 最大为 3，一次请求 6 个控制码必然无法全面覆盖。六次子检索的引用放入 bundle，不能塞进一个现有 RetrievalResult。

检索沿用 BM25 Top-10、pgvector Top-10、RRF k=60、rerank Top-3。若单一控制码有多个独立要求，而 Top-3 不足以支持全部要求，使用该控制码的要求级固定英文查询补检索；每个要求最多一次补检索，每次模型最多 2 次尝试，调用记账不重置。仍不足则 REVIEW_REQUIRED，不降低覆盖标准。

要求级映射由经过语义审阅的目录明确指定可支持条款和结构化参数；引用身份、原文、哈希、范围、版本验证后，再检查所需要求是否被支持。不能仅凭 `covered_control_codes` 或“引用了一个真实条款”宣布所有规则获得支持。此要求级聚合尚待实现。

### 现有检索输入示例

下面是设计示例，不是真实采购或供应商资质证明；可直接由现有 `RetrievalRequest` 校验。

```json
{
  "task_id": "task_demo_rag",
  "task_revision": 6,
  "snapshot_id": "snapshot_demo_rag",
  "policy_set_version": "2026.09.1",
  "policy_index_version": "pidx-375fa65c082096e41d4f6b66",
  "query": "What current RoHS evidence and exact supplier and part matching are required for electronics procurement?",
  "required_control_codes": ["ROHS_COMPLIANCE"],
  "category": "Electronics",
  "region": "SG",
  "evaluated_at": "2026-09-16T08:00:00+08:00"
}
```

现有完整检索输出示例见 `../data/examples/policy_rag/retrieval_result.json`。业务输出示例：

```json
{
  "schema_version": "rag-flow-v1",
  "assessment_id": "assessment_demo",
  "snapshot_id": "snapshot_demo_rag",
  "task_revision": 6,
  "policy_set_version": "2026.09.1",
  "policy_index_version": "pidx-375fa65c082096e41d4f6b66",
  "supplier_registry_version": "registry-demo-v1",
  "control_catalog_version": "catalog-demo-v1",
  "evaluated_at": "2026-09-16T08:00:00+08:00",
  "evidence_bundle_id": "bundle_demo",
  "supplier_assessments": [
    {"quote_id": "quote_b", "supplier_master_id": null, "identity_match_id": null,
     "status": "REVIEW_REQUIRED", "checks": [], "reasons": ["SUPPLIER_IDENTITY_UNCONFIRMED"]},
    {"quote_id": "quote_c", "supplier_master_id": "master_c", "identity_match_id": "match_c",
     "status": "REVIEW_REQUIRED", "checks": [], "reasons": ["ROHS_RECORD_MISSING"]}
  ],
  "selection": {"status": "REVIEW_REQUIRED", "selected_quote_id": null,
                "blocking_quote_ids": ["quote_b", "quote_c"]}
}
```

## 5. 状态、人工处理与候选选择

| 输入状态 | 业务映射 | 允许的处理 |
| --- | --- | --- |
| 检索 `OK` | 继续要求级覆盖和事实核验，不直接 COMPLIANT | 核验要求与结构化证据 |
| `NO_EVIDENCE` | REVIEW_REQUIRED／POLICY_EVIDENCE_MISSING | 定位缺失要求，受控补检索或发布修订制度 |
| `CONFLICT` | REVIEW_REQUIRED／POLICY_CONFLICT | 修订制度并发布新版本，不让用户随意挑一条放行 |
| `ERROR` | REVIEW_REQUIRED／POLICY_RETRIEVAL_ERROR | 管理侧修复或受控重试；不是报价字段缺失 |
| 身份无匹配／多匹配、记录缺失／有效期不明确 | REVIEW_REQUIRED | 确认主体或补充有来源的新记录版本 |
| 明确 inactive、证书过期或物料不适用 | NON_COMPLIANT（待制度修订一致后启用） | 保存证据与原因；不能人工无依据豁免 |

人工事件绑定 snapshot、当前候选／证据 artifact 哈希、revision、操作者及原因。回答推进 revision 并重新评估；旧问题不能作用于新版本。制度冲突不能通过“确认正确”消除，服务故障应受控重试，不要求用户修改报价。

按原价格／交期顺序扫描：明确 NON_COMPLIANT 可跳过；遇到 REVIEW_REQUIRED 则阻塞更低候选；遇到首个 COMPLIANT 且更高候选均明确不合规，则 ELIGIBLE_FOR_APPROVAL。低排名待确认不阻止已经合规的高排名候选。全部明确不合规返回 NO_COMPLIANT_SUPPLIER；没有 FEASIBLE 返回 NO_FEASIBLE_SUPPLIER。全局制度审查或覆盖失败阻塞所有候选。

## 6. 解释、版本和留痕边界

解释只能消费已确认事实、确定性结果及本次有效 citation；不得改金额、排名或合规状态。现有解释校验能检测越界 citation ID 和事实对象变化，不能证明自然语言主张真正被条款支持。真实模型适配与主张支持验证仍属后续任务。解释失败可保留确定性结果草稿，但不能计作完整报告验收。

冻结并保存 task revision、snapshot 哈希、制度／索引／目录／注册表版本、全部 retrieval ID、引用 ID、事实记录版本、规则版本与评估时间。显式切换版本推进 revision；发布、审批、报告事务重新检查当前 revision 与时间有效性。

制度导入 embedding、query embedding、rerank、解释分开记录尝试、用量、延迟和错误；子检索范围、要求级补检索与总预算写入计划。同一 graph run 恢复复用已完成 artifact，不重置预算。

## 7. 本次设计验收与后续实现边界

后续实现进展（2026-09-16）：已完成新制度修订、真实导入验证与检索编排，见 [guide_RAG_ORCHESTRATION.md](guide/guide_RAG_ORCHESTRATION.md)。下面清单是第一轮设计交付时的历史状态；新版本为 `2026.09.2`，旧版仍保留审查冲突。供应商合规／主图接入等后续任务仍未实施。

- 已交付：流程、对象字段、版本约束、控制码生成规则、Top-3 调度方案、状态映射、候选选择与输入输出示例。
- 已完成制度文档审查：详见 `guide/POLICY_SEMANTIC_REVIEW.md`，结论为 CHANGES_REQUESTED，不允许将原制度标记为生产／最终推荐可用。
- 未实施：ControlCatalog 运行时、要求级聚合、供应商表、合规服务、LangGraph 接入、人工接口、真实解释客户端、页面或部署。
- 原制度与 manifest 保持不变；先按审查报告创建新制度版本，再实现上述功能。不能用本设计代替 RAG live、联合闭环或 Lightsail 验收。
