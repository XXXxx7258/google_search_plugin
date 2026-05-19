import json
import logging
import random
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode, urlparse

import aiohttp
from bs4 import BeautifulSoup

from .base import BaseSearchEngine, SearchResult, USER_AGENTS

logger = logging.getLogger(__name__)


# 高频词过滤,目的是让相关性判定有意义。
_STOPWORDS_EN = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "of", "in", "on", "at", "to",
    "for", "from", "by", "with", "is", "are", "was", "were", "be", "been",
    "being", "has", "have", "had", "do", "does", "did", "will", "would",
    "can", "could", "should", "shall", "may", "might", "must", "this", "that",
    "these", "those", "it", "its", "as", "we", "you", "they", "he", "she",
})
_STOPWORDS_ZH = frozenset({
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "和", "与", "或",
    "但", "如果", "怎样", "怎么", "如何", "什么", "为什么", "为何", "是否",
    "应该", "可以", "能否", "哪个", "哪些", "哪里", "这个", "那个",
})

_ENGLISH_ONLY_RE = re.compile(r"^[a-z0-9\s\.\?\!,\-\:\;'\"\(\)]+$")


class BingEngine(BaseSearchEngine):
    """Bing 搜索引擎实现"""

    base_urls: List[str]
    region: str
    setlang: str
    count: int

    SELECTOR_CONFIG: Dict[str, Dict[str, Any]] = {
        "url": {
            "primary": "h2 > a",
            "fallback": [
                "h2 a",
                "h3 > a",
                ".b_algo h2 a",
                ".b_algo a[href]",
            ],
        },
        "title": {
            "primary": "h2 > a",
            "fallback": [
                "h2 a",
                "h3 > a",
                ".b_algo h2 a",
                ".b_algo a[href]",
            ],
        },
        "text": {
            "primary": ".b_caption > p",
            "fallback": [
                ".b_caption",
                ".b_descript",
                ".b_snippet",
                ".b_algo .b_caption",
            ],
        },
        "links": {
            "primary": "ol#b_results > li.b_algo",
            "fallback": [
                "#b_results > li.b_algo",
                "#b_results li.b_algo",
                ".b_algo",
                "li.b_algo",
            ],
        },
        "next": {
            "primary": 'div#b_content nav[role="navigation"] a.sb_pagN',
            "fallback": [
                'nav[role="navigation"] a.sb_pagN',
                'a.sb_pagN',
                '.sb_pagN',
            ],
        },
    }
    # 黑名单留空，按需添加
    BLOCKED_DOMAINS: List[str] = []

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        # cn 优先(多数中国大陆用户场景下中文索引最优), www 兜底(海外 IP 时 cn 给空骨架)。
        self.base_urls = ["https://cn.bing.com", "https://www.bing.com"]
        self.region = self.config.get("region", "zh-CN")
        self.setlang = self.config.get("setlang", "zh")
        self.count = self.config.get("count", 10)

    def _build_keywords(self, query: str) -> List[str]:
        """构建用于相关性过滤的关键词列表,兼容中英文。"""
        if not query:
            return []
        keywords: List[str] = []
        for seg in re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query.lower()):
            if not seg:
                continue
            if seg[0].isascii():
                if seg in _STOPWORDS_EN or len(seg) < 2:
                    continue
                keywords.append(seg)
            else:
                if len(seg) <= 4:
                    if seg not in _STOPWORDS_ZH:
                        keywords.append(seg)
                else:
                    # 长中文段切 bigram,避免整段一坨永远不命中
                    for i in range(len(seg) - 1):
                        bigram = seg[i : i + 2]
                        if bigram not in _STOPWORDS_ZH:
                            keywords.append(bigram)
        seen: set[str] = set()
        return [kw for kw in keywords if not (kw in seen or seen.add(kw))]

    def _is_relevant(self, title: str, snippet: str, url: str, keywords: List[str]) -> bool:
        """命中数 ≥ 1 即通过。

        阈值保持宽松——目标是滤掉与 query 完全无关的广告/导航页,不是修正 Bing 的跑偏。
        Bing 严重跑偏的 case(诺贝尔奖/best 字典)交给下游 LLM 或上层 EngineChain 切其他引擎。
        """
        if not keywords:
            return True
        text = f"{title} {snippet} {url}".lower()
        return any(kw in text for kw in keywords)

    def _is_blocked(self, url: str) -> bool:
        """域名黑名单过滤。"""
        if not url:
            return False
        try:
            netloc = urlparse(url).netloc.lower()
        except Exception:
            return False
        return any(netloc.endswith(domain) for domain in self.BLOCKED_DOMAINS)

    def _set_selector(self, selector: str) -> str:
        """获取页面元素选择器。"""
        config = self.SELECTOR_CONFIG.get(selector, {})
        return config.get("primary", "")

    def _get_fallback_selectors(self, selector: str) -> list:
        """获取备用选择器列表。"""
        config = self.SELECTOR_CONFIG.get(selector, {})
        return config.get("fallback", [])

    async def _get_next_page(
        self,
        query: str,
        *,
        base_url: Optional[str] = None,
        region: Optional[str] = None,
        setlang: Optional[str] = None,
        market: Optional[str] = None,
    ) -> str:
        """构建并获取搜索页面 HTML。

        Bing 的 query 参数只用 ``q`` + ``adlt`` + ``mkt``,语言偏好走 Accept-Language。
        ``ensearch=1`` / ``cc`` / ``setlang`` / ``count`` 历史上传过但实测有害或被忽略。

        market / region 区分 ``None``(用 self.region 兜底) vs ``""``(明确不传 mkt)。
        """
        del setlang
        base_url = base_url or self.base_urls[0]
        if market is not None:
            mkt = market
        elif region is not None:
            mkt = region
        else:
            mkt = self.region or ""

        params: dict[str, str] = {"q": query, "adlt": "off"}
        if mkt and mkt != "clear":
            params["mkt"] = mkt

        if mkt and "-" in mkt:
            lang = mkt.split("-")[0]
            accept_language: Optional[str] = f"{mkt},{lang};q=0.9"
        elif mkt:
            accept_language = f"{mkt};q=0.9"
        else:
            accept_language = None

        query_string = urlencode(params)
        search_url = f"{base_url}/search?{query_string}"
        logger.info(f"Requesting Bing search URL: {search_url}")
        return await self._fetch(search_url, accept_language=accept_language)

    async def _fetch(self, url: str, *, accept_language: Optional[str] = None) -> str:
        """Per-request 抓取,不污染 self.headers,避免并发下 Accept-Language 跨请求泄漏。

        与 base._get_html 行为等价,但 headers 用本地 dict 而非共享实例属性。
        """
        headers = dict(self.headers)
        headers["Referer"] = url
        headers["User-Agent"] = random.choice(USER_AGENTS)
        if accept_language:
            headers["Accept-Language"] = accept_language
        else:
            headers.pop("Accept-Language", None)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.TIMEOUT),
                proxy=self.proxy,
            ) as resp:
                resp.raise_for_status()
                return await resp.text()

    def _get_link_elements(self, soup: BeautifulSoup) -> List[Any]:
        """获取搜索结果节点，包含主选择器和回退。"""
        links_selector = self._set_selector("links")
        if links_selector:
            links = soup.select(links_selector)
            if links:
                return links
        for fallback_selector in self._get_fallback_selectors("links"):
            links = soup.select(fallback_selector)
            if links:
                logger.info(f"Fallback selector '{fallback_selector}' found {len(links)} results")
                return links
        return []

    def _select_with_fallback(self, element: Any, selector_name: str) -> Optional[Any]:
        """在元素上按主/备用选择器查找单个子元素。"""
        primary = self._set_selector(selector_name)
        if primary:
            found = element.select_one(primary)
            if found:
                return found
        for fallback in self._get_fallback_selectors(selector_name):
            found = element.select_one(fallback)
            if found:
                return found
        return None

    def _parse_page_results(self, soup: BeautifulSoup, keywords: List[str]) -> List[SearchResult]:
        """解析页面并生成过滤后的 SearchResult 列表。"""
        links = self._get_link_elements(soup)
        if not links:
            return []

        results: List[SearchResult] = []
        for idx, link in enumerate(links):
            title_elem = self._select_with_fallback(link, "title")
            url_elem = self._select_with_fallback(link, "url")
            text_elem = self._select_with_fallback(link, "text")

            # Bing 在 snippet 里插入 "🌐 翻译此页" 等装饰 span,extract 前去掉免得污染。
            if text_elem is not None:
                for icon in text_elem.select("span.algoSlug_icon"):
                    icon.decompose()

            title = self.tidy_text(title_elem.text) if title_elem else ""
            url_raw = url_elem.get("href") if url_elem else ""
            url = self._normalize_url(url_raw)
            snippet = self.tidy_text(text_elem.text) if text_elem else ""

            if title and url and not self._is_blocked(url) and self._is_relevant(title, snippet, url, keywords):
                results.append(SearchResult(title=title, url=url, snippet=snippet, abstract=snippet, rank=idx))
        return results

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """多 variant 顺序尝试,首个非空结果 break。过滤后 0 时返回空,交给上层换引擎。

        英文 query 不传 mkt——实测 mkt=en-US 会触发 Bing 短词命名实体模式,
        "best practices for python asyncio timeout" 会被搜成 "best" 返字典/Best Buy。
        """
        try:
            keywords = self._build_keywords(query)
            is_english = bool(_ENGLISH_ONLY_RE.fullmatch(query.lower().strip()))
            # 中文 query: cn 优先(中国大陆 IP 最优),www 兜底(海外 IP 时 cn 空骨架自动 fall through)
            # 英文 query: 只走 www 不传 mkt(cn 对英文 query 偏返中文翻译/字典页;
            #            zh_fallback 带 mkt 会触发 Bing 短词命名实体模式,故不作为英文 fallback)
            zh_variant = {
                "base_url": "https://cn.bing.com",
                "region": self.region,
                "setlang": self.setlang,
                "market": self.region,
            }
            zh_fallback = {
                "base_url": "https://www.bing.com",
                "region": self.region,
                "setlang": self.setlang,
                "market": self.region,
            }
            en_variant = {
                "base_url": "https://www.bing.com",
                "region": "",
                "setlang": "en",
                "market": "",
            }
            fetch_variants = [en_variant] if is_english else [zh_variant, zh_fallback, en_variant]

            results: List[SearchResult] = []
            for variant in fetch_variants:
                resp = await self._get_next_page(query, **variant)
                soup = BeautifulSoup(resp, "html.parser")
                page_results = self._parse_page_results(soup, keywords)
                if page_results:
                    results.extend(page_results)
                    break

            if not results:
                logger.warning(f"No relevant results remain after filtering for query '{query}'")

            logger.info(f"Returning {len(results[:num_results])} search results for query '{query}'")
            return results[:num_results]
        except Exception as e:
            logger.error(f"Error in Bing search for query {query}: {e}", exc_info=True)
            return []

    async def search_images(self, query: str, num_results: int) -> List[Dict[str, str]]:
        """执行Bing图片搜索（国内可直接访问，无需科学上网）

        Args:
            query: 搜索关键词
            num_results: 期望的图片数量

        Returns:
            图片信息字典列表，格式：[{"image": "图片URL", "title": "图片标题", "thumbnail": "缩略图URL"}]
        """
        try:
            params = {
                "q": query,
                "first": 1,
                "count": min(num_results, 150),
                "cw": 1177,
                "ch": 826,
                "FORM": "HDRSC2"
            }

            html = ""
            successful_base_url = ""
            for base_url in self.base_urls:
                try:
                    search_url = f"{base_url}/images/search?{urlencode(params)}"
                    logger.debug(f"请求Bing图片搜索URL: {search_url}")
                    html = await self._get_html(search_url)
                    if html and ("img_cont" in html or "iusc" in html):
                        successful_base_url = base_url
                        break
                except Exception as e:
                    logger.warning(f"Bing图片搜索域名 {base_url} 失败: {e}")
                    continue

            if not html:
                logger.warning(f"Bing图片搜索未获取到有效HTML: {query}")
                return []

            soup = BeautifulSoup(html, "html.parser")
            results = []

            image_elements = soup.select("a.iusc")

            for elem in image_elements[:num_results]:
                try:
                    m_attr = elem.get("m")
                    if m_attr:
                        try:
                            m_data = json.loads(m_attr)
                            image_url = m_data.get("murl", "")
                            thumbnail_url = m_data.get("turl", "")
                            title = m_data.get("t", "")

                            if image_url and image_url.startswith(("http://", "https://")):
                                results.append({
                                    "image": image_url,
                                    "title": title or query,
                                    "thumbnail": thumbnail_url or image_url
                                })
                                continue
                        except json.JSONDecodeError:
                            pass

                    img_elem = elem.find("img")
                    if img_elem:
                        image_url = img_elem.get("src") or img_elem.get("data-src")
                        if image_url:
                            if image_url.startswith("//"):
                                image_url = "https:" + image_url
                            elif image_url.startswith("/") and successful_base_url:
                                image_url = f"{successful_base_url}{image_url}"

                            if image_url.startswith(("http://", "https://")):
                                title = img_elem.get("alt") or query
                                results.append({
                                    "image": image_url,
                                    "title": title,
                                    "thumbnail": image_url
                                })
                except Exception as e:
                    logger.debug(f"解析Bing图片元素失败: {e}")
                    continue

            logger.debug(f"Bing图片搜索找到 {len(results)} 张图片: {query}")
            return results[:num_results]

        except Exception as e:
            logger.error(f"Bing图片搜索错误: {query} - {e}", exc_info=True)
            return []
