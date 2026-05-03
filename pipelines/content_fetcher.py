"""网页正文抓取(普通页 trafilatura/readability/bs4 三级降级)。

知乎专用走 ``ZhihuExtractor``。
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING, Optional

import aiohttp
from bs4 import BeautifulSoup
from readability import Document

from .zhihu_extractor import ZhihuExtractor, is_zhihu_url

if TYPE_CHECKING:
    from ..config import EnginesSection, SearchBackendSection
    from ..search_engines.base import SearchResult
    from ..search_engines.you import YouContentsClient

logger = logging.getLogger(__name__)


class ContentFetcher:
    """网页正文抓取器。

    持有 backend / engines 配置 + 知乎抓取器 + you_contents 客户端,
    暴露 ``fetch_single`` (单页) 与 ``fetch_batch`` (批量补充 SearchResult)。
    """

    def __init__(
        self,
        *,
        backend_cfg: "SearchBackendSection",
        engines_cfg: "EnginesSection",
        you_contents: "YouContentsClient",
        zhihu_extractor: "ZhihuExtractor",
    ) -> None:
        self._backend = backend_cfg
        self._engines = engines_cfg
        self._you_contents = you_contents
        self._zhihu = zhihu_extractor

    async def fetch_single(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """抓取单个页面的正文内容。

        Args:
            session: 共享 aiohttp 会话
            url: 待抓取的 URL

        Returns:
            提取到的正文;失败时 None
        """
        if is_zhihu_url(url):
            return await self._zhihu.fetch(url)

        timeout = self._backend.content_timeout
        max_length = self._backend.max_content_length

        try:
            user_agents = list(self._backend.user_agents) or [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ]
            headers = {"User-Agent": random.choice(user_agents)}

            request_kwargs: dict = {"timeout": aiohttp.ClientTimeout(total=timeout), "headers": headers}
            proxy = self._backend.proxy or ""
            if proxy:
                request_kwargs["proxy"] = proxy

            async with session.get(url, **request_kwargs) as response:
                if response.status != 200:
                    logger.warning("抓取失败 %s 状态码 %s", url, response.status)
                    return None

                html_bytes = await response.read()
                # 智能解码
                try:
                    html = html_bytes.decode(response.charset or "utf-8")
                except (UnicodeDecodeError, TypeError):
                    try:
                        html = html_bytes.decode("gbk", errors="ignore")
                    except UnicodeDecodeError:
                        html = html_bytes.decode("utf-8", errors="ignore")

                # 1. trafilatura
                try:
                    import trafilatura

                    extracted = trafilatura.extract(
                        html,
                        include_comments=False,
                        include_tables=True,
                        no_fallback=False,
                    )
                    if extracted and len(extracted.strip()) > 100:
                        logger.debug("trafilatura 提取成功 %s", url)
                        return extracted.strip()[:max_length]
                except ImportError:
                    logger.debug("trafilatura 未安装,跳过")
                except Exception as exc:  # noqa: BLE001
                    logger.debug("trafilatura 提取失败: %s", exc)

                # 2. readability-lxml
                try:
                    doc = Document(html, min_text_length=50, retry_length=250, url=url)
                    summary_html = doc.summary()
                    soup = BeautifulSoup(summary_html, "lxml")
                    readability_text = soup.get_text(separator="\n", strip=True)
                    if readability_text and len(readability_text) > 100:
                        logger.debug("readability 提取成功 %s", url)
                        return readability_text[:max_length]
                except Exception as exc:  # noqa: BLE001
                    logger.debug("readability 提取失败: %s", exc)

                # 3. BeautifulSoup 兜底
                try:
                    soup = BeautifulSoup(html, "lxml")
                    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                        tag.decompose()
                    fallback = soup.get_text(separator="\n", strip=True)
                    logger.debug("BeautifulSoup 兜底 %s", url)
                    return fallback[:max_length] if fallback else None
                except Exception as exc:  # noqa: BLE001
                    logger.error("BeautifulSoup 兜底也失败: %s", exc)
                    return None

        except asyncio.TimeoutError:
            logger.warning("抓取超时: %s", url)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error("抓取未知错误 %s: %s", url, exc)
            return None

    async def fetch_batch(
        self,
        results: "list[SearchResult]",
        *,
        last_success_engine: str,
    ) -> "list[SearchResult]":
        """为搜索结果批量抓取/补充内容。

        Args:
            results: 搜索结果列表(就地修改 abstract/content 字段)
            last_success_engine: 上一次成功的引擎名(决定是否走 you_contents)

        Returns:
            就地修改后的 results
        """
        urls_to_fetch = [r.url for r in results if r.url]
        if not urls_to_fetch:
            return results

        max_length = self._backend.max_content_length

        # 优先走 You Contents(若启用 + 当次引擎是 you 系)
        use_you_contents = False
        if self._engines.you_contents_enabled:
            if self._you_contents.has_api_keys():
                if self._engines.you_contents_force or last_success_engine in {"you", "you_news"}:
                    use_you_contents = True
            else:
                logger.info("You Contents 未配置 API key,跳过")

        if use_you_contents:
            contents_map = await self._you_contents.fetch_contents(urls_to_fetch)
            if contents_map:
                for result in results:
                    url = result.url
                    if not url:
                        continue
                    content = contents_map.get(url)
                    if not content:
                        continue
                    if max_length and len(content) > max_length:
                        content = content[:max_length]
                    if result.abstract:
                        if content not in result.abstract:
                            result.abstract = f"{result.abstract}\n{content}"
                    else:
                        result.abstract = content
                    if not result.content:
                        result.content = content

                urls_to_fetch = [u for u in urls_to_fetch if u not in contents_map]
                if not urls_to_fetch:
                    return results

        # 普通页并发抓取
        async with aiohttp.ClientSession(trust_env=True) as session:
            tasks = [self.fetch_single(session, url) for url in urls_to_fetch]
            content_results = await asyncio.gather(*tasks, return_exceptions=True)

            content_map = dict(zip(urls_to_fetch, content_results, strict=True))
            for result in results:
                url = result.url
                if not url or url not in content_map:
                    continue
                content_or_exc = content_map[url]
                if isinstance(content_or_exc, str) and content_or_exc:
                    if result.abstract:
                        result.abstract = f"{result.abstract}\n{content_or_exc}"
                    else:
                        result.abstract = content_or_exc
                elif isinstance(content_or_exc, Exception):
                    logger.warning("抓取 %s 异常: %s", url, content_or_exc)

        return results

    @staticmethod
    def integrate_inline_content(
        results: "list[SearchResult]",
        tavily_answer: Optional[str],
    ) -> None:
        """将 Tavily 自带的 answer / 内联 content 就地合并到 results。

        Args:
            results: 搜索结果(就地修改)
            tavily_answer: 上一次 Tavily 搜索的 ``last_answer``;无则 None
        """
        if not results:
            return

        # 重要的延迟导入,避免循环
        from ..search_engines.base import SearchResult

        if tavily_answer:
            summarized = tavily_answer.strip()
            if summarized:
                answer_result = SearchResult(
                    title="Tavily Summary",
                    url="",
                    snippet=summarized,
                    abstract=summarized,
                    rank=-1,
                    content=summarized,
                )
                results.insert(0, answer_result)

        for result in results:
            content = (result.content or "").strip()
            if not content:
                continue
            if result.abstract:
                if content not in result.abstract:
                    result.abstract = f"{result.abstract}\n{content}"
            else:
                result.abstract = content
