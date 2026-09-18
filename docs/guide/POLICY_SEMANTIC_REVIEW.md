# Electronics 制度语义审阅清单

> 当前状态：2026-09-16 已完成基于仓库证据的设计语义审查，结论 `CHANGES_REQUESTED`，详见第 6 节。原 A／C 分工不再作为本轮责任划分；未冒充人员签字。本文件不改变已发布制度内容、manifest 哈希或索引版本。

## 1. 冻结范围

- 制度集合：`electronics-procurement`
- 制度版本：`2026.09.1`
- 适用品类：`Electronics`
- 适用地区：`SG`
- 生效起点：`2026-01-01T00:00:00Z`（含）
- 文档数量：5
- 条款数量：24
- 当前索引版本：`pidx-375fa65c082096e41d4f6b66`

这些制度完全是 hackathon 演示用虚构内容，不应被描述为 NUS、AWS、新加坡政府或任何真实组织的正式采购政策。

## 2. 审阅原则

A 负责人核对字段、证据、版本和审核语义；C 负责人核对成本、数量、门禁和推荐边界。两位负责人都应确认：

- control code 由调用方确定，RAG 只定位制度原文；
- 供应商身份、批准状态和 RoHS 事实来自结构化记录，而不是文本相似度；
- 未知费用不能当作零，缺少关键成本时不能发布最终推荐；
- RAG 的 `OK` 只表示找到了制度依据，不等于供应商合规；
- 条款、control code、参数或有效期变更必须创建新制度版本和索引，不能覆盖已发布正文。

## 3. 逐项审阅

| 文档／条款 | control code | 需要确认的业务含义 | A | C | 备注 |
| --- | --- | --- | --- | --- | --- |
| `QTE-001`–`QTE-004` | `QUOTE_COMPLETENESS` | 报价必须包含商业字段、供应商与报价标识、明确有效期；费用状态只能是已知金额、确认零或未提供 | 待签字 | 待签字 | |
| `TCO-001`–`TCO-004` | `TOTAL_COST` | landed cost 包含必要成本；可抵扣税单列；跨币种依赖已批准汇率快照；缺少重要成本时结果保持 pending | 待签字 | 待签字 | |
| `ASL-001`–`ASL-004` | `APPROVED_SUPPLIER` | 只按已确认 supplier ID 查询当前 registry；inactive、过期或缺记录不能当作 approved | 待签字 | 待签字 | |
| `ROHS-001`–`ROHS-004` | `ROHS_COMPLIANCE` | Electronics 的 RoHS 证据必须匹配供应商和物料，且当前有效；相似度不能建立合规事实 | 待签字 | 待签字 | |
| `APR-001`–`APR-004` | `AMOUNT_APPROVAL` | SGD 10,000（含）按 landed cost 触发经理审批；外币使用评测时间有效的批准汇率；审批绑定 revision 和金额 | 待签字 | 待签字 | |
| `CHG-001`–`CHG-004` | `QUOTE_CHANGE_REVIEW` | 影响价格、数量、费用、MOQ 或交期的变更创建新版本／revision 并重跑；历史结果不可覆盖当前结果 | 待签字 | 待签字 | |

## 4. 一致性问题

签字前必须逐项回答：

1. `QTE-001` 的必填字段是否与 A 的字段字典和 B 的 schema 1.1 一致？
2. `TCO-001`–`TCO-004` 是否与 C 的 Decimal 成本、未知不当零和 `PENDING` 规则一致？
3. `ASL-002` 与 `ROHS-003` 是否明确要求先完成确定性身份匹配？
4. `APR-001` 的 `SGD 10000.00`、`>=` 和 landed-cost 计价基础是否是团队希望演示的阈值？
5. `CHG-001`–`CHG-004` 是否与 task revision、snapshot、旧运行 supersede 和迟到结果保护一致？
6. 生效期、品类和地区过滤是否覆盖主演示场景，并能让旧版本测试稳定返回无证据？
7. 是否存在相互矛盾、暗示真实机构背书或把检索结果当作合规判定的措辞？

发现问题时，不直接修改已发布的 `2026.09.1`。先记录问题，创建新制度版本，重新计算内容哈希和索引版本，再重新运行 8 题开发集。

## 5. 签字记录

| 角色 | 姓名／成员代号 | 结论 | 日期（UTC） | 未解决问题 |
| --- | --- | --- | --- | --- |
| A：字段与审核语义 | 待填写 | `APPROVED`／`CHANGES_REQUESTED` | 待填写 | 待填写 |
| C：计算与推荐边界 | 待填写 | `APPROVED`／`CHANGES_REQUESTED` | 待填写 | 待填写 |

只有两行均为 `APPROVED` 且没有未解决问题时，交接指南中的“A／C 制度业务语义签字”才可改为已完成。

## 6. 本轮设计语义审查结果（2026-09-16）

审查依据：5 份制度全部 24 条原文及 manifest、`data/contracts/quote_data_field.csv`、`src/supplier_comparison/rules/cost.py`、Week2 计划和工作流。以上旧签字表作为历史模板保留，不代表当前分工或已签字。

| 条款 | 结论 | 仓库证据与处理决定 |
| --- | --- | --- |
| QTE-001、QTE-003 | CHANGES_REQUESTED | 制度要求 quote_date 必填且有效截止当天起需复核；字典允许明确 valid_until 时 quote_date 缺失，且日期有效到新加坡当日结束。修订制度应允许已确认截止日期及受控相对期限推导；日期型截止使用 Asia/Singapore 次日 00:00 为排他上界，明确时刻则保留其精度。制度有效期仍用左闭右开，两者不要混淆 |
| QTE-002 | CHANGES_REQUESTED | 原文要求报价印 supplier identifier；字典将 supplier_id 视为系统建立／核对字段，报价可不印。修订为报价名称及 quote reference 可追溯，系统确认 supplier_master_id 后才允许资质核验，不将系统值冒充提取事实 |
| QTE-004 | 一致 | 已知金额、确认零、未提供是业务语义，不是直接替换字段枚举；复用 KNOWN_AMOUNT、FREE、INCLUDED、NOT_APPLICABLE、UNKNOWN 及证据状态。INCLUDED 表示不重复加计，不表示原费用为零 |
| TCO-001–TCO-004 | 原则一致／能力受限 | 未知费用保持 PENDING、Decimal 计算一致；现有 cost.py 不支持跨币种换算或非 NOT_APPLICABLE 税模式。不得宣称已实现关税／可抵扣税独立计算；演示限定 SGD 和显式不适用税，其他情况阻塞，不能由 RAG 补算 |
| ASL-001–ASL-004 | CHANGES_REQUESTED | exact identity、inactive 不合规一致；ASL-004 把过期批准与缺记录一起要求 review，而 workflow 对已证明批准无效允许 NON_COMPLIANT。修订明确：缺失／不确定 REVIEW_REQUIRED，已证明过期 NON_COMPLIANT；不设 S$5,000 门槛 |
| ROHS-001–ROHS-004 | CHANGES_REQUESTED | 身份及物料精确匹配一致；ROHS-004 对过期／不匹配一律 review，与 workflow 的明确过期／不适用 NON_COMPLIANT 不一致。修订区分缺失／不确定和已证明无效；接受的声明／证书须有可核验适用范围和有效性，签发日期本身不足以证明仍有效 |
| APR-001–APR-004 | 设计一致／尚未实现 | SGD 10000.00、>=、landed cost 与 workflow 一致，沿用现有演示阈值而非新设商业政策；审批资格与供应商合规分离。未获经理审批可以是合规候选，但不能发布有效最终批准报告。外币未支持时阻塞，成本未知不能按零判断 |
| CHG-001–CHG-004 | 原则一致／待实现边界 | 商务变更须版本化重跑，旧结果不可覆盖；行政更正也要审计。凡会影响供应商身份或证据关联的“拼写更正”必须重评估，不能仅因金额未变免除版本保护 |

对原第 4 节的七个问题：1 不完全一致，见 QTE 修订；2 原则一致但税／汇率未实现；3 已明确要求确定性身份；4 与现有 workflow 阈值一致，未声称真实组织批准；5 原则一致但新版功能未实现；6 当前 Electronics／SG／2026 主场景适用，范围外或生效前必须无证据并阻塞；7 全部是虚构演示制度，存在以上真实语义冲突，不得宣传机构背书。

### 6.1 修订与放行顺序

1. 保留已发布 `2026.09.1` 和索引不动，本轮不改正文或 manifest。
2. 下一实现任务创建新制度版本，修订 QTE、ASL、ROHS 的上述冲突，并绑定审阅过的要求级 ControlCatalog。
3. 新版本重新计算哈希、完整建索引、运行 8 题开发评测及日期／身份／过期边界回归。
4. 未解决冲突为全局 POLICY_SEMANTICS_UNRESOLVED，实际采购不能据此产生最终 COMPLIANT／有效推荐。开发检索评测可继续，但不计为业务放行。

本轮审查者：Codex（仓库一致性设计审查）；结论：`CHANGES_REQUESTED`。审查已完成不等于制度获准使用。新的流程接口和必需控制码规则见 [`../RAG_FULL_FLOW_DESIGN.md`](../RAG_FULL_FLOW_DESIGN.md)。

## 7. 新版本修订复核（2026-09-16）

已创建 `data/policies/electronics-v2/` 的 `2026.09.2`，未覆盖 `2026.09.1`。QTE-001／002／003、ASL-004、ROHS-002／004、APR-001、CHG-003 已按第 6 节修订，全部 24 条复核完成。新版本仓库设计一致性结论为 `APPROVED_FOR_DEMO_DESIGN`；这是虚构演示制度的设计复核，不冒充成员签字或真实机构采购政策批准。

税／汇率等尚未实现能力不因制度发布而变为可用：编排对非 SGD、非 NOT_APPLICABLE 税及未知成本直接阻塞。新版本检索编排已真实验证 24／24 引用要求覆盖；旧版本仍维持 CHANGES_REQUESTED，不得用于当前设计的最终合规放行。资质记录和采购集成尚未实现，任何版本的检索 READY 都不等于实际供应商合规。

实现与运行步骤见 [`guide_RAG_ORCHESTRATION.md`](guide_RAG_ORCHESTRATION.md)。
