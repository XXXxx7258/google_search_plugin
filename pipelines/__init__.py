"""google_search_plugin 业务流水线模块。

阶段 3 重构:把老 plugin.py 的业务逻辑拆分到这里,plugin.py 只保留壳。

模块职责:
- prompts:        rewrite / summarize / url_summarize prompt 模板
- result_formatter: SearchResult 列表的格式化与序列化
- llm_runner:     ctx.llm.generate 包装,显式传 model 避免 Bug C
- engine_chain:   多引擎 fallback 链
- content_fetcher: 网页正文抓取(trafilatura/readability/bs4 三级降级)
- zhihu_extractor: 知乎专用抓取与 initialState 解析
- history_writer: ctx.db.query 写 ChatHistory + 去重 workaround
- url_pipeline:   URL 直访总结流程
- search_pipeline: 主搜索流程(rewrite + 引擎 + 抓取 + 总结)
"""
