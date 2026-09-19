# Team-QQFARM

Supplier Comparison 是一个面向电子物料采购的报价解析、人工审核、确定性比较和制度证据检索工作台。系统允许上传采购需求及 PDF/CSV 报价，保留字段来源和版本记录，并将模型理解、人工确认与确定性计算分开。

## 本次改进

### 报价解析与审核

- 修复 CSV 草稿分次校对时丢失先前 `CorrectionEvent` 的问题；多项阻塞字段可以逐项暂存，最终形成一个完整审核批次。
- 为税费、运费、其他费用、交付语义、交期口径等枚举字段增加标准值约束，拒绝把 `N/A`、`NO` 等自由文本直接写入标准化结果。
- 报价草稿改用下拉选项和清晰中文提示，区分“缺失”“无法识别”和“需要人工确认”。
- 保留未知费用语义：未知不按零处理，审核完成前不发布错误的最终推荐。

### 工作流与本地运行

- Compose 新增一次性的 `migrate` 和 `checkpoint-setup` 服务；业务迁移及 LangGraph checkpoint 初始化完成后，API 与常驻 Worker 才会启动。
- 用户无需复制 Job ID 手工执行一次性 Worker；任务由后台持续领取，并在人工确认后从持久化状态继续。
- `full_flow_demo` 补充采购需求、三家 PDF/CSV 报价、制度文件、校对数据、边界测试清单和独立手工验收指南。

### 前端决策工作台

- 简化任务导航，将主要流程收束为“概览、报价与证据、决策结果、制度检查、采购总结、版本记录”。集中审核和调查记录仅在需要处理时出现。
- 重做“入选差距”页面，以供应商名称、合格状态、成本、交期和未入选原因解释推荐结果，不再向业务用户暴露 Task、Quote、Job 等内部 ID。
- 重做“制度检查”页面，逐供应商展示采购要求状态，并明确区分“已找到制度依据”和“供应商已经合规”。当前后端没有逐供应商制度裁决时，界面显示“待供应商级核验”，不会伪造通过结论。
- 将 Summary 调整为中文“采购总结”，移除原始 JSON、哈希、模型参数和内部引用编号；确定性金额与推荐仍以“决策结果”为准。
- 优化长文本、卡片和响应式布局，统一状态、字段、版本及错误提示的中文表达。

## 本地启动

准备好本地 `.env` 后，在仓库根目录执行：

```powershell
docker compose up -d --build postgres api worker
docker compose ps -a
```

`migrate` 和 `checkpoint-setup` 应显示退出码 0。随后启动前端：

```powershell
Set-Location frontend
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1
```

访问：

- 前端：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`

更完整的环境说明见 [本地运行环境](docs/LOCAL_ENVIRONMENT.md)。

## 完整流程演示

演示数据位于 [`data/generated/inputs/development/full_flow_demo`](data/generated/inputs/development/full_flow_demo/README.md)，推荐顺序为：

1. 导入并发布测试制度。
2. 上传采购需求文件并核对自动填入字段。
3. 新建任务时绑定制度分类 `Electronics`、地区 `SG`。
4. 在同一任务中依次提交 A、B、C 三家报价；PDF 主链路与 CSV 替代链路应使用不同任务测试。
5. 启动分析，完成必要的集中审核后检查决策结果、入选差距、制度证据、采购总结和版本记录。

完整步骤和边界测试见 [full_flow_demo 手工验收指南](docs/FULL_FLOW_DEMO_VALIDATION.md)。参考答案必须与运行时 Worker/Agent 隔离。

## 开发检查

```powershell
python -m pytest tests/backend/test_quote_drafts.py tests/backend/test_full_flow_demo_dataset.py -q

Set-Location frontend
npm.cmd run lint
npm.cmd run build
```

本地 Python 需要安装项目完整开发依赖；也可以在 Docker 服务可用时通过项目镜像执行后端测试。任何端到端通过结论都应以真实浏览器、数据库、常驻 Worker 和实际模型调用记录为依据。
