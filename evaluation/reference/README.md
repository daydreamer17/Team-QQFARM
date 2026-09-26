# Current evaluation reference sets

本目录只保存当前交付版本使用的独立参考答案，不是运行时输入目录。部署、模型提示、RAG 和 Agent 工具不得读取这里的内容。

当前保留：

- `full_flow_demo4/`：完整流程、报价解析和决策对话的参考结果；
- `agent_investigation_demo/`：调查 Agent 的离线测试案例；
- `policy_rag/`：制度检索问题集；
- `chatbot_agent_questions.json`：AI 决策助手问题集；
- `live_model_benchmark_cases.json`：付费真实模型评测使用的 16 个固定英文场景（14 个调查、2 个试算），不包含参考答案或运行时秘密。

对应的可上传演示数据位于 `data/generated/demos/`，通用解析和合规回归夹具位于 `data/generated/fixtures/`。参考答案与运行输入必须继续隔离，不能为了提高结果而注入运行时上下文。

Demo4 的离线评测入口：

```bash
PYTHONPATH=src .venv/bin/python scripts/evaluate_full_flow_demo4.py
```

旧版 Demo1–3、Preference Demo 和 Quote V2–V9 的参考集及专用评分脚本已随交付清理退役，不再属于当前测试基线。
