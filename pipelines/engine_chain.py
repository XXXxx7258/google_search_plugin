"""多引擎搜索 fallback 链。

从老 plugin.py 的 ``_initialize_engines`` / ``_build_engine_config`` /
``_search_with_fallback`` 抽出。

EngineChain 持有所有引擎实例 + 当次搜索的状态(last_success_engine /
last_tavily_answer)。SearchPipeline 通过它发起搜索。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from ..search_engines.bing import BingEngine
from ..search_engines.duckduckgo import DuckDuckGoEngine
from ..search_engines.google import GoogleEngine
from ..search_engines.sogou import SogouEngine
from ..search_engines.tavily import TavilyEngine
from ..search_engines.you import (
    YouContentsClient,
    YouLiveNewsEngine,
    YouSearchEngine,
)

if TYPE_CHECKING:
    from ..config import EnginesSection, SearchBackendSection
    from ..search_engines.base import SearchResult

logger = logging.getLogger(__name__)


def _build_common_cfg(backend: "SearchBackendSection") -> dict[str, Any]:
    return {
        "timeout": backend.timeout,
        "proxy": backend.proxy or None,
        "max_results": backend.max_results,
    }


def _build_engine_dict(
    engine_name: str,
    engines: "EnginesSection",
    common: dict[str, Any],
) -> dict[str, Any]:
    """根据 engine_name 从 EnginesSection 提取该引擎需要的配置 dict。"""
    cfg: dict[str, Any] = {**common}
    if engine_name == "google":
        cfg.update({"enabled": engines.google_enabled, "language": engines.google_language})
    elif engine_name == "bing":
        cfg.update({"enabled": engines.bing_enabled, "region": engines.bing_region})
    elif engine_name == "sogou":
        cfg.update({"enabled": engines.sogou_enabled})
    elif engine_name == "duckduckgo":
        timelimit = engines.duckduckgo_timelimit
        cfg.update(
            {
                "enabled": engines.duckduckgo_enabled,
                "region": engines.duckduckgo_region,
                "backend": engines.duckduckgo_backend,
                "safesearch": engines.duckduckgo_safesearch,
                "timelimit": None if timelimit in ("", "none") else timelimit,
            }
        )
    elif engine_name == "tavily":
        cfg.update(
            {
                "enabled": engines.tavily_enabled,
                "api_keys": list(engines.tavily_api_keys),
                "api_key": engines.tavily_api_key,
                "search_depth": engines.tavily_search_depth,
                "include_raw_content": engines.tavily_include_raw_content,
                "include_answer": engines.tavily_include_answer,
                "topic": engines.tavily_topic,
                "turbo": engines.tavily_turbo,
            }
        )
    elif engine_name == "you":
        cfg.update(
            {
                "enabled": engines.you_enabled,
                "api_keys": list(engines.you_api_keys),
                "api_key": engines.you_api_key,
                "freshness": engines.you_freshness,
                "offset": engines.you_offset,
                "country": engines.you_country,
                "language": engines.you_language,
                "safesearch": engines.you_safesearch,
                "livecrawl": engines.you_livecrawl,
                "livecrawl_formats": engines.you_livecrawl_formats,
            }
        )
    elif engine_name == "you_news":
        cfg.update(
            {
                "enabled": engines.you_news_enabled,
                "api_keys": list(engines.you_api_keys),
                "api_key": engines.you_api_key,
            }
        )
    elif engine_name == "you_contents":
        cfg.update(
            {
                "enabled": engines.you_contents_enabled,
                "api_keys": list(engines.you_api_keys),
                "api_key": engines.you_api_key,
                "format": engines.you_contents_format,
                "force": engines.you_contents_force,
            }
        )
    return cfg


class EngineChain:
    """多引擎搜索 fallback 链"""

    def __init__(self, engines: "EnginesSection", backend: "SearchBackendSection") -> None:
        self._engines_cfg = engines
        self._backend_cfg = backend

        common = _build_common_cfg(backend)
        # content_timeout 用于内容抓取(you_contents)
        contents_common = {**common, "timeout": backend.content_timeout or common["timeout"]}

        self.google = GoogleEngine(_build_engine_dict("google", engines, common))
        self.bing = BingEngine(_build_engine_dict("bing", engines, common))
        self.sogou = SogouEngine(_build_engine_dict("sogou", engines, common))
        self.duckduckgo = DuckDuckGoEngine(_build_engine_dict("duckduckgo", engines, common))
        self.tavily = TavilyEngine(_build_engine_dict("tavily", engines, common))
        self.you = YouSearchEngine(_build_engine_dict("you", engines, common))
        self.you_news = YouLiveNewsEngine(_build_engine_dict("you_news", engines, common))
        self.you_contents = YouContentsClient(_build_engine_dict("you_contents", engines, contents_common))

        self.last_success_engine: Optional[str] = None
        self.last_tavily_answer: Optional[str] = None

    async def search_with_fallback(
        self,
        query: str,
        num_results: int,
        *,
        tavily_topic: Optional[str] = None,
    ) -> "list[SearchResult]":
        """带降级的搜索。

        Args:
            query: 搜索关键词
            num_results: 期望的结果数量
            tavily_topic: 可选的 Tavily topic 覆写(general/news)

        Returns:
            搜索结果列表;所有引擎都失败时返回空列表
        """
        engines_cfg = self._engines_cfg
        default_engine = self._backend_cfg.default_engine

        # 引擎优先级:tavily / you 系列优先(质量较高的 API 引擎),其余兜底
        all_engines: list[tuple[str, Any]] = [
            ("tavily", self.tavily),
            ("you", self.you),
            ("you_news", self.you_news),
            ("google", self.google),
            ("bing", self.bing),
            ("duckduckgo", self.duckduckgo),
            ("sogou", self.sogou),
        ]
        if default_engine in {pair[0] for pair in all_engines}:
            ordered = [pair for pair in all_engines if pair[0] == default_engine]
            ordered.extend(pair for pair in all_engines if pair[0] != default_engine)
        else:
            ordered = all_engines

        # 各引擎默认开关(没有显式配置时)
        defaults = {"google": False, "tavily": False, "you": False, "you_news": False}

        for engine_name, engine in ordered:
            is_enabled = getattr(engines_cfg, f"{engine_name}_enabled", None)
            if is_enabled is None:
                is_enabled = defaults.get(engine_name, True)
            if not is_enabled:
                logger.info("引擎 %s 已禁用,跳过", engine_name)
                continue

            # 需 API key 的引擎,无 key 直接跳过
            if engine_name in {"tavily", "you", "you_news"} and hasattr(engine, "has_api_keys"):
                if not engine.has_api_keys():
                    logger.info("%s 未配置 API key,跳过", engine_name)
                    continue

            try:
                if engine_name == "tavily":
                    results = await engine.search(query, num_results, topic=tavily_topic)  # type: ignore[call-arg]
                else:
                    results = await engine.search(query, num_results)
                if results:
                    logger.info("%s 搜索成功,返回 %d 条", engine_name, len(results))
                    self.last_success_engine = engine_name
                    self.last_tavily_answer = (
                        getattr(engine, "last_answer", None) if engine_name == "tavily" else None
                    )
                    return results
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 搜索失败: %s", engine_name, exc)

        return []
