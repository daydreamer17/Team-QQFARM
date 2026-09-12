# Lumos 工作完成说明与使用指引

## 1. 本次工作范围

本次工作对应第一周成员 C 的职责，主要包括：

- 供应商比较的确定性业务规则。
- B 提取结果到 C 规则计算的接入。
- 主场景和边界条件测试。
- 本地 PostgreSQL、Docker Compose 和持久化卷。
- 报价提取字段的关键性与审查规则。

本次不包含前端、云端部署、模型提取逻辑、业务 API、Alembic 正式迁移和 LangGraph 恢复流程。

## 2. 已完成功能

### 2.1 规格检查

系统会比较采购需求与供应商报价中的：

- `manufacturer`
- `manufacturer_part_number`
- `package`
- `revision`
- `condition`

字段缺失或冲突时进入 `PENDING`；明确不匹配时进入 `INFEASIBLE`。

### 2.2 采购数量计算

系统支持 MOQ、包装数量和采购步长换算，计算公式为：

```text
实际采购量 = 向上取整(max(需求数量, MOQ换算数量) / 采购步长) × 采购步长
```

例如：需求1,050颗、MOQ 1,000颗、采购步长100颗，实际采购量为1,100颗。

### 2.3 成本计算

系统使用 `Decimal` 计算：

- 商品成本
- 运费
- 其他费用
- 已知成本小计
- 确认后的总成本

未知费用不会被当成零。只要必要费用未知，总成本保持未知，报价进入 `PENDING`。

### 2.4 交期与有效期

系统检查：

- 相对交期天数
- 自然日或工作日
- 到货或发运语义
- 交期起算事件
- 报价有效截止日期

当前 MVP 支持“从下单日期开始，按自然日计算到货日期”。其他表达进入 `PENDING`，不能自行推断。

### 2.5 可行性与推荐

单家供应商状态包括：

- `FEASIBLE`：所有必要信息已确认且满足要求。
- `INFEASIBLE`：存在明确的规格、预算、交期或有效期失败。
- `PENDING`：关键字段缺失、冲突或暂不支持。

存在可能改变最终排名的 `PENDING` 报价时，系统不会提前宣布最终最优供应商。

### 2.6 提取审查与安全交接

B 的提取结果不能直接进入 C，必须先生成 `ReviewEnvelope`。其中包含：

- 提取后的 `ExtractionBatch`
- 自动审查结果和问题字段
- 人工确认记录 `ReviewEvent`
- 人工修改记录 `CorrectionEvent`
- `downstream_ready`：是否允许交给 C
- `calculation_inputs_complete`：计算字段是否完整

只有 `downstream_ready=true` 才能进入 C。人工确认“报价确实缺少某字段”后可以交给 C，但只要计算字段仍不完整，C 必须输出 `PENDING`，不能发布最终推荐。

## 3. MCU-DEMO-001 验收结果

| 供应商 | 结果 | 采购数量 | 成本 | 说明 |
| --- | --- | ---: | ---: | --- |
| A | `INFEASIBLE` | 2,000颗 | S$12,800 | 同时超预算和超交期 |
| B 初始 | `PENDING` | 1,000颗 | 已知小计S$6,800 | 运费缺失，总成本未知 |
| C | `FEASIBLE` | 1,000颗 | S$7,100 | B 未确认时不能宣布 C 最优 |
| B 补充运费后 | `FEASIBLE` | 1,000颗 | S$7,000 | 补充S$200运费后推荐 B |

当前已经使用三份真实结构的 `ReviewEnvelope` 完成端到端验证：

```text
ReviewEnvelope → C 确定性计算 → 状态、成本和推荐
```

- 补充 B 运费前：`PENDING_INPUT`，禁止最终推荐。
- 授权补充 B 运费 S$200 后：生成两条 `CorrectionEvent`，B 总成本为 S$7,000，最终推荐 B。

当前规则模块（含 B→C 审查后交接）测试结果为：

```text
60 passed
```

全仓库最近一次回归结果为：

```text
234 passed, 2 failed
```

两个失败来自 Windows CRLF 与仓库 LF 字节导致的 V2 字段字典哈希不一致，不属于 C 计算规则故障。A 最新分支已经加入 LF 约束，待相关更新合入后重新执行全仓库回归。

## 4. 主要文件入口

### 4.1 规则代码

| 文件 | 用途 |
| --- | --- |
| `src/supplier_comparison/rules/contracts.py` | C 的输入、输出和状态契约 |
| `src/supplier_comparison/rules/specification.py` | 规格匹配 |
| `src/supplier_comparison/rules/quantity.py` | MOQ、包装和采购步长换算 |
| `src/supplier_comparison/rules/cost.py` | 商品、运费和其他费用计算 |
| `src/supplier_comparison/rules/delivery.py` | 交期与报价有效期检查 |
| `src/supplier_comparison/rules/engine.py` | 汇总状态、排名和推荐 |
| `src/supplier_comparison/rules/integration.py` | 将审核通过的 `ReviewEnvelope` 安全转换为 C 输入 |

### 4.2 提取审查接口

| 文件 | 用途 |
| --- | --- |
| `src/supplier_comparison/extraction/review_contracts.py` | 定义 `ReviewEnvelope`、审核状态和人工事件 |
| `src/supplier_comparison/extraction/criticality.py` | 解析 16+10+4 字段关键性策略 |
| `src/supplier_comparison/extraction/review.py` | 执行确定性审查门禁 |
| `src/supplier_comparison/extraction/human_review.py` | 生成人工确认记录 `ReviewEvent` |
| `src/supplier_comparison/extraction/corrections.py` | 生成新字段版本和 `CorrectionEvent` |
| `src/supplier_comparison/extraction/readiness.py` | 阻止未就绪结果进入 C |

### 4.3 测试与运行脚本

| 文件 | 用途 |
| --- | --- |
| `tests/rules/test_specification.py` | 规格规则测试 |
| `tests/rules/test_quantity.py` | 数量规则测试 |
| `tests/rules/test_cost.py` | 成本规则测试 |
| `tests/rules/test_delivery.py` | 日期与交期测试 |
| `tests/rules/test_engine.py` | 状态、排序和推荐测试 |
| `tests/rules/test_b_to_c_integration.py` | 真实字段契约和 B→C 集成测试 |
| `tests/rules/test_reviewed_extraction_integration.py` | 验证未审核阻断、确认缺失和补值后的完整链路 |
| `tests/extraction/test_review_gate.py` | 审查门禁测试 |
| `tests/extraction/test_corrections.py` | 人工修改与审计记录测试 |
| `tests/extraction/test_human_review.py` | 人工确认缺失或冲突测试 |
| `scripts/run_mcu_reviewed_comparison.py` | 运行补运费前后的 MCU 端到端演示 |

### 4.4 环境与说明

| 文件 | 用途 |
| --- | --- |
| `compose.yaml` | 本地 PostgreSQL 和持久化卷配置 |
| `.env.example` | 本地环境变量示例 |
| `docs/LOCAL_ENVIRONMENT.md` | Docker 与 PostgreSQL 使用说明 |
| `docs/EXTRACTION_REVIEW_FIELD_CRITICALITY.md` | 关键字段和提取审查规则 |

## 5. 提取结果使用规则

模型原始结果使用 `pre_correction` 文件保存，不允许覆盖。人工补充或纠正必须生成新版本和 `CorrectionEvent`，至少保留：

- 修改前值和修改后值
- 修改人、修改时间和修改原因
- 依据的来源 ID
- 报价、文档和字段版本
- 文档 SHA-256
- `origin` 与 `validation_status`

关键字段只有在以下内容均正确时才能进入下游计算：

```text
normalized_value
+ unit
+ source_refs
+ origin 和 validation_status
```

任何一项无法确认时，应进入 `MISSING`、`CONFLICT` 或 `PENDING`。

供应商 C 的原始模型输出曾将：

```text
shipping_fee_status = PAID
```

该值不在允许枚举范围内。修正版使用：

```text
shipping_fee_status = KNOWN_AMOUNT
shipping_fee_amount = 500.00
```

原始提取文件仍保留，用于模型准确率评估和审计。

当前合成演示使用 `synthetic-demo-reviewer` 作为审核人，只用于测试，不代表真实采购人员审批。生成的本地 `ReviewEnvelope` 和比较结果位于 `evaluation/results/local/`，不应提交 Git。

## 6. 本地环境使用

在 PowerShell 中进入仓库：

```powershell
Set-Location "...\Lumos088"
```

启动 PostgreSQL：

```powershell
docker compose up -d --wait postgres
```

查看健康状态：

```powershell
docker compose ps
```

测试数据库连接：

```powershell
docker compose exec -T postgres psql -U supplier_app -d supplier_comparison -c "SELECT 1;"
```

查看持久化卷：

```powershell
docker volume ls --filter name=supplier-comparison
```

停止并删除容器、保留数据：

```powershell
docker compose down
```

不要执行以下命令：

```powershell
docker compose down -v
```

`-v` 会删除数据库卷和报价文件卷。

## 7. 测试指令

运行 C 规则测试：

```powershell
python -m pytest tests/rules -q
```

运行审查接口与 C 规则测试：

```powershell
python -m pytest tests/extraction/test_review_contracts.py tests/extraction/test_criticality.py tests/extraction/test_review_gate.py tests/extraction/test_corrections.py tests/extraction/test_human_review.py tests/extraction/test_readiness.py tests/rules -q
```

使用 B 生成的 `ReviewEnvelope` 运行完整比较：

```powershell
python scripts/run_mcu_reviewed_comparison.py --envelope-dir "E:\iss_hackathon\B\evaluation\results\local\2026-09-12\review_envelopes"
```

该流程只运行本地确定性代码，不需要 API Key。

运行全仓库测试：

```powershell
python -m pytest -q
```

## 8. 已验证的环境能力

本地环境已经完成以下实际验证：

- PostgreSQL 容器健康检查通过。
- `supplier_app` 可以连接 `supplier_comparison` 数据库。
- 数据库卷和报价文件卷均已成功挂载。
- 删除并重建容器后，数据库记录仍然存在。
- 删除并重建容器后，报价文件仍然存在。
- 验收使用的临时表和临时文件已经清理。

## 9. 与其他成员的交接

### A

- 提供字段业务含义和独立参考答案。
- 使用 C 的确定性结果做业务验收。

### B

- 正式交付物应为 `ReviewEnvelope`，不能只交付修改后的 `ExtractionBatch`。
- 只有 `downstream_ready=true` 才能进入 C；已确认确实缺失的字段进入 C 后仍输出 `PENDING`。
- 对非法枚举、缺失证据和模型提取错误进行上游拦截，并保留 `ReviewEvent` 或 `CorrectionEvent` 审计记录。
- C 不重新解析 PDF，也不猜测 B 未提供的值。

当前 MCU 合成演示的三份 `ReviewEnvelope` 已经生成并通过 C 接口校验。真实业务中仍必须使用真实审核人身份，不能沿用 `synthetic-demo-reviewer`。

### D

- 调用 `supplier_comparison.rules` 完成比较，不重复实现计算公式。
- 提供 Alembic 迁移后，C 验证正式业务表的创建和持久化。
- 提供 API 与 worker 启动命令后，再将对应服务加入 Compose。
- 负责修订号、快照、幂等、纠正记录和 LangGraph 恢复。

## 10. 当前剩余事项

- 等待 A 的 LF 字段字典修复合入共享分支，再重新执行全仓库测试并确认 `0 failed`。
- 等待 D 提供 Alembic 正式迁移并完成数据库表验收。
- 等待 D 提供 API 和 worker 启动入口并加入 Compose。
- 与 A、B、D 完成一次端到端交叉验收。
- 前端和云端部署按当前决定暂不执行。

在以上依赖到位前，C 独立负责的规则计算、B→C 安全交接、端到端演示和本地 PostgreSQL 基础环境已经完成。
