import json
import logging
import os
import random
from typing import Any, Dict, List, Optional

import aiohttp

from .base import BaseSearchEngine, SearchResult, mask_api_key

logger = logging.getLogger(__name__)


class TavilyEngine(BaseSearchEngine):
    """Implementation of the Tavily search engine client."""

    BASE_URL = "https://api.tavily.com"
    SEARCH_ENDPOINT = "/search"

    search_depth: str
    include_raw_content: bool
    include_answer: bool
    topic: Optional[str]
    turbo: bool

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.api_keys = self._load_api_keys()
        self.search_depth = self.config.get("search_depth", "basic")
        self.include_raw_content = self.config.get("include_raw_content", True)
        self.include_answer = self.config.get("include_answer", True)

        topic_cfg = self.config.get("topic")
        if isinstance(topic_cfg, str):
            topic_cfg = topic_cfg.strip()
        self.topic = topic_cfg or None
        self.turbo = self.config.get("turbo", False)
        self.last_answer: Optional[str] = None

    async def search(self, query: str, num_results: int) -> List[SearchResult]:
        """Execute a search request via the Tavily API."""
        api_keys = self._iter_api_keys()
        if not api_keys:
            logger.warning("Tavily API key is not configured; skip Tavily search.")
            return []

        self.last_answer = None

        request_max_results = min(num_results if num_results > 0 else self.max_results, self.max_results)

        payload: Dict[str, Any] = {
            "query": query,
            "search_depth": self.search_depth,
            "max_results": request_max_results,
            "include_answer": self.include_answer,
            "include_raw_content": self.include_raw_content,
            "topic": self.topic,
            "turbo": self.turbo,
        }

        def _include_value(value: Any) -> bool:
            if value is None:
                return False
            if isinstance(value, str) and not value.strip():
                return False
            return True

        payload = {key: value for key, value in payload.items() if _include_value(value)}

        timeout = aiohttp.ClientTimeout(total=self.TIMEOUT)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        for api_key in api_keys:
            payload_with_key = dict(payload)
            payload_with_key["api_key"] = api_key

            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        f"{self.BASE_URL}{self.SEARCH_ENDPOINT}",
                        json=payload_with_key,
                        headers=headers,
                        proxy=self.proxy,
                    ) as response:
                        response_text = await response.text()
                        if response.status >= 400:
                            logger.error(
                                "Tavily search request failed with status %s for key %s; response body: %s",
                                response.status,
                                mask_api_key(api_key),
                                response_text,
                            )
                            continue

                        if not response_text:
                            logger.error("Tavily returned an empty response for key %s.", mask_api_key(api_key))
                            continue

                        try:
                            data = json.loads(response_text)
                        except json.JSONDecodeError:
                            logger.error(
                                "Failed to parse Tavily response as JSON for key %s: %s",
                                mask_api_key(api_key),
                                response_text,
                            )
                            continue

            except Exception as exc:
                logger.error(
                    "Tavily search raised an exception for key %s: %s",
                    mask_api_key(api_key),
                    exc,
                    exc_info=True,
                )
                continue

            if isinstance(data, dict):
                answer = data.get("answer")
                self.last_answer = answer.strip() if isinstance(answer, str) else None
            else:
                self.last_answer = None

            results_data = data.get("results", []) if isinstance(data, dict) else []
            results: List[SearchResult] = []

            for index, item in enumerate(results_data):
                if not isinstance(item, dict):
                    continue

                title = self.tidy_text(item.get("title", ""))
                url = item.get("url", "")
                if not title or not self._is_valid_url(url):
                    continue

                snippet_source = item.get("content") or item.get("snippet") or item.get("raw_content") or ""
                snippet = self.tidy_text(snippet_source)
                content = item.get("raw_content") or snippet

                results.append(
                    SearchResult(
                        title=title,
                        url=url,
                        snippet=snippet,
                        abstract=snippet,
                        rank=index,
                        content=content,
                    )
                )

            return results[: min(len(results), num_results)]

        return []

    def has_api_keys(self) -> bool:
        """Return True when at least one Tavily API key is available."""
        return bool(self.api_keys)

    def _load_api_keys(self) -> List[str]:
        """Collect the list of Tavily API keys from config and environment."""
        def _collect(value: Optional[Any]) -> List[str]:
            if isinstance(value, str):
                cleaned = value.strip()
                return [cleaned] if cleaned else []
            if isinstance(value, (list, tuple, set)):
                return [item.strip() for item in value if isinstance(item, str) and item.strip()]
            return []

        candidates: List[str] = (
            _collect(self.config.get("api_keys"))
            + _collect(self.config.get("api_key"))
            + _collect(os.environ.get("TAVILY_API_KEY"))
        )

        seen = set()
        unique_keys: List[str] = []
        for key in candidates:
            if key and key not in seen:
                seen.add(key)
                unique_keys.append(key)

        return unique_keys

    def _pick_api_key(self) -> Optional[str]:
        """Randomly select one Tavily API key to use for the request."""
        if not self.api_keys:
            return None
        return random.choice(self.api_keys)

    def _iter_api_keys(self) -> List[str]:
        """Return API keys in random order for retries."""
        if not self.api_keys:
            return []
        return random.sample(self.api_keys, k=len(self.api_keys))
