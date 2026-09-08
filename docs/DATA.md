# 数据设计与演示集

> v0.4 · 2026-09-08 · 加入正式采购制度 RAG 数据与评测契约。相关文档：[选型方案](方案.md)、[架构](ARCHITECTURE.md)、[项目计划](../Supplier_Comparison_Project_Plan.md)。

本文定义待制作的数据集、字段、报价文档和评测答案。原始 CSV 已在线核对；演示规格、生成器、PDF 和评测文件是待实现设计，不表示文件已经生成或系统已经通过测试。

## 1. 数据来源与范围

使用 [Procurement Spend Analysis Dashboard](https://github.com/dytcoke23/procurement-spend-analysis-dashboard) 的 [purchase_orders.csv](https://github.com/dytcoke23/procurement-spend-analysis-dashboard/blob/main/data/purchase_orders.csv) 作为业务素材。原项目由固定随机种子合成历史采购订单，不提供本场景的原始供应商报价 PDF。保留其 [MIT 许可证](https://github.com/dytcoke23/procurement-spend-analysis-dashboard/blob/main/LICENSE) 与归属信息。

2026-09-08 在线读取 CSV 后统计如下。上游 main 分支可能变化，落地时必须保存实际下载文件及来源清单，以哈希绑定版本。

| 项目 | 实际统计 |
| --- | --- |
| 全部采购记录／供应商 | 47,128 条／106 家 |
| Electronics 记录／供应商 | 6,123 条／15 家 |
| Microcontroller MCU-9 | 1,185 条／15 家供应商 |
| Power Supply Unit 450W | 1,319 条 |
| Connector Kit CK-8 | 1,194 条 |
| Sensor Module SM-3 | 1,221 条 |
| PCB Assembly A-11 | 1,204 条 |

本次 CSV SHA-256：`fba22a467e5dd800a0af960930d2ca5e2ecbd9d2bc747e37304867d8d031ae21`。

**首版只以 Electronics／Microcontroller MCU-9 为主演示商品。** 保留完整原始数据，提取 Electronics 子集作为背景，再选 MCU-9 的 3–5 家供应商记录作为场景素材；不将全部历史记录逐条转换为报价。其他商品作为后续扩展候选。

原始字段共 16 个：

```text
po_id, order_date, promised_delivery_date, actual_delivery_date,
supplier_id, supplier_name, supplier_country, category, item,
business_unit, unit_price, quantity, line_total, payment_terms,
on_contract, quality_rejected
```

| 原始字段 | 用途与边界 |
| --- | --- |
| supplier_id／supplier_name／supplier_country | 合成供应商素材；不构成真实企业验证，也不自动决定新场景跨境运费或税费 |
| category／item | 筛选 MCU-9；名称相同不证明制造商、料号和封装一致 |
| unit_price／quantity／line_total | 历史价格和规模参考；不能当成当前报价、MOQ 或含运费总成本 |
| payment_terms／on_contract | 保留历史含义，当前付款与合同条件必须重新明确 |
| promised_delivery_date／actual_delivery_date | 可分析历史表现，不能直接填为本次到货承诺 |
| po_id | 映射为 source_po_id，不能充当新报价编号 |
| quality_rejected | 订单行拒收标志，不等于拒收件数或数量拒收率 |

原数据没有币种、详细器件规格、包装步长、MOQ、运费、税费及报价有效期。新增 SGD 是团队设定，不是确认原币种或完成汇率换算。原项目风险评分不纳入首版推荐；以后展示历史表现时，只使用决策时点前已知的记录。

## 2. MCU 规格与采购需求

MCU-9 是合成目录标签。为使三份文档有共同参考，本文定义以下**虚构演示规格**，不对应已核验的真实器件或数据手册；后续调整须同步场景和参考答案。

| 字段 | 主演示设定 |
| --- | --- |
| category／item | Electronics／Microcontroller MCU-9 |
| manufacturer | QQ Demo Components（虚构） |
| manufacturer_part_number | QW-MCU9-DEMO（虚构料号） |
| package／revision | QFN-32／R1（演示规格） |
| condition／allow_substitutes | NEW／false |
| base_unit | piece，界面显示“颗” |

需求指定同制造商、同料号、同封装及必要版本、全新产品，不判断替代型号的电气、引脚或固件兼容性。规格缺失先待确认，证据明确不匹配时判不可行；已失败报价不必追问无助于排除结论的其他字段。

采购需求与供应商报价分别保存：

| 需求字段 | 含义 |
| --- | --- |
| required_product_spec | 上述规格与必须匹配字段 |
| required_quantity／quantity_unit | 主演示为 1,000 颗 |
| budget_amount／currency／budget_basis | 主演示 S$8,000，含运费、不计税的明确合成口径 |
| delivery_deadline／delivery_location | 2026-09-19；新加坡虚构收货点 SG-DEMO-01 |
| planned_order_date／scenario_clock | 起算日 2026-09-14；受控评测时钟为当日 09:00，Asia/Singapore |
| ranking_preference／secondary_preference | 总成本优先，未指定第二排序项时允许并列 |
| task_id／requirement_version／task_revision | 后端生成；业务输入修改推进版本 |

相对交期按共同起算日、自然日、到货语义换算，起算日为第 0 日。只给工作日或发货时间时，先确认到货日期及依据。受控时钟仅用于标记明确的演示／评测环境；实际部署默认使用真实时间，不能为维持旧报价有效而偷偷移动日期。

## 3. 报价字段与来源契约

| 字段组 | 必要字段／规则 |
| --- | --- |
| 身份与版本 | scenario_id、quote_id、quote_version、supplier_id、document_id；素材有对应原订单时保存 source_po_id |
| 产品规格 | manufacturer、manufacturer_part_number、package、revision、condition；与需求逐项核验 |
| 价格 | unit_price、currency、price_basis_quantity、price_basis_unit；标准化价格基数以颗表示，保留按颗／按盘原始表述 |
| 包装与订购 | packaging_type、units_per_pack、order_multiple_units；每盘数量与是否必须整盘购买分开 |
| MOQ | moq_quantity、moq_unit；转为颗后计算，不能从历史 quantity 推断 |
| 运费 | shipping_fee_status、shipping_fee_amount；UNKNOWN 金额为空，不自动记零 |
| 其他费用与税费 | other_fees、fees_complete、tax_mode；费用明确列出或声明无其他费用，预算采用同口径 |
| 交期 | delivery_date，或 lead_time_days、day_basis、delivery_semantics、start_event、start_date 的完整组合 |
| 商务与时间 | payment_terms、quote_date、valid_until；新版独立核验，不继承旧人工补充 |

费用状态为 KNOWN_AMOUNT、FREE、INCLUDED、NOT_APPLICABLE、UNKNOWN。已包含费用不重复累加，未知费用不形成确定总额。`Q = ceil(max(D, M) / S) × S`，货款为 `Q / B × P`；需求 D、MOQ M、步长 S、计价基数 B 均为颗，P 为覆盖 B 颗的固定价格。

Python 使用 Decimal；PostgreSQL NUMERIC 保留约定的单价精度，API 用十进制字符串传递金额。默认货款行按 ROUND_HALF_UP 舍入至分，再加已确认费用；超出支持精度或与报价金额／舍入规则不符时创建问题，不静默截断。

| 追溯层 | 保存内容 |
| --- | --- |
| 合成数据来源 | 源 URL、哈希、source_po_id、generator_version、seed、base_date；字段标记 COPIED_FROM_SOURCE／SYNTHETIC／DERIVED 等生成来源 |
| 字段状态 | validation_status：EXTRACTED／VERIFIED／MISSING／CONFLICT |
| 运行时来源 | origin：DOCUMENT／USER_INPUT／USER_CORRECTION／DERIVED；与生成来源分开，PDF 中的合成条款仍可有 DOCUMENT 原文依据 |
| 解析证据 | 文件版本、哈希、解析器版本、页码、文本块 ID、位置或 CSV 行列；不伪造缺失字段引用 |
| 用户变更 | 原值、新值、用户、时间、问题与任务版本，保留历史 |
| 运行记录 | graph_run_id／thread_id、job_id、task_revision、snapshot_id、环境／provider／model_id／提示词版本 |

身份、版本与来源 ID 由后端生成或校验。检查点保存执行状态，业务表保存权威事实，补问恢复和新报价失效规则见架构文档。

## 4. 合成、渲染与评测隔离

```text
原始 CSV（只读保存与哈希绑定）
→ 筛选 MCU-9 素材并固定目标规格
→ 固定随机种子＋场景规则生成结构化报价
→ 一致性检查与独立人工参考核验
→ 按场景遮蔽字段并渲染文本 PDF／模板 CSV
→ 系统只读取上传文件与已确认的用户回答
```

价格、MOQ、包装倍数、费用和日期按场景联动生成，采购量与金额由规则推导，不逐字段独立随机。固定生成器版本、源文件哈希、基准日期及各场景随机流；保存最终产物，不能只记录一个全局种子。

PDF 必交付采用 1 种开发版式及 1 种独立留出版式，包含表格／措辞差异；为正式 RAG 调整工时，额外开发版式不列为必做。版式与供应商排名独立变化；用于调试后的盲测模板不能继续充当最终留出模板。pdfplumber 解析英文可提取文本 PDF，每份最多 5 页／5 MB、单任务最多 5 份，均为可配置限制；不增加 OCR。解析器生成稳定证据 ID，LLM 再理解字段；文件生成器与解析器独立。

建议目录如下，当前属于计划，不表示文件已经存在：

```text
data/source/                 原始 CSV、许可证、来源与哈希清单
data/generated/manifests/    场景、种子、生成器版本及素材映射
data/generated/inputs/       可上传 PDF／CSV、采购需求
data/policies/              演示制度 Markdown、集合清单和版本哈希
evaluation/reference/        独立参考答案与模拟用户回答
evaluation/rag/              独立 RAG 问题、参考条款和留出集（不挂载给 Agent）
evaluation/results/          按模型环境区分的实测结果
```

部署仅导入被选中的运行输入；参考答案、完整未遮蔽报价和生成来源映射不得挂载到 Agent 可读取目录或暴露为业务工具。历史查询仅提供受限 SQL 查询，不用于反推被遮蔽条款。模拟回答由测试驱动器经正常 API 提交，提交前不可被 Agent 读取。

Docker Compose 管理 Lightsail 上的应用和存储挂载；文件和报告进入持久化文件卷，业务数据、制度条款、检索轨迹和检查点进入 PostgreSQL 专用卷，均需实例外备份。正式 RAG 仅导入已审阅的制度与清单，BM25 索引可从对应版本重建，不使用向量数据库。制度导入采用明确路径白名单，不能递归收录项目文档、生成器映射或 evaluation 目录。

## 5. 共同主演示与参考答案

场景 ID：`MCU-DEMO-001`。采用第 2 节固定规格、1,000 颗需求、S$8,000 预算及共同收货点。以下全部是新增合成条款，金额经独立手算核对，**不是从历史 CSV 直接得到的报价**。A／B／C 是演示别名，生成时选择不同源供应商并保存映射。

各报价日期为 2026-09-13，有效至 2026-09-20 新加坡当日结束；付款条件为演示 Net 30，到货从 2026-09-14 下单日起算，明确不计税且无其他费用。

| 供应商 | 固定报价 | MOQ／订购倍数 | 运费 | 到货周期 | 实际采购量 | 总成本与结论 |
| --- | --- | --- | --- | --- | --- | --- |
| A | S$640／盘，每盘 100 颗 | MOQ 20 盘／整盘购买 | 免费 | 7 天 | 2,000 颗 | 20 × 640＝S$12,800；超预算且到货超期 |
| B | S$6.80／颗 | MOQ 1,000 颗／允许按颗购买 | S$200 | 3 天 | 1,000 颗 | 1,000 × 6.80＋200＝S$7,000；可行且最低 |
| C | S$660／盘，每盘 100 颗 | MOQ 10 盘／整盘购买 | S$500 | 3 天 | 1,000 颗 | 10 × 660＋500＝S$7,100；可行 |

按以下事件演示；上述完整答案不得直接交给 Agent：

1. B 初始报价省略运费。A 不可行，B 待确认，C 可行；只展示草稿，不能直接宣布 C 最优。
2. Agent 请求确认 B 运费，引用相关上下文而不伪造运费证据。模拟用户回答 S$200 并确认后，保存人工输入、推进版本并恢复关联的 LangGraph 中断。
3. 新快照完整校验和计算，推荐 B：S$7,000；审核人审批当前结果并导出报告。
4. 同一受控场景日上传 B v2，报价日期为 2026-09-14、有效期不变，明确运费 S$200，到货改为 6 天。旧运行／推荐／审批失效；新报价独立解析，不继承旧人工回答。
5. 新运行推荐 C：S$7,100，比旧推荐增加 S$100，满足 2026-09-19 截止；需重新审批。B v2 到货为 2026-09-20，超期。
6. RAG 检索审批变更制度，解释为何旧审批失效并展示文档版本、章节和原文；成本差额仍引用计算结果。B 初始缺运费时也可引用报价完整性制度解释补问原因，不能从制度查到 S$200 的隐藏答案。

另备规格缺失、不匹配料号、单位歧义、已不可行报价缺运费、全不可行、仍待确认、用户排除、并列和自然过期场景。自然过期测试显式推进受控时钟，实际应用不能用演示日期替换真实审批时间。

## 6. 评测组织与交付状态

约 20 个业务场景，建议 12 个开发／8 个留出，按模板划分，不能只换数值。独立测试算术、硬约束和版本事务，不将代码断言计为业务场景。使用同一比较函数生成答案再验证自身不能作为唯一证据，主演示和关键边界需手算或独立实现核对。

参考答案包括期望字段、语义支持证据、阻塞问题、人工回答、采购量、金额、可行性、最优／并列集合及事件后状态；分别记录补问前、回答后、新报价后的结果。覆盖重启、重复回答、过期恢复、迟到结果、旧页面审批、自然过期和文件中的越权指令。

本地开发 API 与主办方 API 使用相同评测输入，分开报告人工修正前字段准确率、证据支持、漏检与误报、修正负担、含人工时间的耗时和调用用量／成本。本地通过不代替 AWS 实测；合成表现不等于真实企业验证。获授权匿名化真实报价可补充验证，首版不以其到位为前提。

待制作交付物：原始数据与许可清单、种子生成器、规格与字段 schema、文本 PDF／CSV、独立参考答案、评测脚本与实际结果。生成前确认具体源供应商映射及字段精度；调整主演示规格或金额时，同步项目计划和架构示例。

## 7. 采购制度 RAG 数据与验收（正式交付）

### 7.1 知识库范围与制作

团队编写 3–5 份简短英文 Markdown 制度，分别覆盖报价完整性、总成本比较、审批与报价变更、Electronics 同规格采购要求；每份按完整条款／章节切分，不能切掉否定条件或例外。首版预计 15–30 个条款，以语义完整性为准，不为数量凑文本。每份显著标记“Fictional procurement policy for demonstration”；不代表 NUS、AWS 或真实企业规定。

制度应与现有确定性规则一致，A 编写、C 交叉复核。例如可以写“可能影响比较的费用未知时需澄清”和“已批准输入条件变化后重新审核”，不能写某个供应商的正确费用、隐藏测试问题、预期排名或主场景答案。制度不是原数据集的一部分，不随报价字段随机生成。

| 记录 | 必需内容 |
| --- | --- |
| 制度集合 | policy_set_version、适用范围、成员文档版本、集合哈希；发布后不可变 |
| 制度文档 | policy_id、document_version、title、language、scope、effective_from／effective_to、source_path、content_hash、is_synthetic |
| 条款 | clause_id、章节标题、完整正文、文档版本和正文哈希；引用键为文档 ID＋版本＋条款 ID |
| 检索轨迹 | retrieval_id、任务修订／快照、集合版本、查询、过滤、检索器／分词版本、Top-k 结果与分数、引用与支持判断、状态／错误、耗时 |

有效区间按 Asia/Singapore 日期使用左闭右开规则，effective_to 可空；集合成员在任务评估日需有效，不满足时记录冲突／无依据，不能混用其他版本。主演示的制度有效期需覆盖 2026-09-14，且不据此改变真实运行时钟。

第一版使用英文问题和英文制度，中文界面按钮映射固定英文问题。BM25 初始 Top-3，固定分词与别名；仅在开发问题上调节参数。报价事实、制度依据、人工输入三者分开展示。运行所需制度只读导入，API 不提供任意路径索引入口。

### 7.2 独立评测与通过标准

正式准备 12 个 RAG 问题：8 个开发、4 个留出，独立于约 20 个采购业务场景，不相加冒充采购任务样本。建议开发集含 4 个有答案、2 个无答案、1 个旧版本过滤、1 个冲突问题；留出集各含有答案、无答案、旧版过滤、冲突问题各 1 个。按问题意图／涉及条款划分；文档可以是运行知识库，留出问题、相关条款标签和期望答案不进入索引或提示词。

- 对有答案且无冲突的问题，人工标注必要条款集合；报告 Recall@3＝Top-3 命中的必要条款数／全部必要条款数，以及逐题结果，目标宏平均至少 80%。开发集、留出集及两种模型环境分别统计、分别判定，不能合并开发成绩抵消留出失败；旧版过滤题若有有效目标条款，也计入该指标。必要条款超过 3 条的问题不放入当前单次检索范围。
- 对输出引用，ID／版本／原文必须全部匹配本次检索结果；人工逐条核对制度主张是否被引用支持，目标 100%，无依据主张同样计为失败，不能靠不输出引用获得通过。
- 无答案时明确说明无法从制度确认；冲突时明确标记并不编造合规结论。这两类问题在开发／留出及两种模型环境中均要求全部通过。错误版本、任务外条款、越权指令与检索故障用确定性边界测试另验，预定测试必须全部通过。引用匹配与主张支持的通过标准也按上述分组分别判定，禁止只报总体平均。
- 主演示“缺运费需补问”和“报价变更需重审”都必须实际检索、生成并显示正确引用；硬编码解释不能代替 RAG 验收。
- 第二周完成 8 个开发问题和边界测试，最终周运行 4 个留出问题；两阶段模型分别报告引用支持、失败、延迟与用量。小样本结果不表示真实采购制度泛化效果；留出集用于调试后必须另备未使用问题并披露。

以上为交付目标，当前文档修改不表示制度文件、索引、代码或评测结果已经制作完成。
