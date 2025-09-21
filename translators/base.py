"""
基础翻译器抽象类

定义所有翻译器的通用接口和基础功能
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import time


@dataclass
class TranslationResult:
    """翻译结果数据类"""
    query: str
    translations: List[str]
    source: str
    cached: bool = False
    timestamp: float = 0.0
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class BaseTranslator(ABC):
    """翻译器基础抽象类"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.cache = {}  # 简单内存缓存
        self.cache_ttl = self.config.get("cache_ttl", 3600)  # 缓存过期时间，默认1小时
        self.max_cache_size = self.config.get("cache_size", 1000)
        
    @abstractmethod
    async def translate(self, query: str) -> TranslationResult:
        """
        执行翻译操作
        
        Args:
            query: 待翻译的查询文本
            
        Returns:
            TranslationResult: 翻译结果
        """
        pass
    
    @property
    @abstractmethod 
    def name(self) -> str:
        """翻译器名称"""
        pass
    
    def _get_from_cache(self, query: str) -> Optional[TranslationResult]:
        """从缓存获取结果"""
        if query not in self.cache:
            return None
            
        result, timestamp = self.cache[query]
        if time.time() - timestamp > self.cache_ttl:
            # 缓存过期，删除
            del self.cache[query]
            return None
            
        # 返回缓存结果，标记为缓存
        cached_result = TranslationResult(
            query=result.query,
            translations=result.translations,
            source=result.source,
            cached=True,
            timestamp=result.timestamp
        )
        return cached_result
    
    def _save_to_cache(self, result: TranslationResult) -> None:
        """保存结果到缓存"""
        # 如果缓存过大，清理最旧的条目
        if len(self.cache) >= self.max_cache_size:
            # 删除最旧的条目
            oldest_key = min(self.cache.keys(), 
                           key=lambda k: self.cache[k][1])
            del self.cache[oldest_key]
            
        self.cache[result.query] = (result, time.time())
    
    def clear_cache(self) -> None:
        """清空缓存"""
        self.cache.clear()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        return {
            "cache_size": len(self.cache),
            "max_cache_size": self.max_cache_size,
            "cache_ttl": self.cache_ttl
        }