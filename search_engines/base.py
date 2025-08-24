"""
搜索引擎基类和公共类型定义
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import asyncio


@dataclass
class SearchResult:
    """搜索结果数据类"""
    title: str
    url: str
    abstract: str = ""
    rank: int = 0
    content: str = ""


class BaseSearchEngine(ABC):
    """搜索引擎基类
    
    定义了所有搜索引擎必须实现的接口
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化搜索引擎
        
        Args:
            config: 搜索引擎配置
        """
        self.config = config or {}
        self.name = self.__class__.__name__.replace("Engine", "").lower()
        self.base_urls: List[str] = []
        self.headers: Dict[str, str] = {}
        self.timeout: int = self.config.get("timeout", 10)
        self.max_results: int = self.config.get("max_results", 10)
        self.proxy: Optional[str] = self.config.get("proxy")
        self.user_agent: Optional[str] = self.config.get("user_agent")
        
        # 初始化默认配置
        self._setup_defaults()
    
    def _setup_defaults(self):
        """设置默认配置"""
        # 子类可以重写此方法来设置特定的默认值
        pass
    
    @abstractmethod
    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """执行搜索
        
        Args:
            query: 搜索查询字符串
            num_results: 返回结果数量
            
        Returns:
            搜索结果列表
        """
        raise NotImplementedError("子类必须实现 search 方法")
    
    @abstractmethod
    def get_selectors(self, selector_type: str) -> str:
        """获取页面元素选择器
        
        Args:
            selector_type: 选择器类型（如 "url", "title", "text" 等）
            
        Returns:
            CSS 选择器字符串
        """
        raise NotImplementedError("子类必须实现 get_selectors 方法")
    
    def validate_result(self, result: SearchResult) -> bool:
        """验证搜索结果是否有效
        
        Args:
            result: 搜索结果
            
        Returns:
            是否有效
        """
        # 基本验证：URL 和标题不能为空
        if not result.url or not result.title:
            return False
        
        # URL 格式验证
        if not result.url.startswith(("http://", "https://")):
            return False
        
        return True
    
    def normalize_url(self, url: str) -> str:
        """标准化 URL
        
        Args:
            url: 原始 URL
            
        Returns:
            标准化后的 URL
        """
        # 移除跟踪参数
        from urllib.parse import urlparse, parse_qs, urlencode
        
        try:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            # 移除常见的跟踪参数
            tracking_params = {
                "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                "ref", "source", "from", "spm", "trackid"
            }
            
            clean_params = {
                k: v for k, v in query_params.items() 
                if k not in tracking_params
            }
            
            # 重建 URL
            if clean_params:
                query = urlencode(clean_params, doseq=True)
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{query}"
            else:
                return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
                
        except Exception:
            return url
    
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
            retry_delay: 重试延迟（秒）
            
        Returns:
            搜索结果列表
        """
        last_error = None
        
        for attempt in range(max_retries):
            try:
                results = await self.search(query, num_results)
                
                # 过滤和验证结果
                valid_results = []
                for result in results:
                    if self.validate_result(result):
                        result.url = self.normalize_url(result.url)
                        valid_results.append(result)
                
                return valid_results[:num_results]
                
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (attempt + 1))
                    continue
        
        # 所有重试都失败，返回空列表
        return []
    
    def get_info(self) -> Dict[str, Any]:
        """获取搜索引擎信息
        
        Returns:
            搜索引擎信息字典
        """
        return {
            "name": self.name,
            "class": self.__class__.__name__,
            "base_urls": self.base_urls,
            "timeout": self.timeout,
            "max_results": self.max_results,
            "has_proxy": bool(self.proxy),
        }