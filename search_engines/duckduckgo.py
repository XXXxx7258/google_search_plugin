from typing import List, Dict, Any, Optional
import asyncio
import logging

try:
    # 导入新库
    from ddgs import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False

from .base import BaseSearchEngine, SearchResult

logger = logging.getLogger(__name__)

def sync_ddgs_search(query: str, num_results: int, region: str, backend: str, safesearch: str, timelimit: Optional[str]) -> List[Dict[str, str]]:
    """
    在一个同步函数中执行 DDGS 文本搜索，以便在线程池中运行。
    """
    with DDGS() as ddgs:
        # text 方法是同步的，它在内部处理并发和后端选择
        return ddgs.text(query, region=region, safesearch=safesearch, timelimit=timelimit, max_results=num_results, backend=backend)

def sync_ddgs_images_search(query: str, num_results: int, region: str, safesearch: str, timelimit: Optional[str]) -> List[Dict[str, str]]:
    """
    在一个同步函数中执行 DDGS 图片搜索，以便在线程池中运行。
    """
    with DDGS() as ddgs:
        return ddgs.images(query, region=region, safesearch=safesearch, timelimit=timelimit, max_results=num_results)

class DuckDuckGoEngine(BaseSearchEngine):
    """
    使用新版 ddgs 库的搜索引擎实现。
    这个库现在是一个元搜索引擎，可以调用多个后端。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        if not HAS_DDGS:
            raise ImportError("没有 ddgs 库。请确保它已在插件依赖中声明。")
        self.region = self.config.get("region", "wt-wt")
        self.backend = self.config.get("backend", "auto")
        self.safesearch = self.config.get("safesearch", "moderate")
        self.timelimit = self.config.get("timelimit") # 默认为 None

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """通过在线程池中运行同步的 ddgs.text 方法来进行搜索"""
        try:
            loop = asyncio.get_event_loop()
            
            search_results = await loop.run_in_executor(
                None,
                sync_ddgs_search,
                query,
                num_results,
                self.region,
                self.backend,
                self.safesearch,
                self.timelimit
            )
            
            results = []
            for i, r in enumerate(search_results):
                results.append(SearchResult(
                    title=r.get('title', ''),
                    url=r.get('href', ''),
                    snippet=r.get('body', ''),
                    abstract=r.get('body', ''),
                    rank=i
                ))
            return results
            
        except Exception as e:
            logger.error(f"ddgs 库搜索失败: {e}", exc_info=True)
            return []

    async def search_images(self, query: str, num_results: int) -> List[Dict[str, str]]:
        """通过在线程池中运行同步的 ddgs.images 方法来进行图片搜索"""
        try:
            loop = asyncio.get_event_loop()
            
            search_results = await loop.run_in_executor(
                None,
                sync_ddgs_images_search,
                query,
                num_results,
                self.region,
                self.safesearch,
                self.timelimit
            )
            return search_results
            
        except Exception as e:
            logger.error(f"ddgs 库图片搜索失败: {e}", exc_info=True)
            return []
