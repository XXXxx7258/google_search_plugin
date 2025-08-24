"""
Google 搜索引擎实现
"""

import os
import asyncio
from typing import List, Dict, Any, Optional
from .base import BaseSearchEngine, SearchResult


class GoogleEngine(BaseSearchEngine):
    """Google 搜索引擎实现"""
    
    def _setup_defaults(self):
        """设置默认配置"""
        # Google 特定配置
        self.tld = self.config.get("tld", "com")
        self.lang = self.config.get("lang", "zh-cn")
        self.num = self.config.get("num", self.max_results)
        self.stop = self.config.get("stop", self.max_results)
        self.pause = self.config.get("pause", 5.0)  # 增加暂停时间避免限制
        self.safe = self.config.get("safe", "active")
        self.proxy = self.config.get("proxy") or os.environ.get("https_proxy")
    
    def get_selectors(self, selector_type: str) -> str:
        """获取页面元素选择器
        
        Args:
            selector_type: 选择器类型
            
        Returns:
            CSS 选择器字符串
        """
        # Google 不使用选择器，因为使用 googlesearch-python 库
        return ""
    
    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """执行 Google 搜索
        
        Args:
            query: 搜索查询
            num_results: 结果数量
            
        Returns:
            搜索结果列表
        """
        try:
            # 在线程中执行阻塞的 googlesearch 调用
            results = await asyncio.to_thread(
                self._blocking_search,
                query,
                num_results
            )
            
            # 转换为 SearchResult 对象
            return self._convert_results(results)
            
        except ImportError:
            raise RuntimeError("请先安装 googlesearch-python：pip install googlesearch-python")
        except Exception as e:
            raise RuntimeError(f"Google search failed: {str(e)}")
    
    def _blocking_search(self, query: str, num_results: int) -> list:
        """阻塞式 Google 搜索（在线程中执行）"""
        from googlesearch import search
        
        # 执行搜索，使用简单参数（参考 web_searcher 实现）
        return list(search(
            query,
            advanced=True,  # 获取高级结果（包含标题和描述）
            num_results=num_results,
            timeout=10,  # 增加超时时间
            proxy=self.proxy,
            sleep_interval=self.pause,  # 添加搜索间隔
        ))
    
    def _convert_results(self, raw_results: list) -> List[SearchResult]:
        """转换原始结果为 SearchResult 对象
        
        Args:
            raw_results: 原始搜索结果
            
        Returns:
            SearchResult 列表
        """
        results = []
        
        for idx, item in enumerate(raw_results, start=1):
            try:
                # googlesearch-python 返回的是对象，包含 url, title, description 等属性
                url = getattr(item, "url", "")
                title = getattr(item, "title", "")
                abstract = getattr(item, "description", "")
                
                if url and title:
                    results.append(SearchResult(
                        title=title,
                        url=url,
                        abstract=abstract,
                        rank=idx
                    ))
                    
            except Exception as e:
                # 跳过无效结果
                continue
        
        return results
    
    async def search_with_retry(
        self,
        query: str,
        num_results: int,
        max_retries: int = 3,
        retry_delay: float = 2.0  # 增加重试延迟
    ) -> List[SearchResult]:
        """带重试的搜索
        
        Google 搜索可能会遇到验证码或限制，需要重试机制
        
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
                
                # 等待后重试，延迟时间递增
                await asyncio.sleep(retry_delay * (attempt + 1))
        
        # 所有重试都失败
        if last_error:
            raise last_error
        
        return []