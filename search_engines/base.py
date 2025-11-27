import random
import warnings
import logging
import re
import base64
from bs4 import BeautifulSoup, MarkupResemblesLocatorWarning
import aiohttp
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import urllib.parse
from urllib.parse import urlparse, urljoin, parse_qs

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
        """清理文本，包含Unicode字符规范化

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
        if not text:
            return ""

        # 规范化Unicode字符，将各种空格字符统一为标准空格
        import unicodedata

        # 将Unicode文本规范化为NFC形式
        text = unicodedata.normalize('NFC', text)

        # 替换各种Unicode空格字符为标准空格
        unicode_spaces = [
            '\u00A0',  # 不间断空格
            '\u2002',  # en space
            '\u2003',  # em space
            '\u2009',  # thin space
            '\u200A',  # hair space
            '\u200B',  # 零宽空格
            '\u2060',  # 字符连接符
            '\u3000',  # 全角空格
        ]

        for unicode_space in unicode_spaces:
            text = text.replace(unicode_space, ' ')

        # 常规文本清理
        text = text.strip().replace("\n", " ").replace("\r", " ")

        # 合并多个空格为单个空格
        while "  " in text:
            text = text.replace("  ", " ")

        return text

    def _is_valid_url(self, url: str) -> bool:
        """验证URL是否有效

        Args:
            url: 待验证的URL字符串

        Returns:
            URL是否有效
        """
        if not url or not isinstance(url, str):
            return False

        try:
            parsed = urlparse(url)
            # 检查协议是否为http或https
            if parsed.scheme not in ['http', 'https']:
                return False
            # 检查是否有域名
            if not parsed.netloc:
                return False
            # 过滤掉javascript等非网页链接
            if url.lower().startswith(('javascript:', 'mailto:', 'tel:', 'ftp:')):
                return False
            return True
        except Exception:
            return False

    def _normalize_url(self, url_raw: str, base_url: str = "") -> str:
        """规范化URL处理

        Args:
            url_raw: 原始URL字符串
            base_url: 基础URL，用于相对路径转换

        Returns:
            规范化后的URL字符串
        """
        if not url_raw:
            return ""

        try:
            url = urllib.parse.unquote(str(url_raw))

            # 处理 Bing 跳转链接 /ck/a?...&u=target
            try:
                parsed_raw = urlparse(url)
                if parsed_raw.netloc.endswith("bing.com") and parsed_raw.path.startswith("/ck/a"):
                    query_params = parse_qs(parsed_raw.query)
                    target = query_params.get("u", [])
                    if target:
                        raw_target = urllib.parse.unquote(target[0])
                        if raw_target.startswith("a1"):
                            try:
                                decoded_bytes = base64.b64decode(raw_target[2:] + "===")
                                decoded_text = decoded_bytes.decode("utf-8", errors="ignore")
                                match = re.search(r"https?://[^\s\"'>]+", decoded_text)
                                raw_target = match.group(0) if match else decoded_text
                            except Exception:
                                pass
                        url = raw_target
            except Exception:
                pass

            if base_url and not url.startswith(('http://', 'https://')):
                url = urljoin(base_url, url)

            if self._is_valid_url(url):
                return url
            else:
                return ""
        except Exception:
            return ""

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
                # 处理标题 - 不进行URL解码，只进行文本清理
                title_elem = link.select_one(title_selector)
                title = self.tidy_text(title_elem.text) if title_elem else ""

                # 处理URL - 使用新的规范化方法
                url_elem = link.select_one(url_selector)
                url_raw = url_elem.get("href") if url_elem else ""
                url = self._normalize_url(url_raw)

                # 处理摘要 - 不进行URL解码，只进行文本清理
                snippet = ""
                if text_selector:
                    snippet_elem = link.select_one(text_selector)
                    snippet = self.tidy_text(snippet_elem.text) if snippet_elem else ""

                # 只有当标题和URL都有效时才添加结果
                if title and url:
                    results.append(SearchResult(title=title, url=url, snippet=snippet, abstract=snippet, rank=idx))

            logger.info(f"Returning {len(results[:num_results])} search results for query '{query}'")
            return results[:num_results]
        except Exception as e:
            logger.error(f"Error in search for query {query}: {e}", exc_info=True)
            return []

