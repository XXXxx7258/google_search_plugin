"""
搜索引擎模块

提供统一的搜索引擎抽象接口和具体实现
"""

from .base import BaseSearchEngine, SearchResult
from .google import GoogleEngine
from .bing import BingEngine
from .sogou import SogouEngine
from .tavily import TavilyEngine

__all__ = [
    "BaseSearchEngine",
    "SearchResult",
    "GoogleEngine",
    "BingEngine",
    "SogouEngine",
    "TavilyEngine",
]
