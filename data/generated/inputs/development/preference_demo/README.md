# preference_demo

用于演示 V2 六项排序指标及主/次指标，不替换硬约束回归数据。全部供应商、报价、物料和历史表现均为合成演示数据。

- 场景：`PREFERENCE-DEMO-001`
- 计划下单：`2026-10-10`
- 截止到货：`2026-10-20`
- 历史数据：`synthetic-mcu9-supplier-performance / 2026-08-06-v1`

| 供应商 | 总成本 | 到货 | 付款 | 历史等级 |
|---|---:|---|---|---|
| Sterling Components (SUP-024) | SGD 7,100 | 2026-10-18 | Net 30 after invoice | A |
| Redwood Components (SUP-022) | SGD 6,900 | 2026-10-17 | Net 60 after invoice | B |
| Schwarzwald Circuits (SUP-023) | SGD 6,800 | 2026-10-16 | Net 45 after invoice | C |
| Great Wall Components (SUP-029) | SGD 6,500 | 2026-10-19 | Net 15 after invoice | D |


## 人工演示流程

1. 在新建任务页上传 `requirement/procurement_requirement.txt`，或根据
   `requirement/confirmed_requirement.json` 核对表单；发布后的历史数据应为
   `2026-08-06-v1`。
2. 上传 `quotes/` 下四份 CSV，完成字段审核后运行比较。
3. 分别切换六个主指标，验证确定性结果：
   - 最低总成本：Great Wall Components（SUP-029）。
   - 最快到货：Schwarzwald Circuits（SUP-023）。
   - 最长账期：Redwood Components（SUP-022）。
   - 历史综合等级：Sterling Components（SUP-024）。
   - 历史准时率：Schwarzwald Circuits（SUP-023，11/11）。
   - 历史拒收订单行率：Sterling Components（SUP-024，0/55）。
4. 将主指标设为“最低已确认总成本”，成本容差设为 `400`，次指标设为
   “最快已确认到货”；容差组内应由次指标选出 SUP-023，`ranking_trace.secondary_applied`
   应为 `true`。
5. 打开“供应商信息”页，确认 scope、as-of、数据版本、样本量和“合成演示数据”
   标记均来自同一个冻结快照。

若需整体平移日期，运行：

    PYTHONPATH=src .venv/bin/python data/generate_preference_demo.py --planned-order-date YYYY-MM-DD
