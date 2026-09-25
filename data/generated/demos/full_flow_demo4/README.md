# full_flow_demo4

全新 MCU 商业取舍开发测试包，所有报价和制度材料均为合成数据。不是 demo3 的复制品。

## 开始

1. 上传并发布 `policy/electronics_sg/`；按 `upload_metadata.json` 和 `reviewed_clauses.json` 完成人工核对。
2. 创建新任务时绑定刚发布的 Electronics/SG 制度，上传 `requirement/procurement_requirement.txt`（PDF/MD 等价），核对 `confirmed_requirement.json`。若只回归旧的无制度基线，才创建不绑定制度的独立任务。
3. 上传 `quotes/pdf/` 四份 PDF，或 `quotes/csv/` 四份 CSV；两套不要混传。供应商 ID 使用 manifest 所列值。
4. 核对全部字段并正式提交四份报价。PDF 路径含真实模型提取；固定 CSV 不需要模型。
5. 进入制度检查，先上传 `compliance_evidence/initial/` 的八份材料并确认结果；再使用 `corrections/` 的三份材料逐项执行“替换材料”。确认制度检查后再进入决策比较。
6. 每个 `variants/` 用例使用新任务，只替换指定供应商的一份报价，其余三家沿用主场景，不要把全部变体一起上传。
7. 历史数据可绑定现有 `synthetic-mcu9-supplier-performance / 2026-08-06-v1`，不要新增虚构评级。`policy/unrelated_office_eu/` 是范围隔离反例，不要绑定到 SG 电子采购。

制度材料的录入值见 `compliance_evidence/entry_guide.json`；它只是人工录入辅助，不替代阅读原文。完整步骤见仓库 `docs/TESTING.md`，预期结果位于 `evaluation/reference/full_flow_demo4/`；这些离线验收资料禁止上传给运行时 Agent。

## 数据边界

固定评估时间：2026-11-02，报价有效至 2026-11-30。以后重测若过期，另建有独立预期的版本，不自动使用今天改变结果。
保留原 MCU 标识、单商品采购和六指标主/次排序，不测试新器件选型或替代兼容性。
自然语言只解释制度与偏好；金额、硬约束、证据匹配和状态由确定性代码计算，材料事实由人工确认。
本目录不含参考推荐、人工补充答案或模型评测输出。

## 再生成

```bash
PYTHONPATH=src .venv/bin/python data/generate_full_flow_demo4.py
```

脚本只重建本数据包及其独立留出版式，不触碰 demo1/2/3、数据库或其他代码。生成器不导入参考答案或计算引擎。
