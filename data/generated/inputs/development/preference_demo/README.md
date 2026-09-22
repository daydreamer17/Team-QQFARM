# preference_demo

用于验证 V2 六项排序指标会真正改变推荐结果。全部供应商、报价和物料均为合成数据，历史指标来自固定的合成历史快照。

- 场景：`PREFERENCE-SWITCHBOARD-002`
- 计划下单：`2026-11-02`
- 截止到货：`2026-11-14`
- 历史数据：`synthetic-mcu9-supplier-performance / 2026-08-06-v1`

| 供应商 | 总成本 | 到货 | 付款 | 历史等级 | 历史准时率 | 历史拒收行率 |
|---|---:|---|---|---|---:|---:|
| Lotus Components (SUP-032) | SGD 20,160 | 2026-11-11 | Net 30 after invoice | B | 127/146 (86.99%) | 6/146 (4.11%) |
| Busan Components (SUP-033) | SGD 20,520 | 2026-11-06 | Net 15 after invoice | B | 73/83 (87.95%) | 1/83 (1.20%) |
| Summit Components (SUP-028) | SGD 21,000 | 2026-11-09 | Net 90 after invoice | A | 209/230 (90.87%) | 2/230 (0.87%) |
| Schwarzwald Circuits (SUP-023) | SGD 20,688 | 2026-11-08 | Net 45 after invoice | C | 11/11 (100.00%) | 1/11 (9.09%) |
| Golden Dragon Components (SUP-036) | SGD 20,352 | 2026-11-10 | Net 60 after invoice | B | 100/120 (83.33%) | 0/120 (0.00%) |


## 人工演示流程

1. 目标分支没有全局历史数据时，启动 API/Worker 前把 `SUPPLIER_HISTORY_ROOT` 设为
   `data/generated/inputs/development/preference_demo/supplier_history`。本目录已包含完整、可校验的
   `2026-08-06-v1` 快照。
2. 在新建任务页上传 `requirement/procurement_requirement.txt`，或根据
   `requirement/confirmed_requirement.json` 核对表单；发布后的历史数据应为
   `2026-08-06-v1`。
3. 上传 `quotes/` 下五份 CSV，完成字段审核后运行比较。
4. 分别切换六个主指标，验证确定性结果：
   - 最低总成本：Lotus Components（SUP-032）。
   - 最快到货：Busan Components（SUP-033）。
   - 最长账期：Summit Components（SUP-028）。
   - 历史综合等级：Summit Components（SUP-028，唯一 A 级）。
   - 历史准时率：Schwarzwald Circuits（SUP-023，11/11）。
   - 历史拒收订单行率：Golden Dragon Components（SUP-036，0/120）。
5. 将主指标设为“最低已确认总成本”，成本容差设为 `600`，次指标设为
   “最快已确认到货”；容差组包含 SUP-032、SUP-036、SUP-033、SUP-023，
   应由次指标选出 SUP-033，`ranking_trace.secondary_applied`
   应为 `true`。
6. 打开“供应商信息”页，确认 scope、as-of、数据版本、样本量和“合成演示数据”
   标记均来自同一个冻结快照。

若需整体平移日期，运行：

    PYTHONPATH=src .venv/bin/python data/generate_preference_demo.py --planned-order-date YYYY-MM-DD
