"""
神奇海螺缩写翻译器

基于神奇海螺API的中文网络缩写翻译服务
https://lab.magiconch.com/api/nbnhhsh/
"""

import aiohttp
import asyncio
from typing import List, Dict, Any, Optional
from src.common.logger import get_logger
from .base import BaseTranslator, TranslationResult

logger = get_logger("nbnhhsh_translator")


class NbnhhshTranslator(BaseTranslator):
    """神奇海螺缩写翻译器"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.api_url = self.config.get("api_url", "https://lab.magiconch.com/api/nbnhhsh/guess")
        self.timeout = self.config.get("timeout", 10)
        self.max_retries = self.config.get("max_retries", 3)
    
    @property
    def name(self) -> str:
        return "nbnhhsh"
    
    async def translate(self, query: str) -> TranslationResult:
        """
        翻译缩写
        
        Args:
            query: 待翻译的缩写
            
        Returns:
            TranslationResult: 翻译结果
        """
        if not query:
            return TranslationResult(
                query=query,
                translations=[],
                source=self.name
            )
        
        # 先检查缓存
        cached_result = self._get_from_cache(query)
        if cached_result:
            logger.info(f"从缓存获取翻译结果: {query}")
            return cached_result
        
        # 调用API获取翻译
        translations = await self._call_api(query)
        
        result = TranslationResult(
            query=query,
            translations=translations,
            source=self.name
        )
        
        # 保存到缓存
        self._save_to_cache(result)
        
        logger.info(f"翻译完成: {query} -> {translations}")
        return result
    
    async def _call_api(self, query: str) -> List[str]:
        """
        调用神奇海螺API
        
        Args:
            query: 待翻译的缩写
            
        Returns:
            List[str]: 翻译结果列表
        """
        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        self.api_url,
                        json={"text": query},
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=self.timeout),
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data and len(data) > 0:
                                # 提取翻译结果
                                result_item = data[0]
                                if "trans" in result_item and result_item["trans"]:
                                    return result_item["trans"]
                        
                        logger.warning(f"API请求失败，状态码: {response.status}")
                        return []
                        
            except asyncio.TimeoutError:
                logger.warning(f"API请求超时，尝试 {attempt + 1}/{self.max_retries}")
                if attempt == self.max_retries - 1:
                    logger.error(f"API请求最终超时: {query}")
                    
            except Exception as e:
                logger.error(f"API请求出错，尝试 {attempt + 1}/{self.max_retries}: {e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"API请求最终失败: {query}")
                    
            # 重试前等待
            if attempt < self.max_retries - 1:
                await asyncio.sleep(1 * (attempt + 1))  # 递增延迟
        
        return []
    
    def is_abbreviation_query(self, query: str) -> bool:
        """
        判断是否为缩写查询
        
        Args:
            query: 查询字符串
            
        Returns:
            bool: 是否为缩写查询
        """
        import re
        # 匹配 "xxx是什么" 或 "xxx是啥" 的模式
        pattern = r"^([a-z0-9]{2,})(?:是什么|是啥)$"
        match = re.match(pattern, query.lower().strip())
        return match is not None
    
    def extract_abbreviation(self, query: str) -> Optional[str]:
        """
        从查询中提取缩写部分
        
        Args:
            query: 查询字符串
            
        Returns:
            Optional[str]: 提取的缩写，如果不匹配则返回None
        """
        import re
        pattern = r"^([a-z0-9]{2,})(?:是什么|是啥)$"
        match = re.match(pattern, query.lower().strip())
        if match:
            return match.group(1)
        return None