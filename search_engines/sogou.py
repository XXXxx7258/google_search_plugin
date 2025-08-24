"""
搜狗搜索引擎实现
"""

import asyncio
import random
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseSearchEngine, SearchResult


class SogouEngine(BaseSearchEngine):
    """搜狗搜索引擎实现"""
    
    def _setup_defaults(self):
        """设置默认配置"""
        self.base_urls = ["https://www.sogou.com", "https://m.sogou.com"]
        self.headers.update({
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        
        # 搜狗特定配置
        self.s_from = self.config.get("s_from", "input")  # 搜索来源
        self.sst_type = self.config.get("sst_type", "normal")  # 搜索类型
    
    def get_selectors(self, selector_type: str) -> str:
        """获取页面元素选择器
        
        Args:
            selector_type: 选择器类型
            
        Returns:
            CSS 选择器字符串
        """
        selectors = {
            "url": "div.fz-mid .citeUrl, div.txt-box cite",
            "title": "h3 a, .vr-title a",
            "text": "div.fz-mid p, .txt-box p",
            "links": ".results .rb, .vrwrap",
            "next": "#pagebar_container .np",
        }
        return selectors.get(selector_type, "")
    
    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """执行搜狗搜索
        
        Args:
            query: 搜索查询
            num_results: 结果数量
            
        Returns:
            搜索结果列表
        """
        # 构建搜索 URL
        search_url = self._build_search_url(query, num_results)
        
        # 获取搜索结果页
        html = await self._fetch_html(search_url)
        if not html:
            return []
        
        # 解析搜索结果
        results = await self._parse_results(html)
        
        return results[:num_results]
    
    def _build_search_url(self, query: str, num_results: int) -> str:
        """构建搜索 URL
        
        Args:
            query: 搜索查询
            num_results: 结果数量
            
        Returns:
            搜索 URL
        """
        # 使用第一个 base_url
        base_url = self.base_urls[0]
        
        # 构建查询参数
        params = {
            "query": query,
            "ie": "utf8",
            "from": self.s_from,
            "sst_type": self.sst_type,
        }
        
        # 构建 URL
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base_url}/web?{query_string}"
    
    async def _fetch_html(self, url: str) -> Optional[str]:
        """获取 HTML 内容
        
        Args:
            url: 目标 URL
            
        Returns:
            HTML 内容
        """
        try:
            # 配置代理
            connector = None
            if self.proxy and self.proxy.startswith("socks"):
                try:
                    from aiohttp_socks import ProxyConnector
                    connector = ProxyConnector.from_url(self.proxy)
                except ImportError:
                    pass
            
            # 随机 User-Agent
            headers = self.headers.copy()
            if not self.user_agent:
                user_agents = [
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                ]
                headers["User-Agent"] = random.choice(user_agents)
            else:
                headers["User-Agent"] = self.user_agent
            
            async with aiohttp.ClientSession(
                headers=headers,
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                async with session.get(url, proxy=self.proxy if not connector else None) as response:
                    if response.status == 200:
                        return await response.text()
                    else:
                        return None
                        
        except Exception as e:
            return None
    
    async def _parse_results(self, html: str) -> List[SearchResult]:
        """解析搜索结果
        
        Args:
            html: HTML 内容
            
        Returns:
            搜索结果列表
        """
        results = []
        
        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # 搜狗有两种页面布局，尝试两种选择器
            selectors = [
                ".results .rb",
                ".vrwrap",
                ".vrResult",
                ".res-list"
            ]
            
            items = []
            for selector in selectors:
                items = soup.select(selector)
                if items:
                    break
            
            for idx, item in enumerate(items, start=1):
                try:
                    # 提取标题
                    title = ""
                    title_selectors = [
                        "h3 a",
                        ".vr-title a",
                        ".res-title a"
                    ]
                    
                    for selector in title_selectors:
                        title_elem = item.select_one(selector)
                        if title_elem:
                            title = title_elem.get_text(strip=True)
                            break
                    
                    # 提取 URL
                    url = ""
                    url_selectors = [
                        "h3 a",
                        ".vr-title a",
                        ".res-title a"
                    ]
                    
                    for selector in url_selectors:
                        url_elem = item.select_one(selector)
                        if url_elem:
                            url = url_elem.get("href", "")
                            if isinstance(url, str) and url.startswith("/link?url="):
                                # 搜狗也有重定向
                                url = await self._resolve_sogou_redirect(url)
                            break
                    
                    # 提取摘要
                    abstract = ""
                    abstract_selectors = [
                        ".fz-mid p",
                        ".txt-box p",
                        ".res-desc"
                    ]
                    
                    for selector in abstract_selectors:
                        abstract_elem = item.select_one(selector)
                        if abstract_elem:
                            abstract = abstract_elem.get_text(strip=True)
                            break
                    
                    if url and title and isinstance(url, str):
                        results.append(SearchResult(
                            title=title,
                            url=url,
                            abstract=abstract,
                            rank=idx
                        ))
                        
                except Exception:
                    continue
                    
        except Exception:
            pass
        
        return results
    
    async def _resolve_sogou_redirect(self, url: str) -> str:
        """解析搜狗重定向 URL
        
        Args:
            url: 重定向 URL
            
        Returns:
            实际 URL
        """
        try:
            # 搜狗重定向 URL 格式：/link?url=<实际URL>&...
            from urllib.parse import parse_qs, urlparse
            
            # 如果是完整的 URL，直接返回
            if url.startswith("http"):
                return url
            
            # 构建完整 URL
            base_url = self.base_urls[0]
            full_url = urljoin(base_url, url)
            
            # 解析查询参数
            parsed = urlparse(full_url)
            query_params = parse_qs(parsed.query)
            
            if "url" in query_params:
                return query_params["url"][0]
            
            return full_url
                    
        except Exception:
            return url
    
    def validate_result(self, result: SearchResult) -> bool:
        """验证搜索结果是否有效
        
        Args:
            result: 搜索结果
            
        Returns:
            是否有效
        """
        # 调用父类验证
        if not super().validate_result(result):
            return False
        
        # 搜狗特定验证
        # 过滤掉搜狗自己的链接
        if "sogou.com" in result.url:
            return False
        
        return True
    
    async def search_with_retry(
        self, 
        query: str, 
        num_results: int,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> List[SearchResult]:
        """带重试的搜索
        
        Args:
            query: 搜索查询
            num_results: 结果数量
            max_retries: 最大重试次数
            retry_delay: 重试延迟
            
        Returns:
            搜索结果列表
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                results = await self.search(query, num_results)
                
                if results:
                    return results
                    
            except Exception as e:
                last_error = e
                
                # 如果是最后尝试，抛出异常
                if attempt == max_retries - 1:
                    break
                
                # 等待后重试
                await asyncio.sleep(retry_delay * (attempt + 1))
        
        # 所有重试都失败
        if last_error:
            raise last_error
        
        return []