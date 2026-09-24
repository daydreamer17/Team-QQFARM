# 制度检查合成证明材料

本目录全部为虚构演示材料，不代表真实供应商、认证或采购审批。三个子目录分别用于验证供应商准入、RoHS 和金额审批；每类均有 `v1` 异常版与 `v2` 更正版。

建议使用 `data/generated/inputs/development/full_flow_demo4/quotes/csv/` 的四份报价，任务制造商为 `QQ Demo Components`、料号为 `QW-MCU9-DEMO`。在制度检查页选择对应供应商和检查项，上传文件后按原文填写字段并勾选人工核对。

| 供应商 | 编号 | v1 重点异常 | v2 预期 |
|---|---|---|---|
| Redwood Components | SUP-022 | 准入过期、审批金额不足 | 更新后有效 |
| Schwarzwald Circuits | SUP-023 | RoHS 错料号、审批错币种 | 更正后有效 |
| Sterling Components | SUP-024 | 准入暂停、RoHS 过期 | 恢复/续期后有效 |
| Great Wall Components | SUP-029 | 准入编号不符、RoHS 不符合、审批拒绝 | 更正后有效 |

验证版本替换时，先上传 `v1`，再点击“替换材料/审批记录”上传同一供应商的 `v2`。若把 v1、v2 都作为新材料保存而不建立替换关系，系统应提示冲突而不是按“最新上传”自动通过。

## 成对验证材料

`paired-scenarios/` 是更直观的前端验收包。每家供应商都有以下 6 份文件：

- `supplier-compliant.md` / `supplier-non-compliant-*.md`
- `rohs-compliant.md` / `rohs-non-compliant-*.md`
- `amount-compliant.md` / `amount-non-compliant-*.md`

文件名明确标注预期，上传后系统应自动回填材料编号、供应商编号、结论、有效期，以及 RoHS 物料范围或审批金额。无需上传 `manifest.json`。异常版分别覆盖过期、拒绝/暂停、供应商或料号不匹配、金额不足及币种不匹配等场景。
