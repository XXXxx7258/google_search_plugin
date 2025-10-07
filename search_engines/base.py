import random
import warnings
import logging
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
import aiohttp
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import urllib.parse

warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36",
    "Accept": "*/*",
    "Connection": "keep-alive",
    "Accept-Language": "en-GB,en;q=0.5",
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.131 Safari/537.36",
]

@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    abstract: str = ""
    rank: int = 0
    content: str = ""

class BaseSearchEngine:
    """搜索引擎基类"""
    
    config: Dict[str, Any]
    TIMEOUT: int
    max_results: int
    headers: Dict[str, str]
    proxy: Optional[str]
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.TIMEOUT = self.config.get("timeout", 10)
        self.max_results = self.config.get("max_results", 10)
        self.headers = HEADERS.copy()
        self.proxy = self.config.get("proxy")

    def _set_selector(self, selector: str) -> str:
        """获取页面元素选择器
        
        Args:
            selector: 选择器名称
            
        Returns:
            CSS选择器字符串
        """
        raise NotImplementedError()

    async def _get_next_page(self, query: str) -> str:
        """获取搜索页面HTML
        
        Args:
            query: 搜索查询
            
        Returns:
            HTML内容
        """
        raise NotImplementedError()

    async def _get_html(self, url: str, data: Optional[Dict[str, Any]] = None) -> str:
        """获取HTML内容
        
        Args:
            url: 目标URL
            data: POST数据（可选）
            
        Returns:
            HTML字符串
        """
        headers = self.headers
        headers["Referer"] = url
        headers["User-Agent"] = random.choice(USER_AGENTS)
        async with aiohttp.ClientSession() as session:
            if data:
                async with session.post(
                    url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=self.TIMEOUT), proxy=self.proxy
                ) as resp:
                    resp.raise_for_status()
                    return await resp.text()
            else:
                async with session.get(
                    url, headers=headers, timeout=aiohttp.ClientTimeout(total=self.TIMEOUT), proxy=self.proxy
                ) as resp:
                    resp.raise_for_status()
                    return await resp.text()

    def tidy_text(self, text: str) -> str:
        """清理文本
        
        Args:
            text: 原始文本
            
        Returns:
            清理后的文本
        """
        return text.strip().replace("\n", " ").replace("\r", " ").replace("  ", " ")

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """执行搜索
        
        Args:
            query: 搜索查询
            num_results: 期望的结果数量
            
        Returns:
            搜索结果列表
        """
        try:
            resp = await self._get_next_page(query)
            soup = BeautifulSoup(resp, "html.parser")

            links_selector = self._set_selector("links")
            if not links_selector:
                return []
            links = soup.select(links_selector)
            logger.info(f"Found {len(links)} link elements using selector '{links_selector}'")

            results = []
            title_selector = self._set_selector("title")
            url_selector = self._set_selector("url")
            text_selector = self._set_selector("text")

            for idx, link in enumerate(links):
                title_elem = link.select_one(title_selector)
                title = self.tidy_text(urllib.parse.unquote(title_elem.text)) if title_elem else ""

                url_elem = link.select_one(url_selector)
                url_raw = url_elem.get("href") if url_elem else ""
                url = urllib.parse.unquote(str(url_raw)) if url_raw else ""

                snippet = ""
                if text_selector:
                    snippet_elem = link.select_one(text_selector)
                    snippet = self.tidy_text(urllib.parse.unquote(snippet_elem.text)) if snippet_elem else ""

                if title and url:
                    results.append(SearchResult(title=title, url=str(url), snippet=snippet, abstract=snippet, rank=idx))

            logger.info(f"Returning {len(results[:num_results])} search results for query '{query}'")
            return results[:num_results]
        except Exception as e:
            logger.error(f"Error in search for query {query}: {e}", exc_info=True)
            return []
