# Extraction fixtures

这里存放仍由自动化测试使用的最小解析夹具，而不是可交付给用户逐步上传的演示数据。

- `canonical-quotes/`：固定 CSV 与基础 PDF 报价。
- `requirements/`：采购需求解析样本。
- `pdf-boundaries/`：空白、损坏、加密、页数限制、来源稳定性等边界样本。
- `pdf-layout/`：多页、重复字段、内部冲突和非规则版式。
- `hybrid-csv/`：已登记与语义表头 CSV。
- `ocr/`：扫描件识别边界样本。
- `full-flow-demo4-layout-holdout/`：Demo4 的版式留出样本；一旦据此调试，应转为开发夹具。
