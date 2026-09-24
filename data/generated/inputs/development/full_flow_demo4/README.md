> **范围说明（2026-09-24）**：平台最小版本已支持结构化供应商准入与 RoHS 检查；本数据集暂未提供对应样例，建议使用 `full_flow_demo3` 验证。

# full_flow_demo4

全新 MCU 商业取舍开发测试包，所有报价均为合成数据。不是 demo3 的复制品。

## 开始

1. 创建新任务，上传 `requirement/procurement_requirement.txt`（PDF/MD 等价），核对 `confirmed_requirement.json`。
2. 先不绑定 Policy；历史数据可绑定现有 `synthetic-mcu9-supplier-performance / 2026-08-06-v1`，不要新增虚构评级。
3. 上传 `quotes/pdf/` 四份 PDF，或 `quotes/csv/` 四份 CSV；两套不要混传。供应商 ID 使用 manifest 所列值。
4. 核对全部字段并正式提交四份报价，再手动开始比较。PDF 路径含真实模型提取；固定 CSV 不需要模型。
5. 每个 `variants/` 用例使用新任务，只替换指定供应商的一份报价，其余三家沿用主场景，不要把全部变体一起上传。
6. 第二轮绑定 `policy/electronics_sg/` 已发布制度，检查缺失证明的人工核验流程。`unrelated_office_eu` 是独立不适用制度，不要混入 SG 制度的相同 scope。

完整人工步骤、回答、预期推荐与测试记录在仓库 `evaluation/reference/full_flow_demo4/` 和 `docs/guide/guide_FULL_FLOW_DEMO4_TESTING.md`。它们是离线验收资料，禁止上传给运行时 Agent。

## 数据边界

固定时间：计划下单 2026-11-02，报价有效至 2026-11-30。以后重测若过期，另建有独立预期的版本，不自动使用今天改变结果。
保留原 MCU 标识、单商品采购和六指标主/次排序，不测试新器件选型或替代兼容性。
自然语言仅提出偏好；金额、硬约束和结果由既有确定性引擎计算，应用前必须确认。
本目录不含参考推荐、人工补充答案或模型评测输出。

## 再生成

```bash
PYTHONPATH=src .venv/bin/python data/generate_full_flow_demo4.py
```

脚本只重建本数据包及其独立留出版式，不触碰 demo1/2/3、数据库或其他代码。生成器不导入参考答案或计算引擎。
