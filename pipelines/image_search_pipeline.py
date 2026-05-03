"""图片搜索流水线。

职责:
- 维护 4 个图片搜索引擎(Bing / Sogou / DuckDuckGo / YouImages)
- 30 分钟内同 query 不重复发送同一张图片(dedup 缓存)
- 找到候选 → 下载 → base64,返回给调用方让它决定如何发送
"""

from __future__ import annotations

import asyncio
import base64
import logging
import random
import time
from collections import deque
from typing import TYPE_CHECKING, Literal, Optional

import aiohttp

from ..search_engines.bing import BingEngine
from ..search_engines.duckduckgo import DuckDuckGoEngine
from ..search_engines.sogou import SogouEngine
from ..search_engines.you import YouImagesEngine
from .engine_chain import _build_common_cfg, _build_engine_dict

if TYPE_CHECKING:
    from ..config import EnginesSection, SearchBackendSection

logger = logging.getLogger(__name__)

# 状态码:plugin handler 据此决定 user-facing 文案
ImageSearchStatus = Literal["ok", "no_results", "no_unique", "all_failed"]


class ImageSearchPipeline:
    """图片搜索流水线"""

    def __init__(
        self,
        *,
        engines_cfg: "EnginesSection",
        backend_cfg: "SearchBackendSection",
    ) -> None:
        self._engines_cfg = engines_cfg
        self._backend_cfg = backend_cfg

        common = _build_common_cfg(backend_cfg)
        self.bing = BingEngine(_build_engine_dict("bing", engines_cfg, common))
        self.sogou = SogouEngine(_build_engine_dict("sogou", engines_cfg, common))
        self.duckduckgo = DuckDuckGoEngine(_build_engine_dict("duckduckgo", engines_cfg, common))
        self.you_images = YouImagesEngine(_build_engine_dict("you_images", engines_cfg, common))

        # 30 分钟去重:每个 query 一个 deque,(url, ts)
        self._image_history: dict[str, deque[tuple[str, float]]] = {}
        # 单 query 缓存大小:max_results × 3 但下限 30
        max_results = max(self._backend_cfg.max_results or 0, 0)
        self._image_history_max_size: int = max(30, max_results * 3) if max_results > 0 else 30
        self._image_repeat_window_seconds: int = 30 * 60
        # 顶层 query 数量上限,防止长期运行下 dict 单调增长(每次清理过期 query 时检查)
        self._max_distinct_queries: int = 200
        self.last_engine: Optional[str] = None

    def _evict_stale_queries(self, now: float) -> None:
        """清理 30 分钟内零活跃的 query 条目。

        每条 query 的 deque 内是 (url, ts);ts 都早于窗口 → 整条 query 可丢。
        """
        window = self._image_repeat_window_seconds
        stale = [
            q
            for q, hist in self._image_history.items()
            if not hist or now - hist[-1][1] > window
        ]
        for q in stale:
            self._image_history.pop(q, None)
        # 顶层兜底:即使没全部过期,如果 query 数超阈值,按最旧 ts 淘汰至阈值
        if len(self._image_history) > self._max_distinct_queries:
            sorted_by_age = sorted(
                self._image_history.items(),
                key=lambda kv: kv[1][-1][1] if kv[1] else 0.0,
            )
            for q, _ in sorted_by_age[: -self._max_distinct_queries]:
                self._image_history.pop(q, None)

    async def find_unique_image_b64(
        self,
        query: str,
    ) -> tuple[ImageSearchStatus, Optional[str], Optional[str]]:
        """搜索 + 去重 + 下载 + base64。

        Args:
            query: 关键词

        Returns:
            ``(status, base64, picked_url)``:

            - ``("ok", "<base64>", "<url>")`` 找到并下载成功
            - ``("no_results", None, None)`` 所有引擎都没返回结果
            - ``("no_unique", None, None)`` 所有候选都在 30 分钟内发送过
            - ``("all_failed", None, None)`` 候选有,但下载全失败
        """
        # ---- 1. 引擎链 fallback 搜索 ---- #
        image_results = await self._search_with_fallback(query)
        if not image_results:
            return ("no_results", None, None)

        # ---- 2. 提取 URL + 去重 ---- #
        image_urls: list[str] = []
        seen: set[str] = set()
        for item in image_results:
            url = item.get("image") if isinstance(item, dict) else None
            if not url or url in seen:
                continue
            seen.add(url)
            image_urls.append(url)
        if not image_urls:
            return ("no_results", None, None)

        history = self._image_history.get(query)
        if history is None:
            # 新 query 接入前先清理过期条目,避免长期运行下 dict 单调增长
            self._evict_stale_queries(time.time())
            history = deque(maxlen=self._image_history_max_size)
            self._image_history[query] = history

        now = time.time()
        recent_urls = {
            url
            for url, ts in history
            if now - ts < self._image_repeat_window_seconds
        }
        candidates = [u for u in image_urls if u not in recent_urls]
        if not candidates:
            return ("no_unique", None, None)

        random.shuffle(candidates)

        # ---- 3. 依次下载 ---- #
        async with aiohttp.ClientSession(trust_env=True) as session:
            for url in candidates:
                if not url:
                    continue
                image_data = await self._fetch_image(session, url)
                if image_data:
                    b64 = base64.b64encode(image_data).decode("utf-8")
                    history.append((url, time.time()))
                    return ("ok", b64, url)
        return ("all_failed", None, None)

    # ------------------------------------------------------------------ #
    # 内部:多引擎 fallback 搜索
    # ------------------------------------------------------------------ #

    async def _search_with_fallback(self, query: str) -> list[dict[str, str]]:
        """按 YouImages → Bing → Sogou → DuckDuckGo 顺序尝试。"""
        engines_cfg = self._engines_cfg
        num_results = self._backend_cfg.max_results

        engines: list[tuple[str, str, object]] = [
            ("you_images", "You Images", self.you_images),
            ("bing", "Bing", self.bing),
            ("sogou", "搜狗", self.sogou),
            ("duckduckgo", "DuckDuckGo", self.duckduckgo),
        ]

        for engine_key, display_name, engine in engines:
            # 引擎启用检查
            is_enabled = getattr(engines_cfg, f"{engine_key}_enabled", None)
            if is_enabled is None:
                # bing / sogou / duckduckgo 默认启用,you_images 默认禁用
                is_enabled = engine_key != "you_images"
            if not is_enabled:
                logger.info("%s 图片搜索已禁用,跳过", display_name)
                continue

            # YouImages 还需 API key
            if engine_key == "you_images" and hasattr(engine, "has_api_keys"):
                if not engine.has_api_keys():
                    logger.info("%s 未配置 API key,跳过", display_name)
                    continue

            try:
                logger.info("尝试 %s 搜索图片: %s", display_name, query)
                image_results = await engine.search_images(query, num_results)  # type: ignore[attr-defined]
                if image_results:
                    logger.info("%s 图片搜索成功,找到 %d 张", display_name, len(image_results))
                    self.last_engine = engine_key
                    return list(image_results)
                logger.info("%s 未找到结果,尝试下一个", display_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s 图片搜索失败: %s", display_name, exc)
                continue

        return []

    @staticmethod
    async def _fetch_image(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    return await response.read()
        except asyncio.TimeoutError:
            logger.warning("下载图片超时: %s", url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("下载图片失败 %s: %s", url, exc)
        return None
