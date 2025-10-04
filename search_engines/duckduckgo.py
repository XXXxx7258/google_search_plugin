from typing import List, Dict, Any, Optional
import asyncio

try:
    # 导入新库
    from ddgs import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False

from .base import BaseSearchEngine, SearchResult

def sync_ddgs_search(query: str, num_results: int, region: str, backend: str) -> List[Dict[str, str]]:
    """
    在一个同步函数中执行 DDGS 搜索，以便在线程池中运行。
    """
    with DDGS() as ddgs:
        # text 方法是同步的，它在内部处理并发和后端选择
        return ddgs.text(query, region=region, safesearch='off', timelimit='y', max_results=num_results, backend=backend)

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
        # 允许用户在 config.toml 中指定后端，默认为 'auto' 以获得最佳结果
        self.backend = self.config.get("backend", "auto")

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
                self.backend
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
            print(f"ddgs 库搜索失败: {e}")
            return []