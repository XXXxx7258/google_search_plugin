from typing import List, Dict, Any, Optional
import asyncio
import logging

try:
    # 导入新库
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException, TimeoutException
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False
    DDGSException = Exception
    TimeoutException = Exception

from .base import BaseSearchEngine, SearchResult

logger = logging.getLogger(__name__)

def sync_ddgs_search(query: str, search_params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """在一个同步函数中执行 DDGS 文本搜索，以便在线程池中运行
    
    Args:
        query: 搜索查询
        search_params: 搜索参数字典
        
    Returns:
        搜索结果字典列表
    """
    timeout = search_params.pop('timeout', 10)
    with DDGS(timeout=timeout) as ddgs:
        return ddgs.text(query, **search_params)

def sync_ddgs_images_search(query: str, search_params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """在一个同步函数中执行 DDGS 图片搜索，以便在线程池中运行
    
    Args:
        query: 搜索查询
        search_params: 搜索参数字典
        
    Returns:
        图片结果字典列表
    """
    timeout = search_params.pop('timeout', 10)
    with DDGS(timeout=timeout) as ddgs:
        return ddgs.images(query, **search_params)

class DuckDuckGoEngine(BaseSearchEngine):
    """使用新版 ddgs 库的搜索引擎实现
    
    这个库现在是一个元搜索引擎，可以调用多个后端。
    """
    
    region: str
    backend: str
    safesearch: str
    timelimit: Optional[str]

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        if not HAS_DDGS:
            raise ImportError("没有 ddgs 库。请确保它已在插件依赖中声明。")
        
        # 优化默认配置以提高搜索成功率
        self.region = self.config.get("region", "wt-wt")  # 全球搜索
        self.backend = self.config.get("backend", "auto")  # 自动选择最佳后端
        self.safesearch = self.config.get("safesearch", "moderate")  # 适中的安全搜索
        self.timelimit = self.config.get("timelimit")  # 时间限制，默认为 None
        
        logger.info(f"DuckDuckGo 引擎初始化完成 - region: {self.region}, backend: {self.backend}, safesearch: {self.safesearch}")

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """通过在线程池中运行同步的 ddgs.text 方法来进行搜索
        
        Args:
            query: 搜索查询
            num_results: 期望的结果数量
            
        Returns:
            搜索结果列表
        """
        try:
            loop = asyncio.get_event_loop()
            
            # 构建搜索参数
            search_params = {
                'max_results': num_results,
                'region': self.region,
                'backend': self.backend,
                'safesearch': self.safesearch,
                'timeout': self.config.get('timeout', 10)
            }
            
            # 只有当 timelimit 不为空时才添加
            if self.timelimit:
                search_params['timelimit'] = self.timelimit
            
            search_results = await loop.run_in_executor(
                None,
                sync_ddgs_search,
                query,
                search_params
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
            
        except DDGSException as e:
            if "No results found" in str(e):
                logger.info(f"ddgs 文本搜索未找到结果: {query}")
            else:
                logger.error(f"ddgs 文本搜索错误: {e}")
            return []
        except TimeoutException as e:
            logger.warning(f"ddgs 文本搜索超时: {query} - {e}")
            return []
        except Exception as e:
            logger.error(f"ddgs 文本搜索意外错误: {query} - {e}", exc_info=True)
            return []

    async def search_images(self, query: str, num_results: int) -> List[Dict[str, str]]:
        """通过在线程池中运行同步的 ddgs.images 方法来进行图片搜索
        
        Args:
            query: 搜索查询
            num_results: 期望的结果数量
            
        Returns:
            图片信息字典列表
        """
        try:
            loop = asyncio.get_event_loop()
            
            # 构建图片搜索参数
            search_params = {
                'max_results': num_results,
                'region': self.region,
                'safesearch': self.safesearch,
                'timeout': self.config.get('timeout', 10)
            }
            
            # 只有当 timelimit 不为空时才添加
            if self.timelimit:
                search_params['timelimit'] = self.timelimit
            
            search_results = await loop.run_in_executor(
                None,
                sync_ddgs_images_search,
                query,
                search_params
            )
            return search_results
            
        except DDGSException as e:
            if "No results found" in str(e):
                logger.info(f"ddgs 图片搜索未找到结果: {query}")
            else:
                logger.error(f"ddgs 图片搜索错误: {e}")
            return []
        except TimeoutException as e:
            logger.warning(f"ddgs 图片搜索超时: {query} - {e}")
            return []
        except Exception as e:
            logger.error(f"ddgs 图片搜索意外错误: {query} - {e}", exc_info=True)
            return []
