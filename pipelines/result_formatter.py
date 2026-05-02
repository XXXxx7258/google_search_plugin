"""SearchResult 列表的格式化、序列化与关键词提取。

从老 plugin.py 的 ``_format_results_summary`` / ``_serialize_results`` /
``_extract_keywords`` 抽出。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..utils.keywords import parse_keywords_string

if TYPE_CHECKING:
    from ..search_engines.base import SearchResult


def format_results_summary(results: "list[SearchResult]", top_k: int) -> str:
    """格式化搜索结果用于 chat_history.summary 字段(写库用)。

    Args:
        results: 搜索结果列表
        top_k: 取前 K 条
    """
    if not results:
        return ""
    lines: list[str] = []
    for item in results[:top_k]:
        if not item:
            continue
        title = getattr(item, "title", "") or ""
        url = getattr(item, "url", "") or ""
        snippet = getattr(item, "snippet", "") or getattr(item, "abstract", "") or ""
        if title or url:
            lines.append(f"{title} - {url}".strip(" -"))
        if snippet:
            lines.append(f"摘要：{snippet}")
        lines.append("")
    return "\n".join(lines).strip()


def serialize_results(results: "list[SearchResult]", top_k: int) -> list[dict[str, str]]:
    """序列化搜索结果为可 JSON 化的字典列表(写库用)。"""
    serialized: list[dict[str, str]] = []
    if not results:
        return serialized
    for item in results[:top_k]:
        if not item:
            continue
        serialized.append(
            {
                "title": getattr(item, "title", "") or "",
                "url": getattr(item, "url", "") or "",
                "snippet": getattr(item, "snippet", "") or "",
                "abstract": getattr(item, "abstract", "") or "",
                "content": getattr(item, "content", "") or "",
            }
        )
    return serialized


def extract_keywords(text: str) -> list[str]:
    """从文本提取关键词(优先用 vendor 的 parse_keywords_string,失败回退到正则切分)"""
    if not text:
        return []
    try:
        kws = parse_keywords_string(text)
        if kws:
            return kws
    except Exception:
        pass
    try:
        return [kw for kw in re.split(r"[\s,;/]+", text) if kw]
    except Exception:
        return []
