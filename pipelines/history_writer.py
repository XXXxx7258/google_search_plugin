"""ChatHistory 落库 + 去重(走 ctx.db.query)。

字段映射(**关键!**):新 SDK 的 ChatHistory 是 SQLModel 风格,字段名跟老 peewee
版完全不同:

| v3.x plugin 写入 | 实际 SQLModel 字段 |
|---|---|
| chat_id          | session_id |
| start_time(float)| start_timestamp(datetime) |
| end_time(float)  | end_timestamp(datetime) |
| original_text    | original_messages |

去重 workaround:host 的 ``database_service._build_filters`` 只支持等值,无法直接
``start_timestamp >= now - window`` 范围查询。改成:取最近一条 + 插件侧时间窗判断。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from .result_formatter import extract_keywords, format_results_summary, serialize_results

if TYPE_CHECKING:
    from maibot_sdk import PluginContext

    from ..config import StorageSection
    from ..search_engines.base import SearchResult

logger = logging.getLogger(__name__)


def _coerce_to_timestamp(value: Any) -> float:
    """把 host 返回的 ``start_timestamp`` 字段值统一转为 unix 时间戳(float)。

    RPC 层可能把 datetime 序列化成 ISO 字符串、原始 datetime、float、int 任意一种,
    这里做防御性兼容。
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            # 兼容 "2026-05-02T10:00:00+00:00" / "2026-05-02 10:00:00"
            if "T" not in text and " " in text:
                text = text.replace(" ", "T", 1)
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            return 0.0
    return 0.0


class HistoryWriter:
    """将搜索结果写入 ChatHistory 表(经 ctx.db.query)。"""

    def __init__(self, ctx: "PluginContext", storage: "StorageSection") -> None:
        self._ctx = ctx
        self._cfg = storage

    async def record(
        self,
        *,
        chat_id: str,
        original_question: str,
        search_query: str,
        results: "list[SearchResult]",
        final_answer: str,
        source_type: str,
        last_success_engine: Optional[str],
    ) -> None:
        """记录一条搜索历史。

        Args:
            chat_id: 当前聊天流 ID(写入 session_id 字段)
            original_question: 用户原始问题
            search_query: 重写后的搜索词
            results: 搜索结果列表
            final_answer: LLM 总结输出
            source_type: ``search`` / ``direct_url``
            last_success_engine: 命中的引擎名
        """
        if not chat_id:
            logger.debug("chat_id 为空,跳过历史落库")
            return
        if not self._cfg.enable_store:
            logger.debug("enable_store=False,跳过历史落库")
            return

        try:
            now_ts = datetime.now().timestamp()
            theme = f"Web搜索: {search_query or original_question}"
            dedup_window = self._cfg.dedup_window_seconds

            # ---- 去重 workaround:取最近一条 + 插件侧时间窗判断 ---- #
            if dedup_window:
                hit = await self._is_recent_dup(chat_id, theme, now_ts, dedup_window)
                if hit:
                    logger.info("命中去重,跳过本次写入: theme=%s", theme)
                    return

            top_k = self._cfg.store_top_k
            keywords = extract_keywords(search_query or original_question)
            results_summary = format_results_summary(results, top_k)
            final_answer_text = (final_answer or "").strip()

            if final_answer_text and results_summary:
                summary = f"{final_answer_text}\n\n---\n\n{results_summary}"
            elif final_answer_text:
                summary = final_answer_text
            else:
                summary = results_summary or ""

            # 控制 original_messages 中最终回答的长度
            final_answer_for_text = final_answer_text
            max_final_len = self._cfg.final_answer_max_len
            if max_final_len and len(final_answer_for_text) > max_final_len:
                final_answer_for_text = final_answer_for_text[:max_final_len] + "…"

            serialized_results = serialize_results(results, top_k)
            original_messages_parts = [
                f"source_type: {source_type}",
                f"original_question: {original_question}",
                f"search_query: {search_query}",
                f"engine: {last_success_engine or 'unknown'}",
                f"final_answer: {final_answer_for_text}",
                f"results_json: {json.dumps(serialized_results, ensure_ascii=False)}",
            ]
            original_messages = "\n".join(original_messages_parts)

            # ---- 字段名转换:peewee 老字段 → SQLModel 新字段 ---- #
            now_iso = datetime.fromtimestamp(now_ts).isoformat()
            data = {
                "session_id": chat_id,
                "start_timestamp": now_iso,
                "end_timestamp": now_iso,
                "original_messages": original_messages,
                "participants": "web_search_plugin",
                "theme": theme,
                "keywords": json.dumps(keywords, ensure_ascii=False) if keywords else json.dumps([], ensure_ascii=False),
                "summary": summary or "网络搜索结果已记录",
            }
            await self._ctx.db.query("ChatHistory", query_type="create", data=data)
            logger.info("已写入搜索结果到 chat_history: theme=%s", theme)

        except Exception as exc:  # noqa: BLE001
            logger.error("记录搜索历史失败: %s", exc, exc_info=True)

    async def _is_recent_dup(
        self,
        chat_id: str,
        theme: str,
        now_ts: float,
        dedup_window: int,
    ) -> bool:
        """查询同一 chat_id+theme 的最近一条;若 start_timestamp 落在窗口内,则视为重复。"""
        try:
            row = await self._ctx.db.query(
                "ChatHistory",
                query_type="get",
                filters={"session_id": chat_id, "theme": theme},
                order_by=["-start_timestamp"],
                limit=1,
                single_result=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("去重查询失败,放弃去重: %s", exc)
            return False

        if not row or not isinstance(row, dict):
            return False
        last_ts = _coerce_to_timestamp(row.get("start_timestamp"))
        return last_ts >= now_ts - dedup_window
