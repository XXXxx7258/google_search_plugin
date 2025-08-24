import os
import asyncio
import random
from typing import List, Tuple, Type, Dict, Any, Optional
from urllib.parse import urlparse, unquote, parse_qs, parse_qsl, urlencode
from dataclasses import dataclass

import aiohttp
from bs4 import BeautifulSoup

from src.common.logger import get_logger
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseTool,
    ComponentInfo,
    ConfigField,
    ToolParamType,
)

# 导入搜索引擎
from .search_engines.base import SearchResult
from .search_engines.google import GoogleEngine
from .search_engines.bing import BingEngine
from .search_engines.sogou import SogouEngine

logger = get_logger("google_search")

# User-Agent 池
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


class WebSearchTool(BaseTool):
    """简化的 Web 搜索工具"""
    
    name = "web_search"
    description = "进行网络搜索并聚合结果"
    parameters = [
        ("query", ToolParamType.STRING, "搜索关键词或问题", True, None),
        ("with_content", ToolParamType.BOOLEAN, "是否抓取正文内容", False, None),
        ("max_results", ToolParamType.INTEGER, "返回的结果数量", False, None),
    ]
    available_for_llm = True
    
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._initialize_engines()
        
    def _initialize_engines(self) -> None:
        """初始化搜索引擎"""
        config = self.plugin_config
        
        # 准备各搜索引擎的配置
        google_config = {
            "pause_time": config.get("engines", {}).get("google", {}).get("pause_time", 5.0),
            "language": config.get("engines", {}).get("google", {}).get("language", "zh-cn"),
            "country": config.get("engines", {}).get("google", {}).get("country", "cn"),
        }
        
        bing_config = {
            "market": config.get("engines", {}).get("bing", {}).get("market", "zh-CN"),
            "language": config.get("engines", {}).get("bing", {}).get("language", "zh-CN"),
        }
        
        sogou_config = {
            "type": config.get("engines", {}).get("sogou", {}).get("type", "web"),
        }
        
        self.google = GoogleEngine(google_config)
        self.bing = BingEngine(bing_config)
        self.sogo = SogouEngine(sogou_config)
        
        # 存储配置供后续使用
        self.search_config = config.get("search", {})
        self.advanced_config = config.get("advanced", {})
        
    async def execute(self, function_args: dict) -> dict:
        """执行搜索"""
        try:
            query = function_args.get("query", "").strip()
            with_content = function_args.get("with_content", self.advanced_config.get("fetch_content", True))
            max_results = function_args.get("max_results", self.search_config.get("max_results", 5))
            
            if not query:
                return {"name": self.name, "content": "查询关键词为空"}
            
            # 执行搜索（带重试）
            retry_count = self.search_config.get("retry_count", 2)
            retry_delay = self.search_config.get("retry_delay", 1.0)
            
            for attempt in range(retry_count + 1):
                try:
                    results = await self._search_with_fallback(query, max_results)
                    
                    if not results:
                        if attempt < retry_count:
                            logger.warning(f"搜索失败，{retry_delay}秒后重试 (尝试 {attempt + 1}/{retry_count + 1})")
                            await asyncio.sleep(retry_delay)
                            continue
                        return {"name": self.name, "content": f"未找到关于「{query}」的相关信息。"}
                    
                    # 抓取内容（如果需要）
                    if with_content:
                        results = await self._fetch_content_for_results(results)
                    
                    # 格式化输出
                    output = self._format_results(results)
                    
                    return {"name": self.name, "content": output}
                    
                except Exception as e:
                    if attempt < retry_count:
                        logger.warning(f"搜索异常，{retry_delay}秒后重试: {e} (尝试 {attempt + 1}/{retry_count + 1})")
                        await asyncio.sleep(retry_delay)
                    else:
                        # 最后一次重试也失败了，返回错误信息
                        return {"name": self.name, "content": f"Web搜索失败: {str(e)}"}
            
            # 如果所有重试都失败了，返回错误信息
            return {"name": self.name, "content": f"Web搜索失败：所有重试都已用尽"}
            
        except Exception as e:
            logger.error(f"Web搜索执行异常: {e}", exc_info=True)
            return {"name": self.name, "content": f"Web搜索失败: {str(e)}"}
    
    async def _search_with_fallback(self, query: str, num_results: int) -> List[SearchResult]:
        """带降级的搜索"""
        config = self.plugin_config
        engines_config = config.get("engines", {})
        
        # 获取默认搜索引擎顺序
        default_engine = config.get("search", {}).get("default_engine", "google")
        
        # 定义搜索引擎顺序
        engine_order = []
        if default_engine == "google":
            engine_order = [("google", self.google), ("bing", self.bing), ("sogou", self.sogo)]
        elif default_engine == "bing":
            engine_order = [("bing", self.bing), ("google", self.google), ("sogou", self.sogo)]
        elif default_engine == "sogou":
            engine_order = [("sogou", self.sogo), ("google", self.google), ("bing", self.bing)]
        
        # 按顺序尝试搜索引擎
        for engine_name, engine in engine_order:
            # 检查引擎是否启用
            if not engines_config.get(engine_name, {}).get("enabled", True):
                logger.info(f"搜索引擎 {engine_name} 已禁用，跳过")
                continue
                
            try:
                results = await engine.search(query, num_results)
                if results:
                    logger.info(f"{engine_name} 搜索成功，返回 {len(results)} 条结果")
                    return results
            except Exception as e:
                logger.warning(f"{engine_name} 搜索失败: {e}")
        
        return []
    
    async def _fetch_content_for_results(self, results: List[SearchResult]) -> List[SearchResult]:
        """为搜索结果抓取内容"""
        # 简化版本，直接返回原结果
        # 实际实现可以参考 web_searcher 的内容抓取逻辑
        return results
    
    def _format_results(self, results: List[SearchResult]) -> str:
        """格式化搜索结果"""
        lines = []
        
        for idx, result in enumerate(results, start=1):
            # 标题行
            header = f"{idx}. {result.title}"
            if result.url:
                header += f" {result.url}"
            lines.append(header)
            
            # 摘要
            if result.abstract:
                lines.append(result.abstract)
            
            # 空行分隔
            lines.append("")
        
        return "\n".join(lines).strip()


@register_plugin
class google_search_simple(BasePlugin):
    """简化的 Google Search 插件"""
    
    plugin_name: str = "google_search"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = [
        "googlesearch-python",
        "aiohttp",
        "beautifulsoup4",
        "lxml",
    ]
    config_file_name: str = "config.toml"
    
    config_schema: dict = {
        "plugin": {
            "name": ConfigField(type=str, default="google_search", description="插件名称"),
            "version": ConfigField(type=str, default="1.0.0", description="插件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "search": {
            "default_engine": ConfigField(type=str, default="google", description="默认搜索引擎 (google/bing/sogou)"),
            "max_results": ConfigField(type=int, default=5, description="默认返回结果数量"),
            "timeout": ConfigField(type=int, default=30, description="搜索超时时间（秒）"),
            "retry_count": ConfigField(type=int, default=2, description="失败重试次数"),
            "retry_delay": ConfigField(type=float, default=1.0, description="重试延迟（秒）"),
        },
        "engines": {
            "google": {
                "enabled": ConfigField(type=bool, default=True, description="是否启用Google搜索"),
                "pause_time": ConfigField(type=float, default=5.0, description="Google搜索间隔（秒）"),
                "language": ConfigField(type=str, default="zh-cn", description="搜索语言"),
                "country": ConfigField(type=str, default="cn", description="搜索国家/地区"),
            },
            "bing": {
                "enabled": ConfigField(type=bool, default=True, description="是否启用Bing搜索"),
                "market": ConfigField(type=str, default="zh-CN", description="Bing市场区域"),
                "language": ConfigField(type=str, default="zh-CN", description="搜索语言"),
            },
            "sogou": {
                "enabled": ConfigField(type=bool, default=True, description="是否启用搜狗搜索"),
                "type": ConfigField(type=str, default="web", description="搜索类型 (web/news)"),
            },
        },
        "advanced": {
            "user_agents": ConfigField(type=list, default=[
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
            ], description="User-Agent列表"),
            "fetch_content": ConfigField(type=bool, default=True, description="是否抓取网页内容"),
            "content_timeout": ConfigField(type=int, default=10, description="内容抓取超时（秒）"),
            "max_content_length": ConfigField(type=int, default=5000, description="最大内容长度"),
        },
    }
    
    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """获取插件提供的组件"""
        return [
            (WebSearchTool.get_tool_info(), WebSearchTool),
        ]
