import re
import random
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from .base import BaseSearchEngine, SearchResult

class SogouEngine(BaseSearchEngine):
    """搜狗搜索引擎实现"""
    
    base_urls: List[str]
    s_from: str
    sst_type: str
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.base_urls = ["https://www.sogou.com", "https://m.sogou.com"]
        self.headers.update({
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        self.s_from = self.config.get("s_from", "input")
        self.sst_type = self.config.get("sst_type", "normal")

    def _set_selector(self, selector: str) -> str:
        selectors = {
            "url": "h3 > a",
            "title": "h3",
            "text": "div.fz-mid.p, .txt-box p",
            "links": "div.results div.vrwrap, div.results div.rb",
            "next": "",
        }
        return selectors.get(selector, "")

    async def _get_next_page(self, query: str) -> str:
        params = {
            "query": query,
            "ie": "utf8",
            "from": self.s_from,
            "sst_type": self.sst_type,
        }
        url = f"{self.base_urls[0]}/web?{urlencode(params)}"
        return await self._get_html(url)
    
    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        results = await super().search(query, num_results)
        for result in results:
            if result.url.startswith("/link?"):
                result.url = self.base_urls[0] + result.url
                result.url = await self._parse_sogou_redirect(result.url)
        return results
    
    async def _parse_sogou_redirect(self, url: str) -> str:
        """解析搜狗重定向URL
        
        Args:
            url: 重定向URL
            
        Returns:
            真实URL
        """
        html = await self._get_html(url)
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script")
        if script:
            script_text = script.get_text()
            match = re.search(r'window.location.replace\("(.+?)"\)', script_text)
            if match:
                return match.group(1)
        return url
