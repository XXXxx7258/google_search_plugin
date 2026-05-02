"""URL 直访总结流程。

从老 plugin.py 的 ``_execute_direct_url_summary`` / ``_is_url`` 抽出。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import aiohttp

from .prompts import build_url_summarize_prompt

if TYPE_CHECKING:
    from .content_fetcher import ContentFetcher
    from .history_writer import HistoryWriter
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
    """URL 直访 → 抓正文 → LLM 总结 → 落库。"""

    def __init__(
        self,
        *,
        content_fetcher: "ContentFetcher",
        llm_runner: "LLMRunner",
        history_writer: "HistoryWriter",
    ) -> None:
        self._fetcher = content_fetcher
        self._llm = llm_runner
        self._history = history_writer

    async def run(self, url: str, *, chat_id: str, bot_name: str) -> str:
        """执行 URL 直访总结。

        Args:
            url: 用户提供的 URL
            chat_id: 当前聊天流 ID(用于历史落库)
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
        final_answer = await self._llm.generate(prompt)

        # 延迟导入避免循环
        from ..search_engines.base import SearchResult

        await self._history.record(
            chat_id=chat_id,
            original_question=url,
            search_query=url,
            results=[
                SearchResult(
                    title=url,
                    url=url,
                    snippet=content[:200] if content else "",
                ),
            ],
            final_answer=final_answer,
            source_type="direct_url",
            last_success_engine=None,
        )
        return final_answer
