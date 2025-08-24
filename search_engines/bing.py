"""
Bing 搜索引擎实现
"""

import asyncio
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseSearchEngine, SearchResult


class BingEngine(BaseSearchEngine):
    """Bing 搜索引擎实现"""
    
    # Bing 专用的 User-Agent
    USER_AGENT_BING = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
    
    def _setup_defaults(self):
        """设置默认配置"""
        self.base_urls = ["https://cn.bing.com", "https://www.bing.com"]
        self.headers.update({"User-Agent": self.USER_AGENT_BING})
        
        # Bing 特定配置
        self.region = self.config.get("region", "zh-CN")
        self.setlang = self.config.get("setlang", "zh")
        self.count = self.config.get("count", self.max_results)
        
        # 定义选择器
        self.selectors = {
            "url": "div.b_attribution cite",
            "title": "h2",
            "text": "div.b_caption p",
            "links": "ol#b_results > li.b_algo",
            "next": 'div#b_content nav[role="navigation"] a.sb_pagN',
        }
    
    def get_selectors(self, selector_type: str) -> str:
        """获取页面元素选择器
        
        Args:
            selector_type: 选择器类型
            
        Returns:
            CSS 选择器字符串
        """
        return self.selectors.get(selector_type, "")
    
    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """执行 Bing 搜索
        
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
            "q": query,
            "setlang": self.setlang,
            "count": str(min(num_results, 50)),  # Bing 限制每页最多 50 条
        }
        
        # 添加区域参数
        if self.region:
            params["cc"] = self.region.split("-")[0] if "-" in self.region else self.region
        
        # 构建 URL
        query_string = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{base_url}/search?{query_string}"
    
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
            
            async with aiohttp.ClientSession(
                headers=self.headers,
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
            
            # 查找所有结果项
            items = soup.select(self.get_selectors("links"))
            
            for idx, item in enumerate(items, start=1):
                try:
                    # 提取标题
                    title_elem = item.select_one(self.get_selectors("title"))
                    title = title_elem.get_text(strip=True) if title_elem else ""
                    
                    # 提取 URL
                    url_elem = item.select_one("a")
                    url = ""
                    if url_elem:
                        url = str(url_elem.get("href", ""))
                        # 处理 Bing 重定向
                        if url.startswith("/aclk"):
                            url = self._resolve_bing_redirect(url)
                    
                    # 提取摘要
                    text_elem = item.select_one(self.get_selectors("text"))
                    abstract = text_elem.get_text(strip=True) if text_elem else ""
                    
                    if url and title:
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
    
    def _resolve_bing_redirect(self, url: str) -> str:
        """解析 Bing 重定向 URL
        
        Args:
            url: 重定向 URL
            
        Returns:
            实际 URL
        """
        try:
            # Bing 重定向 URL 格式：/aclk?u=<实际URL>&...
            from urllib.parse import parse_qs, urlparse
            
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            if "u" in query_params:
                return query_params["u"][0]
            
        except Exception:
            pass
        
        return url
    
    async def _get_next_page(self, query: str) -> str:
        """获取下一页（如果需要分页）"""
        # Bing 搜索通常一页返回足够结果，暂不需要分页
        return ""
    
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
        
        # Bing 特定验证
        # 过滤掉 Bing 自己的链接
        if "bing.com" in result.url:
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