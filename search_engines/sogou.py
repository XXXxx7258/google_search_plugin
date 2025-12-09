import json
import re
import random
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from .base import BaseSearchEngine, SearchResult

logger = logging.getLogger(__name__)

class SogouEngine(BaseSearchEngine):
    """搜狗搜索引擎实现"""
    
    base_urls: List[str]
    s_from: str
    sst_type: str
    
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.base_urls = ["https://www.sogou.com", "https://m.sogou.com"]
        self.headers.update({
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        self.s_from = self.config.get("s_from", "input")
        self.sst_type = self.config.get("sst_type", "normal")

    def _set_selector(self, selector: str) -> str:
        selectors = {
            "url": "h3 > a",
            "title": "h3",
            "text": "div.fz-mid.p, .txt-box p",
            "links": "div.results div.vrwrap, div.results div.rb",
            "next": "",
        }
        return selectors.get(selector, "")

    async def _get_next_page(self, query: str) -> str:
        params = {
            "query": query,
            "ie": "utf8",
            "from": self.s_from,
            "sst_type": self.sst_type,
        }
        url = f"{self.base_urls[0]}/web?{urlencode(params)}"
        return await self._get_html(url)
    
    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        results = await super().search(query, num_results)
        for result in results:
            if result.url.startswith("/link?"):
                result.url = self.base_urls[0] + result.url
                result.url = await self._parse_sogou_redirect(result.url)
        return results
    
    async def _parse_sogou_redirect(self, url: str) -> str:
        """解析搜狗重定向URL
        
        Args:
            url: 重定向URL
            
        Returns:
            真实URL
        """
        html = await self._get_html(url)
        soup = BeautifulSoup(html, "html.parser")
        script = soup.find("script")
        if script:
            script_text = script.get_text()
            match = re.search(r'window.location.replace\("(.+?)"\)', script_text)
            if match:
                return match.group(1)
        return url

    async def search_images(self, query: str, num_results: int) -> List[Dict[str, str]]:
        """执行搜狗图片搜索
        
        Args:
            query: 搜索关键词
            num_results: 期望的图片数量
            
        Returns:
            图片信息字典列表，格式：[{"image": "图片URL", "title": "图片标题", "thumbnail": "缩略图URL"}]
        """
        try:
            # 构建搜狗图片搜索URL
            params = {
                "query": query,
                "mode": 1,  # 模式1：综合搜索
                "start": 0,
                "reqType": "ajax",
                "reqFrom": "result",
                "tn": 0
            }
            
            search_url = f"https://pic.sogou.com/pics?{urlencode(params)}"
            logger.info(f"请求搜狗图片搜索URL: {search_url}")
            
            # 搜狗图片搜索返回的是JSON数据
            html = await self._get_html(search_url)
            if not html:
                logger.warning(f"搜狗图片搜索未获取到响应: {query}")
                return []
            
            results = []
            
            try:
                # 尝试解析JSON响应
                data = json.loads(html)
                if data.get("success") and "items" in data:
                    items = data["items"]
                    for item in items[:num_results]:
                        try:
                            # 搜狗图片数据结构
                            pic_url = item.get("pic_url") or item.get("picUrl")
                            thumb_url = item.get("thumb_url") or item.get("thumbUrl") or pic_url
                            title = item.get("title") or item.get("name") or query
                            
                            if pic_url and pic_url.startswith(("http://", "https://")):
                                # 处理可能的相对路径
                                if pic_url.startswith("//"):
                                    pic_url = "https:" + pic_url
                                
                                results.append({
                                    "image": pic_url,
                                    "title": title,
                                    "thumbnail": thumb_url or pic_url
                                })
                        except Exception as e:
                            logger.debug(f"解析搜狗图片项失败: {e}")
                            continue
                
                logger.info(f"搜狗图片搜索JSON解析找到 {len(results)} 张图片: {query}")
                return results[:num_results]
                
            except json.JSONDecodeError:
                # 如果不是JSON，尝试HTML解析
                logger.debug("搜狗图片搜索响应不是JSON，尝试HTML解析")
                soup = BeautifulSoup(html, "html.parser")
                
                # 搜狗图片搜索的HTML结构
                image_elements = soup.select("div.img-box, div.pic-box, a.pic")
                
                for elem in image_elements[:num_results]:
                    try:
                        # 查找img标签
                        img_elem = elem.find("img")
                        if img_elem:
                            image_url = img_elem.get("src") or img_elem.get("data-src")
                            if image_url and image_url.startswith(("http://", "https://")):
                                # 处理相对路径
                                if image_url.startswith("//"):
                                    image_url = "https:" + image_url
                                elif image_url.startswith("/"):
                                    image_url = "https://pic.sogou.com" + image_url
                                
                                title = img_elem.get("alt") or query
                                
                                # 尝试获取原图链接（可能藏在a标签中）
                                link_elem = elem.find("a")
                                if link_elem and link_elem.get("href"):
                                    original_url = link_elem.get("href")
                                    if original_url.startswith(("http://", "https://")):
                                        image_url = original_url
                                    elif original_url.startswith("/"):
                                        image_url = "https://pic.sogou.com" + original_url
                                
                                results.append({
                                    "image": image_url,
                                    "title": title,
                                    "thumbnail": image_url  # 如果没有缩略图，使用原图
                                })
                    except Exception as e:
                        logger.debug(f"解析搜狗图片HTML元素失败: {e}")
                        continue
                
                logger.info(f"搜狗图片搜索HTML解析找到 {len(results)} 张图片: {query}")
                return results[:num_results]
                
        except Exception as e:
            logger.error(f"搜狗图片搜索错误: {query} - {e}", exc_info=True)
            return []
