import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
from .base import BaseSearchEngine, SearchResult
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

class BingEngine(BaseSearchEngine):
    """Bing 搜索引擎实现"""
    
    base_urls: List[str]
    region: str
    setlang: str
    count: int

    SELECTOR_CONFIG: Dict[str, Dict[str, Any]] = {
        "url": {
            "primary": "h2 > a",
            "fallback": [
                "h2 a",
                "h3 > a",
                ".b_algo h2 a",
                ".b_algo a[href]",
            ],
        },
        "title": {
            "primary": "h2 > a",
            "fallback": [
                "h2 a",
                "h3 > a",
                ".b_algo h2 a",
                ".b_algo a[href]",
            ],
        },
        "text": {
            "primary": ".b_caption > p",
            "fallback": [
                ".b_caption",
                ".b_descript",
                ".b_snippet",
                ".b_algo .b_caption",
            ],
        },
        "links": {
            "primary": "ol#b_results > li.b_algo",
            "fallback": [
                "#b_results > li.b_algo",
                "#b_results li.b_algo",
                ".b_algo",
                "li.b_algo",
            ],
        },
        "next": {
            "primary": 'div#b_content nav[role="navigation"] a.sb_pagN',
            "fallback": [
                'nav[role="navigation"] a.sb_pagN',
                'a.sb_pagN',
                '.sb_pagN',
            ],
        },
    }
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.base_urls = ["https://cn.bing.com", "https://www.bing.com"]
        self.region = self.config.get("region", "zh-CN")
        self.setlang = self.config.get("setlang", "zh")
        self.count = self.config.get("count", 10)

    def _set_selector(self, selector: str) -> str:
        """获取页面元素选择器

        Args:
            selector: 选择器名称

        Returns:
            CSS选择器字符串
        """
        config = self.SELECTOR_CONFIG.get(selector, {})
        return config.get("primary", "")

    def _get_fallback_selectors(self, selector: str) -> list:
        """获取备用选择器列表

        Args:
            selector: 选择器名称

        Returns:
            备用选择器列表
        """
        config = self.SELECTOR_CONFIG.get(selector, {})
        return config.get("fallback", [])

    async def _get_next_page(self, query: str) -> str:
        """构建并获取搜索页面的HTML内容

        Args:
            query: 搜索查询

        Returns:
            HTML内容
        """
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

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """执行搜索，使用增强的选择器回退机制

        Args:
            query: 搜索查询
            num_results: 期望的结果数量

        Returns:
            搜索结果列表
        """
        try:
            resp = await self._get_next_page(query)
            soup = BeautifulSoup(resp, "html.parser")

            # 使用主选择器查找结果
            links_selector = self._set_selector("links")
            links = soup.select(links_selector) if links_selector else []

            # 如果主选择器失效，尝试备用选择器
            if not links:
                logger.warning(f"Primary links selector '{links_selector}' found no results, trying fallbacks")
                for fallback_selector in self._get_fallback_selectors("links"):
                    links = soup.select(fallback_selector)
                    if links:
                        logger.info(f"Fallback selector '{fallback_selector}' found {len(links)} results")
                        break

            if not links:
                logger.error(f"No results found with any selector for query '{query}'")
                return []

            logger.info(f"Found {len(links)} link elements")

            results = []
            title_selector = self._set_selector("title")
            url_selector = self._set_selector("url")
            text_selector = self._set_selector("text")

            for idx, link in enumerate(links):
                # 处理标题，使用备用选择器
                title_elem = link.select_one(title_selector) if title_selector else None
                if not title_elem:
                    for fallback in self._get_fallback_selectors("title"):
                        title_elem = link.select_one(fallback)
                        if title_elem:
                            break
                title = self.tidy_text(title_elem.text) if title_elem else ""

                # 处理URL，使用备用选择器
                url_elem = link.select_one(url_selector) if url_selector else None
                if not url_elem:
                    for fallback in self._get_fallback_selectors("url"):
                        url_elem = link.select_one(fallback)
                        if url_elem:
                            break
                url_raw = url_elem.get("href") if url_elem else ""
                url = self._normalize_url(url_raw)

                # 处理摘要，使用备用选择器
                snippet = ""
                if text_selector:
                    snippet_elem = link.select_one(text_selector)
                    if not snippet_elem:
                        for fallback in self._get_fallback_selectors("text"):
                            snippet_elem = link.select_one(fallback)
                            if snippet_elem:
                                break
                    snippet = self.tidy_text(snippet_elem.text) if snippet_elem else ""

                # 只有当标题和URL都有效时才添加结果
                if title and url:
                    results.append(SearchResult(title=title, url=url, snippet=snippet, abstract=snippet, rank=idx))

            logger.info(f"Returning {len(results[:num_results])} search results for query '{query}'")
            return results[:num_results]
        except Exception as e:
            logger.error(f"Error in Bing search for query {query}: {e}", exc_info=True)
            return []
