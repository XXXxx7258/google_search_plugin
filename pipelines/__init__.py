"""google_search_plugin 业务流水线模块。

把老 plugin.py 的业务逻辑拆分到这里,plugin.py 只保留壳。

模块职责:
- prompts:        rewrite / summarize / url_summarize prompt 模板
- llm_runner:     ctx.llm.generate 包装,显式传 model 避免 Bug C
- engine_chain:   多引擎 fallback 链
- content_fetcher: 网页正文抓取(trafilatura/readability/bs4 三级降级)
- zhihu_extractor: 知乎专用抓取与 initialState 解析
- url_pipeline:   URL 直访总结流程
- search_pipeline: 主搜索流程(rewrite + 引擎 + 抓取 + 总结)
- image_search_pipeline: 图片搜索 + 30 分钟去重 + base64

注:工具调用结果由 host 的 maisaka.reasoning_engine 自动写入 ``tool_records`` 表,
插件不再自己写 ChatHistory(老 v3.x HistoryWriter 已删除)。
"""
