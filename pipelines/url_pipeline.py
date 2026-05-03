"""URL 直访总结流程。

从老 plugin.py 的 ``_execute_direct_url_summary`` / ``_is_url`` 抽出。

注:工具调用结果由 host 的 maisaka.reasoning_engine 自动写入 ``tool_records`` 表,
插件不再自己写 ChatHistory。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import aiohttp

from .llm_runner import LLMCallError
from .prompts import build_url_summarize_prompt

if TYPE_CHECKING:
    from .content_fetcher import ContentFetcher
    from .llm_runner import LLMRunner

logger = logging.getLogger(__name__)


def is_url(text: str) -> bool:
    """检测文本是否为单个有效的 http/https URL。"""
    if not text:
        return False
    candidate = text.strip()
    if " " in candidate:
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    return bool(parsed.netloc)


class UrlPipeline:
    """URL 直访 → 抓正文 → LLM 总结。"""

    def __init__(
        self,
        *,
        content_fetcher: "ContentFetcher",
        llm_runner: "LLMRunner",
    ) -> None:
        self._fetcher = content_fetcher
        self._llm = llm_runner

    async def run(self, url: str, *, bot_name: str) -> str:
        """执行 URL 直访总结。

        Args:
            url: 用户提供的 URL
            bot_name: bot 昵称(prompt 用)

        Returns:
            LLM 总结文本;抓取失败返回空字符串
        """
        logger.info("URL 直访开始: %s", url)
        async with aiohttp.ClientSession(trust_env=True) as session:
            content = await self._fetcher.fetch_single(session, url)

        if not content:
            logger.info("URL 内容抓取失败,返回空: %s", url)
            return ""

        logger.info("成功抓取网页内容,长度=%d", len(content))
        prompt = build_url_summarize_prompt(bot_name=bot_name, url=url, content=content)
        logger.info("调用 LLM 对网页内容进行总结")
        try:
            return await self._llm.generate(prompt)
        except LLMCallError as exc:
            logger.warning("url summarize LLM 调用失败: %s", exc)
            return f"已抓取网页内容,但总结时遇到问题:\n\n{content[:500]}..."
