你之前看到的确实大多是“产品介绍页”。这类企业软件通常把完整演示藏在 iframe、客户登录或留资表单后。我重新筛了一遍，下面这些更接近真实产品界面和操作过程。

### 建议优先看这 6 个

1. **SAP Ariba：目前最完整的 Agent 流程演示**

   [Working with Joule Agents in Next-Gen SAP Ariba Sourcing](https://learning.sap.com/courses/introducing-next-gen-sap-ariba-sourcing/working-with-sourcing-agents-in-next-gen-sap-ariba-sourcing_ef257d90-adb9-49c8-8477-592125de11e5)

   可以看到：

   - 用户通过对话创建寻源项目
   - Sourcing Event Agent 自动生成事件
   - Bid Analysis Agent 分析供应商报价
   - Negotiation Agent 生成还价策略
   - 输出供应商推荐、权衡理由和授标建议

   这是最接近我们要做的“从询价到评审”的完整 Agent 产品形态。注意：SAP 页面注明部分 Sourcing Assistant 能力计划于 **2026 年第三季度 GA**。

2. **Fairmarkit：最接近我们“报价文件分析”场景**

   [直接播放 AI Bid Analysis 视频](https://vimeo.com/1104488244)

   演示重点是把不同供应商的报价文件交给 Agent，用户通过对话询问报价差异、条款和建议。尤其值得参考“左侧业务对象＋右侧 Agent 对话＋中间结构化结果”的组合。

3. **Keelvar：寻源平台完整产品 Tour**

   [直接播放 Keelvar Intelligent Sourcing Platform](https://vimeo.com/986364381/18f75ada60)

   可以重点观察：

   - 寻源项目列表与工作台
   - 事件配置与供应商邀请
   - 方案和情景分析
   - 约束条件、评分、授标结果的表达方式

4. **Ivalua IVA：新一代动态 Agent UI**

   [直接打开 IVA 官方嵌入视频](https://player.vimeo.com/video/1216469200?app_id=122963&dnt=1&h=aa036868c0)

   [带说明和界面图片的 IVA 产品页](https://www.ivalua.com/technology/agentic-ai/)

   它不只是聊天框，而是在对话中动态生成：

   - 表单
   - 表格
   - 数据摘要
   - 图表
   - 可确认的执行动作

   这一点非常值得前端参考：Agent 的回答不应只有长文本，而应输出可操作的业务组件。

5. **Zip：AI 助手真实演示**

   [直接打开 Zip AI 演示播放器](https://fast.wistia.net/embed/iframe/d6indwg14e)

   [包含演示和截图的官方文章](https://zip.com/blog/zip-ai-procurement-intelligence-that-works-for-you)

   可以看到 AI 助手如何：

   - 查询采购请求、合同和审批状态
   - 回答公司采购政策
   - 从对话直接发起采购请求
   - 自动将需求路由到适合的工作流

6. **Coupa Navi：最清楚的聊天助手交互说明**

   [Coupa Navi 官方图文操作说明](https://compass.coupa.com/en-us/products/product-documentation/supplier-resources/for-suppliers/core-supplier-onboarding/announcements-and-general-info/answer-csp-related-questions-with-csp-supplier-assistance-agent)

   这里有真实界面截图和完整操作步骤，包括：

   - 右下角悬浮 Agent 入口
   - 可移动、最小化的对话窗口
   - 回答中的资料来源
   - 点赞/点踩反馈
   - 将对话导出为 PDF
   - 转人工客服入口

   另有一个公开视频页面：[Navi for Supply Chain Demo](https://engage.coupa.com/tfest25/items/2025-navi-for-supply-chain-demo-video--1mp4-231d)。

### 各产品的补充真实界面资料

| 产品 | 可直接看的资料 | 适合研究的内容 |
|---|---|---|
| Fairmarkit | [全部 On-demand Demo](https://www.fairmarkit.com/demo-resource) · [供应商报价评审界面](https://docs.fairmarkit.com/buyers/events/review-supplier-responses) | 报价对比、事件详情、授标入口、Agent 分析 |
| Keelvar | [供应商端视频 Tour](https://support.keelvar.com/hc/en-us/articles/360019286540-Sourcing-Optimizer-tour) · [在线提交报价](https://support.keelvar.com/hc/en-us/articles/360016316800-Submitting-your-bids-online) · [RFI 填写界面](https://support.keelvar.com/hc/en-us/articles/360016314780-Answering-the-RFI-online) | 供应商填报、报价状态、买卖双方协作 |
| SAP Ariba | [Joule 与寻源协作](https://learning.sap.com/courses/introducing-next-gen-sap-ariba-sourcing/collaborating-with-joule-in-next-gen-sap-ariba-sourcing_a91ea93b-d1af-4df0-aa3d-c8509aed441b) · [Joule 供应商管理短视频](https://learning.sap.com/videos/using-joule-in-sap-ariba-supplier-management) | 对话创建 RFP、查看投标、提醒供应商、审批 |
| Zip | [Zip Sourcing 产品 Walkthrough](https://zip.com/walkthroughs/zip-sourcing) · [带实际界面的产品 PDF](https://lp.ziphq.com/rs/195-QFD-187/images/eBook___Zip_spend_control_made_easy.pdf?version=0) | Intake、RFx、供应商响应、AI 生成问题 |
| Ivalua | [Agentic AI 界面白皮书](https://info.ivalua.com/hubfs/Agentic%20AI%20in%20Procurement.pdf) | IVA 首页入口、对话查供应商、动态业务组件 |
| GEP | [Direct Sourcing Agent 页面](https://www.gep.com/software/gep-quantum-intelligence/supply-chain/direct-sourcing) · [采购 Agent 编排页面](https://www.gep.com/software/gep-quantum-intelligence/procurement) | Prompt 创建 RFP、多 Agent 状态、跨模块编排 |

### 需要留资的完整 Demo

这些内容确实是完整演示，但通常需要填写邮箱：

- [Zip Sourcing Demo Walkthrough](https://zip.com/walkthroughs/zip-sourcing)
- [Ivalua IVA On-demand Demo](https://info.ivalua.com/demo/iva)
- [Coupa Procurement On-demand Demo](https://get.coupa.com/On-Demand-Demo_Procurement-Invoicing-Payments.html)

GEP 当前公开内容主要是带动效的产品界面和流程卡片，我暂时没有找到经过验证、无需留资的完整 Agent 操作录像。

综合来看，我们的前端最值得借鉴的是：**SAP 的完整 Agent 工作流、Fairmarkit 的报价文件分析、Ivalua 的动态组件式回答、Coupa 的悬浮助手与引用反馈机制、Keelvar 的授标情景分析**。这些组合起来，比单纯模仿某一个竞品更适合当前项目。