import logging
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode, urlparse
from bs4 import BeautifulSoup

from .base import BaseSearchEngine, SearchResult

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
    # 黑名单留空，按需添加
    BLOCKED_DOMAINS: List[str] = []

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.base_urls = ["https://cn.bing.com", "https://www.bing.com"]
        self.region = self.config.get("region", "zh-CN")
        self.setlang = self.config.get("setlang", "zh")
        self.count = self.config.get("count", 10)

    def _build_keywords(self, query: str) -> List[str]:
        """构建用于相关性过滤的关键词列表，兼容中英文。"""
        if not query:
            return []
        pieces: List[str] = []
        for token in re.split(r"\s+", query.lower().strip()):
            if not token:
                continue
            words = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", token)
            if words:
                pieces.extend(words)
            else:
                pieces.append(token)
        return [p for p in pieces if len(p) >= 2]

    def _is_relevant(self, title: str, snippet: str, url: str, keywords: List[str]) -> bool:
        """粗粒度过滤：标题/摘要/URL 命中至少一个关键词即通过。"""
        if not keywords:
            return True
        text = f"{title} {snippet} {url}".lower()
        match_count = sum(1 for kw in keywords if kw in text)
        return match_count >= 1

    def _is_blocked(self, url: str) -> bool:
        """域名黑名单过滤。"""
        if not url:
            return False
        try:
            netloc = urlparse(url).netloc.lower()
        except Exception:
            return False
        return any(netloc.endswith(domain) for domain in self.BLOCKED_DOMAINS)

    def _set_selector(self, selector: str) -> str:
        """获取页面元素选择器。"""
        config = self.SELECTOR_CONFIG.get(selector, {})
        return config.get("primary", "")

    def _get_fallback_selectors(self, selector: str) -> list:
        """获取备用选择器列表。"""
        config = self.SELECTOR_CONFIG.get(selector, {})
        return config.get("fallback", [])

    async def _get_next_page(
        self,
        query: str,
        *,
        base_url: Optional[str] = None,
        region: Optional[str] = None,
        setlang: Optional[str] = None,
        market: Optional[str] = None,
    ) -> str:
        """构建并获取搜索页面的 HTML 内容。

        Args:
            query: 搜索查询词。
            base_url: 基础域名，默认先用 cn，再回退 www。
            region: 区域代码（cc），影响市场。
            setlang: 语言参数。
            market: 市场参数（mkt），可用于强制 en-US 等。

        Returns:
            HTML 文本。
        """
        base_url = base_url or self.base_urls[0]
        region = region or self.region
        setlang = setlang or self.setlang
        params = {
            "q": query,
            "setlang": setlang,
            "count": str(min(self.count, 50)),
            "ensearch": "1",
        }
        if region:
            params["cc"] = region.split("-")[0] if "-" in region else region
        if market:
            params["mkt"] = market

        query_string = urlencode(params)
        search_url = f"{base_url}/search?{query_string}"
        logger.info(f"Requesting Bing search URL: {search_url}")
        return await self._get_html(search_url)

    def _get_link_elements(self, soup: BeautifulSoup) -> List[Any]:
        """获取搜索结果节点，包含主选择器和回退。"""
        links_selector = self._set_selector("links")
        if links_selector:
            links = soup.select(links_selector)
            if links:
                return links
        for fallback_selector in self._get_fallback_selectors("links"):
            links = soup.select(fallback_selector)
            if links:
                logger.info(f"Fallback selector '{fallback_selector}' found {len(links)} results")
                return links
        return []

    def _select_with_fallback(self, element: Any, selector_name: str) -> Optional[Any]:
        """在元素上按主/备用选择器查找单个子元素。"""
        primary = self._set_selector(selector_name)
        if primary:
            found = element.select_one(primary)
            if found:
                return found
        for fallback in self._get_fallback_selectors(selector_name):
            found = element.select_one(fallback)
            if found:
                return found
        return None

    def _parse_page_results(self, soup: BeautifulSoup, keywords: List[str]) -> List[SearchResult]:
        """解析页面并生成过滤后的 SearchResult 列表。"""
        links = self._get_link_elements(soup)
        if not links:
            return []

        results: List[SearchResult] = []
        for idx, link in enumerate(links):
            title_elem = self._select_with_fallback(link, "title")
            url_elem = self._select_with_fallback(link, "url")
            text_elem = self._select_with_fallback(link, "text")

            title = self.tidy_text(title_elem.text) if title_elem else ""
            url_raw = url_elem.get("href") if url_elem else ""
            url = self._normalize_url(url_raw)
            snippet = self.tidy_text(text_elem.text) if text_elem else ""

            if title and url and not self._is_blocked(url) and self._is_relevant(title, snippet, url, keywords):
                results.append(SearchResult(title=title, url=url, snippet=snippet, abstract=snippet, rank=idx))
        return results

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """执行搜索，使用多区域尝试与选择器回退。"""
        try:
            keywords = self._build_keywords(query)
            fetch_variants = [
                {
                    "base_url": self.base_urls[0],
                    "region": self.region,
                    "setlang": self.setlang,
                    "market": self.region,
                },
                {
                    "base_url": self.base_urls[1] if len(self.base_urls) > 1 else self.base_urls[0],
                    "region": "en-US",
                    "setlang": "en",
                    "market": "en-US",
                },
            ]

            results: List[SearchResult] = []
            for variant in fetch_variants:
                resp = await self._get_next_page(query, **variant)
                soup = BeautifulSoup(resp, "html.parser")
                page_results = self._parse_page_results(soup, keywords)
                if page_results:
                    results.extend(page_results)
                    break

            if not results:
                logger.warning(f"No relevant results remain after filtering for query '{query}'")

            logger.info(f"Returning {len(results[:num_results])} search results for query '{query}'")
            return results[:num_results]
        except Exception as e:
            logger.error(f"Error in Bing search for query {query}: {e}", exc_info=True)
            return []
