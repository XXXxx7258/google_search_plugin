import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
from .base import BaseSearchEngine, SearchResult

logger = logging.getLogger(__name__)

class BingEngine(BaseSearchEngine):
    """Bing 搜索引擎实现"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base_urls = ["https://cn.bing.com", "https://www.bing.com"]
        self.region = self.config.get("region", "zh-CN")
        self.setlang = self.config.get("setlang", "zh")
        self.count = self.config.get("count", 10)

    def _set_selector(self, selector: str) -> str:
        """获取页面元素选择器"""
        selectors = {
            "url": "h2 > a",
            "title": "h2 > a",
            "text": ".b_caption > p",
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
        logger.info(f"Requesting Bing search URL: {search_url}")
        return await self._get_html(search_url)
