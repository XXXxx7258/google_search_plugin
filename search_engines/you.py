import logging
import os
import random
from typing import Any, Dict, List, Optional

import aiohttp

from .base import BaseSearchEngine, SearchResult

logger = logging.getLogger(__name__)


def _collect_api_keys(value: Optional[Any]) -> List[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, (list, tuple, set)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _load_you_api_keys(config: Dict[str, Any]) -> List[str]:
    candidates: List[str] = (
        _collect_api_keys(config.get("api_keys"))
        + _collect_api_keys(config.get("api_key"))
        + _collect_api_keys(os.environ.get("YOU_API_KEY"))
    )
    seen = set()
    unique_keys: List[str] = []
    for key in candidates:
        if key and key not in seen:
            seen.add(key)
            unique_keys.append(key)
    return unique_keys


class _YouApiKeyMixin:
    api_keys: List[str]

    def _init_api_keys(self, config: Dict[str, Any]) -> None:
        self.api_keys = _load_you_api_keys(config)

    def has_api_keys(self) -> bool:
        return bool(self.api_keys)

    def _pick_api_key(self) -> Optional[str]:
        if not self.api_keys:
            return None
        return random.choice(self.api_keys)


def _first_snippet(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item
        return ""
    if isinstance(value, str):
        return value
    return ""


def _pick_contents(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("markdown", "html"):
        content = value.get(key)
        if isinstance(content, str) and content.strip():
            return content
    return ""


class YouSearchEngine(BaseSearchEngine, _YouApiKeyMixin):
    """You.com search API client."""

    BASE_URL = "https://ydc-index.io"
    SEARCH_ENDPOINT = "/v1/search"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._init_api_keys(self.config)

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        api_key = self._pick_api_key()
        if not api_key:
            logger.warning("You Search API key is not configured; skip You search.")
            return []

        request_count = min(num_results if num_results > 0 else self.max_results, self.max_results)
        params: Dict[str, Any] = {
            "query": query,
            "count": request_count,
        }

        for key in (
            "freshness",
            "offset",
            "country",
            "language",
            "safesearch",
            "livecrawl",
            "livecrawl_formats",
        ):
            value = self.config.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            params[key] = value

        try:
            timeout = aiohttp.ClientTimeout(total=self.TIMEOUT)
            headers = {
                "Accept": "application/json",
                "X-API-Key": api_key,
            }
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.BASE_URL}{self.SEARCH_ENDPOINT}",
                    params=params,
                    headers=headers,
                    proxy=self.proxy,
                ) as response:
                    response_text = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "You Search request failed with status %s; response body: %s",
                            response.status,
                            response_text,
                        )
                        return []
                    if not response_text:
                        logger.error("You Search returned an empty response.")
                        return []
                    try:
                        data = await response.json()
                    except Exception:
                        logger.error("Failed to parse You Search response as JSON: %s", response_text)
                        return []
        except Exception as exc:
            logger.error("You Search raised an exception: %s", exc, exc_info=True)
            return []

        if not isinstance(data, dict):
            return []

        results_data = data.get("results") or {}
        web_items = results_data.get("web") if isinstance(results_data, dict) else None
        news_items = results_data.get("news") if isinstance(results_data, dict) else None

        results: List[SearchResult] = []

        if isinstance(web_items, list):
            for index, item in enumerate(web_items):
                if not isinstance(item, dict):
                    continue
                title = self.tidy_text(item.get("title", ""))
                url = item.get("url", "")
                if not title or not self._is_valid_url(url):
                    continue
                description = self.tidy_text(item.get("description", ""))
                snippet = self.tidy_text(_first_snippet(item.get("snippets"))) or description
                content = _pick_contents(item.get("contents"))
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        abstract=snippet or description,
                        rank=index,
                        content=content,
                    )
                )

        if isinstance(news_items, list):
            offset = len(results)
            for index, item in enumerate(news_items):
                if not isinstance(item, dict):
                    continue
                title = self.tidy_text(item.get("title", ""))
                url = item.get("url", "")
                if not title or not self._is_valid_url(url):
                    continue
                description = self.tidy_text(item.get("description", ""))
                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=description,
                        abstract=description,
                        rank=offset + index,
                        content="",
                    )
                )

        return results[: min(len(results), num_results)]


class YouLiveNewsEngine(BaseSearchEngine, _YouApiKeyMixin):
    """You.com live news API client (early access)."""

    BASE_URL = "https://api.ydc-index.io"
    NEWS_ENDPOINT = "/livenews"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._init_api_keys(self.config)

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        api_key = self._pick_api_key()
        if not api_key:
            logger.warning("You Live News API key is not configured; skip live news.")
            return []

        logger.warning("You Live News is early access; ensure the API key has access.")

        request_count = min(num_results if num_results > 0 else self.max_results, self.max_results)
        params = {
            "q": query,
            "count": request_count,
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self.TIMEOUT)
            headers = {
                "Accept": "application/json",
                "X-API-Key": api_key,
            }
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.BASE_URL}{self.NEWS_ENDPOINT}",
                    params=params,
                    headers=headers,
                    proxy=self.proxy,
                ) as response:
                    response_text = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "You Live News request failed with status %s; response body: %s",
                            response.status,
                            response_text,
                        )
                        return []
                    if not response_text:
                        logger.error("You Live News returned an empty response.")
                        return []
                    try:
                        data = await response.json()
                    except Exception:
                        logger.error("Failed to parse You Live News response as JSON: %s", response_text)
                        return []
        except Exception as exc:
            logger.error("You Live News raised an exception: %s", exc, exc_info=True)
            return []

        if not isinstance(data, dict):
            return []

        news_data = data.get("news") or {}
        items = news_data.get("results") if isinstance(news_data, dict) else None
        if not isinstance(items, list):
            return []

        results: List[SearchResult] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            title = self.tidy_text(item.get("title", ""))
            url = item.get("url", "")
            if not title or not self._is_valid_url(url):
                continue
            description = self.tidy_text(item.get("description", ""))
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=description,
                    abstract=description,
                    rank=index,
                    content="",
                )
            )

        return results[: min(len(results), num_results)]


class YouContentsClient(_YouApiKeyMixin):
    """You.com contents API client."""

    MAX_URLS_PER_REQUEST = 10
    BASE_URL = "https://ydc-index.io"
    CONTENTS_ENDPOINT = "/v1/contents"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.TIMEOUT = self.config.get("timeout", 10)
        self.proxy = self.config.get("proxy")
        self.format = self.config.get("format", "markdown")
        self.force = bool(self.config.get("force", False))
        self._init_api_keys(self.config)

    async def fetch_contents(self, urls: List[str]) -> Dict[str, str]:
        api_key = self._pick_api_key()
        if not api_key:
            logger.warning("You Contents API key is not configured; skip contents fetch.")
            return {}

        if not urls:
            return {}

        format_value = self.format if self.format in {"html", "markdown"} else "markdown"
        contents_map: Dict[str, str] = {}
        batch_size = self.MAX_URLS_PER_REQUEST

        for start in range(0, len(urls), batch_size):
            batch = [url for url in urls[start:start + batch_size] if url]
            if not batch:
                continue
            batch_map = await self._fetch_contents_batch(batch, format_value, api_key)
            if batch_map:
                contents_map.update(batch_map)

        return contents_map

    async def _fetch_contents_batch(
        self,
        urls: List[str],
        format_value: str,
        api_key: str,
    ) -> Dict[str, str]:
        payload = {"urls": urls, "format": format_value}

        try:
            timeout = aiohttp.ClientTimeout(total=self.TIMEOUT)
            headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-API-Key": api_key,
            }
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.BASE_URL}{self.CONTENTS_ENDPOINT}",
                    json=payload,
                    headers=headers,
                    proxy=self.proxy,
                ) as response:
                    response_text = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "You Contents request failed with status %s; response body: %s",
                            response.status,
                            response_text,
                        )
                        return {}
                    if not response_text:
                        logger.error("You Contents returned an empty response.")
                        return {}
                    try:
                        data = await response.json()
                    except Exception:
                        logger.error("Failed to parse You Contents response as JSON: %s", response_text)
                        return {}
        except Exception as exc:
            logger.error("You Contents raised an exception: %s", exc, exc_info=True)
            return {}

        if not isinstance(data, list):
            logger.error("Unexpected You Contents response type: %s", type(data))
            return {}

        contents_map: Dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url:
                continue
            content = item.get(format_value)
            if not isinstance(content, str) or not content:
                content = _pick_contents(item)
            if content:
                contents_map[url] = content

        return contents_map


class YouImagesEngine(BaseSearchEngine, _YouApiKeyMixin):
    """You.com images API client (early access)."""

    BASE_URL = "https://image-search.ydc-index.io"
    IMAGES_ENDPOINT = "/images"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self._init_api_keys(self.config)

    async def search_images(self, query: str, num_results: int) -> List[Dict[str, str]]:
        api_key = self._pick_api_key()
        if not api_key:
            logger.warning("You Images API key is not configured; skip image search.")
            return []

        logger.warning("You Images is early access; ensure the API key has access.")

        params = {
            "q": query,
        }

        try:
            timeout = aiohttp.ClientTimeout(total=self.TIMEOUT)
            headers = {
                "Accept": "application/json",
                "X-API-Key": api_key,
            }
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.BASE_URL}{self.IMAGES_ENDPOINT}",
                    params=params,
                    headers=headers,
                    proxy=self.proxy,
                ) as response:
                    response_text = await response.text()
                    if response.status >= 400:
                        logger.error(
                            "You Images request failed with status %s; response body: %s",
                            response.status,
                            response_text,
                        )
                        return []
                    if not response_text:
                        logger.error("You Images returned an empty response.")
                        return []
                    try:
                        data = await response.json()
                    except Exception:
                        logger.error("Failed to parse You Images response as JSON: %s", response_text)
                        return []
        except Exception as exc:
            logger.error("You Images raised an exception: %s", exc, exc_info=True)
            return []

        if not isinstance(data, dict):
            return []

        images_data = data.get("images") or {}
        items = images_data.get("results") if isinstance(images_data, dict) else None
        if not isinstance(items, list):
            return []

        results: List[Dict[str, str]] = []
        for item in items[:num_results]:
            if not isinstance(item, dict):
                continue
            image_url = item.get("image_url")
            if not isinstance(image_url, str) or not image_url:
                continue
            title = item.get("title") if isinstance(item.get("title"), str) else query
            page_url = item.get("page_url")
            if isinstance(page_url, str) and page_url:
                title = f"{title} ({page_url})" if title else page_url
            results.append(
                {
                    "image": image_url,
                    "title": title or query,
                    "thumbnail": image_url,
                }
            )

        return results
