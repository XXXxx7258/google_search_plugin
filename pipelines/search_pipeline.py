"""主搜索流程:rewrite → engines → fetch → summarize → history。

从老 plugin.py 的 ``_execute_model_driven_search`` 抽出。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from ..tools.rewrite_output import parse_rewrite_output
from .prompts import build_rewrite_prompt, build_summarize_prompt, format_results_for_prompt

if TYPE_CHECKING:
    from maibot_sdk import PluginContext

    from ..config import ModelsSection, SearchBackendSection
    from .content_fetcher import ContentFetcher
    from .engine_chain import EngineChain
    from .history_writer import HistoryWriter
    from .llm_runner import LLMRunner

logger = logging.getLogger(__name__)


class SearchPipeline:
    """主搜索流水线"""

    def __init__(
        self,
        ctx: "PluginContext",
        *,
        models_cfg: "ModelsSection",
        backend_cfg: "SearchBackendSection",
        engine_chain: "EngineChain",
        content_fetcher: "ContentFetcher",
        llm_runner: "LLMRunner",
        history_writer: "HistoryWriter",
    ) -> None:
        self._ctx = ctx
        self._models = models_cfg
        self._backend = backend_cfg
        self._engines = engine_chain
        self._fetcher = content_fetcher
        self._llm = llm_runner
        self._history = history_writer

    async def run(
        self,
        question: str,
        *,
        chat_id: str,
        bot_name: str,
        tavily_topic_override: Optional[str] = None,
    ) -> str:
        """执行主搜索。

        Args:
            question: 用户原始问题
            chat_id: 当前聊天流 ID(取上下文 + 落库)
            bot_name: bot 昵称(prompt 用)
            tavily_topic_override: 调用方显式指定的 tavily topic(优先级高于模型建议)

        Returns:
            LLM 总结文本;无可用结果时返回提示文本
        """
        # ---- 1. 取聊天上下文 ---- #
        context_str = await self._fetch_context(chat_id)

        # ---- 2. rewrite prompt ---- #
        rewrite_prompt = build_rewrite_prompt(bot_name=bot_name, question=question, context=context_str)
        logger.info("调用 LLM 进行查询重写")
        rewrite_output = (await self._llm.generate(rewrite_prompt) or "").strip()

        if not rewrite_output:
            logger.info("LLM 未返回查询重写结果")
            return "根据上下文分析，我无法确定需要搜索的具体内容。"

        if "无需搜索" in rewrite_output:
            logger.info("LLM 判断无需搜索")
            return rewrite_output

        rewritten_query, model_tavily_topic = parse_rewrite_output(rewrite_output)
        if not rewritten_query:
            logger.info("LLM 未能生成有效搜索词,返回原始 rewrite 文本")
            return rewrite_output

        logger.info("rewrite 后的搜索词: %s (model_tavily_topic=%s)", rewritten_query, model_tavily_topic)

        # ---- 3. 多引擎 fallback 搜索 ---- #
        max_results = self._backend.max_results
        topic = tavily_topic_override or model_tavily_topic
        results = await self._engines.search_with_fallback(
            rewritten_query,
            max_results,
            tavily_topic=topic,
        )
        if not results:
            return f"关于「{rewritten_query}」，我没有找到相关的网络信息。"

        # ---- 4. 内容补充(Tavily inline / you_contents / 普通抓取) ---- #
        last_engine = self._engines.last_success_engine or ""
        if last_engine == "tavily":
            self._fetcher.integrate_inline_content(results, self._engines.last_tavily_answer)
        elif self._backend.fetch_content:
            results = await self._fetcher.fetch_batch(results, last_success_engine=last_engine)

        # ---- 5. summarize prompt ---- #
        formatted = format_results_for_prompt(results)
        summarize_prompt = build_summarize_prompt(
            bot_name=bot_name,
            original_question=question,
            search_query=rewritten_query,
            formatted_results=formatted,
        )
        logger.info("调用 LLM 对搜索结果进行总结")
        final_answer = await self._llm.generate(summarize_prompt)

        # ---- 6. 落库 ---- #
        await self._history.record(
            chat_id=chat_id,
            original_question=question,
            search_query=rewritten_query,
            results=results,
            final_answer=final_answer,
            source_type="search",
            last_success_engine=last_engine or None,
        )
        return final_answer

    # ------------------------------------------------------------------ #
    # 内部:取聊天上下文(为 rewrite prompt 准备 context_str)
    # ------------------------------------------------------------------ #

    async def _fetch_context(self, chat_id: str) -> str:
        """直接让 host 的 build_readable 在内部按 chat_id+时间窗拉消息。

        **不要**先调 ``get_by_time_in_chat`` 再把结果传给 ``build_readable``——
        host 的 ``_serialize_messages`` 把对象转成 dict/str,而
        ``build_readable_messages`` 期望未序列化的消息对象(要 ``.processed_plain_text``
        属性),会抛 ``'str' object has no attribute 'processed_plain_text'``。
        改用 ``messages=None + chat_id + start_time + end_time`` 模式,host
        内部 fetch + readable 一气呵成,绕开序列化。
        """
        if not chat_id:
            return ""

        time_gap = self._models.context_time_gap
        max_limit = self._models.context_max_limit
        current_ts = time.time()
        start_ts = current_ts - time_gap

        try:
            text = await self._ctx.message.build_readable(
                None,  # messages=None → host 触发自取
                chat_id=chat_id,
                start_time=start_ts,           # type: ignore[arg-type]
                end_time=current_ts,           # type: ignore[arg-type]
                limit=max_limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("build_readable 失败: %s", exc)
            return ""

        # 防御:host 失败时返回 {"success": False, "error": "..."} 而非 str
        if not isinstance(text, str):
            logger.warning("build_readable 返回非 str(可能 host 端报错): %r", type(text).__name__)
            return ""
        return text
