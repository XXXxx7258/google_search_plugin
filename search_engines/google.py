import asyncio
import os
from typing import List, Dict, Any, Optional

try:
    from googlesearch import search
    HAS_GOOGLESEARCH = True
except ImportError:
    HAS_GOOGLESEARCH = False
    
from urllib.parse import urlencode, parse_qs, urlparse
from .base import BaseSearchEngine, SearchResult

class GoogleEngine(BaseSearchEngine):
    """Google 搜索引擎实现"""
    
    lang: str
    proxy: Optional[str]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        if not HAS_GOOGLESEARCH:
            raise ImportError("没有googlesearch-python。")
        self.lang = self.config.get("language", "zh-CN")
        self.proxy = self.config.get("proxy") or os.environ.get("https_proxy")

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """使用 googlesearch 库进行搜索
        
        Args:
            query: 搜索查询
            num_results: 期望的结果数量
            
        Returns:
            搜索结果列表
        """
        try:
            # 在线程池中执行同步的 googlesearch，避免阻塞
            loop = asyncio.get_event_loop()
            search_results = await loop.run_in_executor(
                None,
                lambda: list(search(
                    query,
                    advanced=True,
                    num_results=num_results,
                    timeout=10,
                    proxy=self.proxy,
                    lang=self.lang.lower().replace('-', '')
                ))
            )
            
            results = []
            for i, result in enumerate(search_results):
                # 处理 googlesearch 返回的结果对象
                title = getattr(result, 'title', '') or ''
                url = getattr(result, 'url', str(result)) or str(result)
                description = getattr(result, 'description', '') or ''
                
                results.append(SearchResult(
                    title=str(title),
                    url=str(url),
                    snippet=str(description),
                    abstract=str(description),
                    rank=i
                ))
            
            return results
            
        except Exception as e:
            print(f"googlesearch 库搜索失败: {e}")
            return []
