"""主搜索流程:rewrite → engines → fetch → summarize。

从老 plugin.py 的 ``_execute_model_driven_search`` 抽出。

注:工具调用结果由 host 的 maisaka.reasoning_engine 自动写入 ``tool_records`` 表
(``database_api.store_tool_info``),插件不再自己写 ChatHistory。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from ..tools.rewrite_output import parse_rewrite_output
from ._envelope import peel_envelope
from .prompts import build_rewrite_prompt, build_summarize_prompt, format_results_for_prompt

if TYPE_CHECKING:
    from maibot_sdk import PluginContext

    from ..config import ModelsSection, SearchBackendSection
    from .content_fetcher import ContentFetcher
    from .engine_chain import EngineChain
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
    ) -> None:
        self._ctx = ctx
        self._models = models_cfg
        self._backend = backend_cfg
        self._engines = engine_chain
        self._fetcher = content_fetcher
        self._llm = llm_runner

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
            chat_id: 当前聊天流 ID(取上下文用)
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

        rewritten_query, _ = parse_rewrite_output(rewrite_output)
        if not rewritten_query:
            logger.info("LLM 未能生成有效搜索词,返回原始 rewrite 文本")
            return rewrite_output

        logger.info("rewrite 后的搜索词: %s", rewritten_query)

        # ---- 3. 多引擎 fallback 搜索 ---- #
        max_results = self._backend.max_results
        # 只在调用方(web_search Tool 参数)显式指定时才用 tavily_topic;
        # 不再让 rewrite LLM 建议 topic ——「最新赛况」之类的中文 query 命中
        # tavily news 索引会被切换到只收英文体育的国际新闻库,反而劣化结果。
        results = await self._engines.search_with_fallback(
            rewritten_query,
            max_results,
            tavily_topic=tavily_topic_override,
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

        return final_answer

    # ------------------------------------------------------------------ #
    # 内部:取聊天上下文(为 rewrite prompt 准备 context_str)
    # ------------------------------------------------------------------ #

    async def _fetch_context(self, chat_id: str) -> str:
        """拉聊天上下文并本地拼成可读文本。

        **不走 ctx.message.build_readable**——host 那个 cap 在
        ``_cap_message_build_readable`` 里调 ``message_service.build_readable_messages``
        要求 SessionMessage 对象,但 ``_cap_message_get_by_time_in_chat`` 已经
        把对象通过 ``_serialize_messages`` 序列化成 dict/str,两边对不上,
        会抛 ``'str' object has no attribute 'processed_plain_text'``。

        改成:get_by_time_in_chat 拿到 dict 列表,在插件侧自己拼。
        host dict 里已经平铺了 ``processed_plain_text`` / ``display_message`` /
        ``message_info.user_info.*`` 等必要字段,完全够用。
        """
        if not chat_id:
            logger.info("_fetch_context: chat_id 为空,跳过")
            return ""

        time_gap = self._models.context_time_gap
        max_limit = self._models.context_max_limit
        current_ts = time.time()
        start_ts = current_ts - time_gap

        logger.info(
            "_fetch_context: chat_id=%s start_ts=%.3f end_ts=%.3f limit=%d",
            chat_id,
            start_ts,
            current_ts,
            max_limit,
        )

        try:
            # SDK 类型注解写 str,实际 host 用 float() 强转,故传 number 即可
            messages = await self._ctx.message.get_by_time_in_chat(
                chat_id=chat_id,
                start_time=start_ts,           # type: ignore[arg-type]
                end_time=current_ts,           # type: ignore[arg-type]
                limit=max_limit,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_by_time_in_chat 失败: %s", exc)
            return ""

        logger.info(
            "_fetch_context: get_by_time_in_chat 返回 type=%s",
            type(messages).__name__,
        )

        # 防御性剥皮:SDK 2.4 / 新 Runner 双层 envelope
        # {"success": True, "result": {"success": True, "messages": [...]}}
        messages = peel_envelope(messages)
        if isinstance(messages, dict):
            # 剥完仍是 dict 时,从 messages 键提取 list
            inner_list = messages.get("messages")
            if isinstance(inner_list, list):
                messages = inner_list
            else:
                logger.warning(
                    "_fetch_context: peel 后仍是 dict 且无 'messages' 列表,keys=%s",
                    sorted(messages.keys()),
                )
                return ""

        if not isinstance(messages, list):
            logger.warning("_fetch_context: messages 非 list,type=%s", type(messages).__name__)
            return ""

        if not messages:
            logger.info("_fetch_context: 拿到空列表(时间窗内可能没有消息)")
            return ""

        first = messages[0]
        first_info = (
            f"keys={sorted(first.keys())}" if isinstance(first, dict) else f"type={type(first).__name__}"
        )
        logger.info("_fetch_context: 拿到 %d 条消息,首条 %s", len(messages), first_info)

        text = _format_messages_to_readable(messages)
        preview = text[:200].replace("\n", "\\n") if text else ""
        logger.info("_fetch_context: 拼出文本长度=%d preview=%r", len(text), preview)
        return text


def _format_messages_to_readable(messages: list) -> str:
    """把 host 序列化过的 message dict 列表拼成 ``[HH:MM:SS] 名字: 文本`` 形式。

    防御性处理:跳过结构不全的项(非 dict / 缺 user / 缺文本)。

    Args:
        messages: ``ctx.message.get_by_time_in_chat`` 返回的 dict 列表
    """
    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        user_info = (msg.get("message_info") or {}).get("user_info") or {}
        user_name = (
            user_info.get("user_cardname")
            or user_info.get("user_nickname")
            or user_info.get("user_id")
            or "未知"
        )
        text = msg.get("processed_plain_text") or msg.get("display_message") or ""
        if not text:
            continue
        ts_prefix = ""
        ts_raw = msg.get("timestamp")
        if ts_raw is not None:
            try:
                ts_float = float(ts_raw)
                ts_prefix = "[" + time.strftime("%H:%M:%S", time.localtime(ts_float)) + "] "
            except (ValueError, TypeError):
                ts_prefix = ""
        lines.append(f"{ts_prefix}{user_name}: {text}")
    return "\n".join(lines)
