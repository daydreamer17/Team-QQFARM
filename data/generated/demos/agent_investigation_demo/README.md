# Agent 调查验收场景

本目录不复制报价二进制文件，而是以带哈希的 manifest 复用 `full_flow_demo4` 的合成输入。运行时 manifest 只描述场景和输入；预期工具路线保存在 `evaluation/reference/agent_investigation_demo/cases.json`，不得提供给 Agent。

报价型场景按 manifest 的 `requirement`、`base_quotes` 和 `variant_quote` 分别新建任务；用户请求型场景先用四份主报价完成比较，再从“智能调查”页面选择目标。制度故障和输入失效属于受控状态注入，不应伪造成制度正文或报价内容。
