from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
from .base import BaseSearchEngine, SearchResult

class BingEngine(BaseSearchEngine):
    """Bing 搜索引擎实现"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base_urls = ["https://cn.bing.com", "https://www.bing.com"]
        self.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 6.1; rv:84.0) Gecko/20100101 Firefox/84.0"})
        self.region = self.config.get("region", "zh-CN")
        self.setlang = self.config.get("setlang", "zh")
        self.count = self.config.get("count", 10)

    def _set_selector(self, selector: str) -> str:
        """获取页面元素选择器"""
        selectors = {
            "url": "div.b_attribution cite",
            "title": "h2",
            "text": "div.b_caption p",
            "links": "ol#b_results > li.b_algo",
            "next": 'div#b_content nav[role="navigation"] a.sb_pagN',
        }
        return selectors.get(selector, "")

    async def _get_next_page(self, query: str) -> str:
        """构建并获取搜索页面的HTML内容"""
        base_url = self.base_urls[0]
        params = {
            "q": query,
            "setlang": self.setlang,
            "count": str(min(self.count, 50)),
        }
        if self.region:
            params["cc"] = self.region.split("-")[0] if "-" in self.region else self.region
        
        query_string = urlencode(params)
        search_url = f"{base_url}/search?{query_string}"
        return await self._get_html(search_url)