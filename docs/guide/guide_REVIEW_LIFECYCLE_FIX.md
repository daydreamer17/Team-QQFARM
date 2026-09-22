# 人工核验与版本重算修复验收

日期：2026-09-22。适用：当前工作区。所有报价均为合成 MCU 演示数据。

## 修复的根因

1. 当前分析查询混入了同一报价的历史文件版本，导致旧解析结果覆盖新版本。
2. 人工确认、人工修正的恢复依赖当前 graph/revision 或可为空的 artifact.document_id；补运费、应用 Scenario 后可能丢失之前的审核凭据。
3. 编辑现有报价只读取最初提交的草稿，遗漏后来确认的费用。
4. 修改需求没有走已审核解析结果的复用入口。
5. 费用状态编辑器根据旧金额是否存在，禁止选择 KNOWN_AMOUNT，无法一起补充状态和金额。
6. 模型输出非法业务枚举时，解析阶段直接失败，无法进入人工纠正。现在保留候选值并在审核表单提示；非法值仍不能进入计算。
7. 补充信息推进版本时，历史数据绑定的插入顺序可能触发外键错误。

## 现在如何操作

- 已知运费 320 SGD：选择“已有单独费用金额”，填写 320；状态和金额一起提交。集中审核页同样会显示关联金额字段。
- 仍然未知：保留 UNKNOWN，金额留空。人工审核后可提交报价，但不能把未知费用当作 0，也不能因此宣布最终最优。
- 已人工核验的 `Net 60 after invoice` 可进入账期计算。分期预付等复杂条件保留原文、只展示，不强行换算成 Net 天数。
- `Net 60` 缺少起算口径时，只有实际需要账期排序才请求补充；确认后随该报价保存，后续重算沿用。
- 改 Scenario／需求：重算，不重新提取未改变的报价；仍根据新需求检查适用性。
- 编辑现有报价：带入最近人工确认的值，修改后提交新版本。
- 上传新的报价文件：独立提取、独立人工核验，不能继承另一份文件的确认。

文件身份、来源真实性、字段版本、非负金额、状态与金额一致性仍保留必要保护；“人工审核优先”不是允许混用不同文件或把未知当零。

## 自动验收

`tests/backend/test_review_lifecycle.py` 覆盖：

1. 五份报价解析与全字段人工确认；保存人工修改前输出。
2. 未知运费补充为 320。
3. Scenario 改为优先交期并应用。
4. 编辑现有报价，运费改为 325。
5. 修改需求预算。
6. 集中审核将状态和金额一起提交，运费改为 330。
7. 上传独立新版源文件，验证不继承旧人工金额，人工确认 340。
8. 账期起算口径补充；再次修改预算和需求数量，验证确认保留。

每轮检查只含五份当前报价、每份只有一条当前文件执行记录；审核后重算如再次调用解析，测试直接失败。

```bash
.venv/bin/python -m pytest tests/backend/test_review_lifecycle.py -q

# PostgreSQL 独立临时 schema；退出后只删除本测试 schema
RUN_REVIEW_POSTGRES=1 .venv/bin/python -m dotenv -f .env run -- \
  .venv/bin/python -m pytest tests/backend/test_review_lifecycle.py -q

# 付费真实模型：4 份 PDF + 1 份 CSV，另含新版 CSV
RUN_REVIEW_LIVE=1 REVIEW_EVIDENCE_DIR=tmp/review-lifecycle-live \
  .venv/bin/python -m dotenv -f .env run -- \
  env SUPPLIER_MODEL_TIMEOUT_SECONDS=300 SUPPLIER_MODEL_MAX_ATTEMPTS=1 \
  .venv/bin/python -m pytest tests/backend/test_review_lifecycle.py -q
```

真实模型模式使用真实解析；人工动作由测试模拟，对照输入侧同内容 CSV 核验。独立参考答案不传给模型。默认模式是明确标识的固定 CSV 测试，不能冒充真实提取准确率。300 秒仅用于此验收命令，不修改 `.env` 的全局超时。

## 验收边界

### 本次实际结果

- 解析／规则／后端回归：`792 passed, 20 skipped, 3 xfailed`。
- 前端：`76 passed`；`npm run build` 通过（现有 bundle 超 500 kB 提示仍在）。
- 完整固定数据生命周期：通过；PostgreSQL 独立临时 schema 生命周期：通过。
- 真实模型 PDF/CSV 生命周期：通过，229.41 秒。证据见 `tmp/review-lifecycle-live-verified/`；`*-pre-human.json` 是人工修改前候选，`lifecycle.json` 保存各轮结果。
- 真实自然语言“解释／意图 → 确认 Scenario → apply → 重算”专项：通过（`test_live_conversation_preview_confirm_apply_isolated`，真实对话模型＋固定报价夹具，不是上述 PDF 测试的同一个任务）。
- 真实模型首轮发生三次 60 秒传输超时；后续发现非法枚举提前拒绝、Scenario 后人工修正记录恢复不完整，修复后重新运行通过。先前失败不计入通过。
- 人工核验通过测试动作模拟，覆盖真实提取后的纠正；不是“无人工修正提取准确率 100%”。新页面通过组件交互测试，未声称完成浏览器全流程点击验收。
- 曾创建一个独立测试账号的合成任务，现已标记 `ABANDONED` 并保留审计；之后数据库验收使用独立临时 schema，测试结束自动清理，未修改用户已有任务。

此次不自动修写用户历史报价或旧结果。已运行的 API／Worker 需重启加载新代码，再对当前版本重新分析；历史冻结结果保留。

已有 demo4 的三条预期失败仍需另行处理：PDF 组合规格单元格漏读、两个当前 per-100 价格的歧义识别、叙述中的错误溢价关系。这些不能计作本次通过；人工核验仍是必要步骤。
