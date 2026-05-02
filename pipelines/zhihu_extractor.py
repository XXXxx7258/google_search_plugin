"""知乎专用抓取与 initialState 解析。

从老 plugin.py 的 ``_fetch_zhihu_content`` / ``_build_zhihu_request_profiles`` /
``_request_zhihu_page`` / ``_extract_zhihu_*`` 系列抽出。

设计原则:这是一个独立的"零 src 依赖"模块,只用 curl_cffi + bs4 + stdlib。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def is_zhihu_url(url: str) -> bool:
    """判断 URL 是否属于知乎站点。"""
    if not url:
        return False
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return hostname in {"www.zhihu.com", "zhihu.com", "zhuanlan.zhihu.com"}


class ZhihuExtractor:
    """知乎专用抓取器(走 curl_cffi + 浏览器指纹)。"""

    def __init__(
        self,
        *,
        zhihu_cookies: str,
        content_timeout: int = 10,
        max_content_length: int = 3000,
        proxy: str = "",
    ) -> None:
        self._cookies = (zhihu_cookies or "").strip()
        self._timeout = content_timeout
        self._max_length = max_content_length
        self._proxy = proxy or ""

    @property
    def enabled(self) -> bool:
        return bool(self._cookies)

    async def fetch(self, url: str) -> Optional[str]:
        """抓取知乎页面内容。

        Args:
            url: 知乎页面 URL

        Returns:
            提取到的正文(已截断到 max_content_length);失败时 None
        """
        if not self.enabled:
            logger.info("[zhihu] cookies 未配置,跳过 %s", url)
            return None

        for profile_name, profile_url, headers, impersonate in self._build_profiles(url):
            try:
                response_ctx = await self._request(profile_url, headers=headers, impersonate=impersonate)
            except Exception as exc:  # noqa: BLE001
                logger.info("[zhihu] %s 请求失败: %s", profile_name, exc)
                continue

            html_text = str(response_ctx.get("text") or "")
            final_url = str(response_ctx.get("final_url") or profile_url)
            status_code = int(response_ctx.get("status_code") or 0)

            if self._is_challenge_page(html_text, status_code):
                logger.info("[zhihu] %s 命中风控页: %s -> %s", profile_name, profile_url, final_url)
                continue

            if self._is_login_page(final_url, html_text):
                logger.info("[zhihu] %s 重定向到登录页: %s -> %s", profile_name, profile_url, final_url)
                continue

            initial_data = self._extract_initial_data(html_text)
            if not initial_data:
                logger.info("[zhihu] %s initialData 缺失: %s -> %s", profile_name, profile_url, final_url)
                continue

            content = self._extract_content_from_initial_data(profile_url, initial_data)
            if content:
                return content[: self._max_length]

            logger.info("[zhihu] %s 目标实体未找到: %s -> %s", profile_name, profile_url, final_url)

        return None

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _build_profiles(self, url: str) -> list[tuple[str, str, dict[str, str], str]]:
        base = {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            "referer": "https://www.zhihu.com/",
            "origin": "https://www.zhihu.com",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "cookie": self._cookies,
        }
        return [
            (
                "desktop",
                url,
                {
                    **base,
                    "user-agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                },
                "chrome",
            ),
            (
                "ios",
                url,
                {
                    **base,
                    "user-agent": (
                        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
                        "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"
                    ),
                },
                "safari_ios",
            ),
            (
                "mobile",
                url,
                {
                    **base,
                    "user-agent": (
                        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
                    ),
                },
                "chrome_android",
            ),
        ]

    async def _request(
        self,
        url: str,
        *,
        headers: dict[str, str],
        impersonate: str,
    ) -> dict[str, Any]:
        from curl_cffi import requests as curl_requests

        proxy = self._proxy or None

        def do_request() -> Any:
            return curl_requests.get(
                url,
                headers=headers,
                impersonate=impersonate,
                proxies={"https": proxy, "http": proxy} if proxy else None,
                timeout=self._timeout,
                allow_redirects=True,
            )

        response = await asyncio.to_thread(do_request)
        return {
            "status_code": int(response.status_code),
            "final_url": str(response.url),
            "text": str(response.text),
        }

    @staticmethod
    def _is_challenge_page(html_text: str, status_code: int) -> bool:
        lowered = html_text.lower()
        return (
            'id="zh-zse-ck"' in lowered
            or "static.zhihu.com/zse-ck/" in lowered
            or 'appname":"zse_ck"' in lowered
            or (status_code == 403 and "zse-ck" in lowered)
        )

    @staticmethod
    def _is_login_page(final_url: str, html_text: str) -> bool:
        lowered_url = final_url.lower()
        lowered_html = html_text.lower()
        return (
            "/signin" in lowered_url
            or "/signup" in lowered_url
            or "<title>知乎 - 有问题，就会有答案</title>" in lowered_html
        )

    @staticmethod
    def _extract_initial_data(html_text: str) -> Optional[dict[str, Any]]:
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one('script#js-initialData[type="text/json"]')
        if node is None:
            return None
        raw = node.get_text(strip=True)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        return payload if isinstance(payload.get("initialState"), dict) else None

    def _extract_content_from_initial_data(self, url: str, initial_data: dict[str, Any]) -> Optional[str]:
        article_match = re.search(r"zhuanlan\.zhihu\.com/p/(?P<article_id>\d+)", url)
        if article_match:
            return self._extract_article(article_match.group("article_id"), initial_data)

        answer_match = re.search(r"zhihu\.com/question/(?P<question_id>\d+)/answer/(?P<answer_id>\d+)", url)
        if answer_match:
            return self._extract_answer(
                answer_match.group("question_id"),
                answer_match.group("answer_id"),
                initial_data,
            )

        question_match = re.search(r"zhihu\.com/question/(?P<question_id>\d+)(?:[/?#]|$)", url)
        if question_match:
            return self._extract_question(question_match.group("question_id"), initial_data)

        return None

    def _extract_article(self, article_id: str, initial_data: dict[str, Any]) -> Optional[str]:
        article = (self._entities(initial_data).get("articles") or {}).get(article_id) or {}
        if not isinstance(article, dict):
            return None
        return self._join_parts(
            str(article.get("title") or "").strip(),
            self._extract_text_from_html(str(article.get("content") or "")),
        )

    def _extract_answer(
        self,
        question_id: str,
        answer_id: str,
        initial_data: dict[str, Any],
    ) -> Optional[str]:
        entities = self._entities(initial_data)
        question = (entities.get("questions") or {}).get(question_id) or {}
        answer = (entities.get("answers") or {}).get(answer_id) or {}
        if not isinstance(answer, dict):
            return None
        return self._join_parts(
            str(question.get("title") or "").strip(),
            self._extract_text_from_html(str(answer.get("content") or "")),
        )

    def _extract_question(self, question_id: str, initial_data: dict[str, Any]) -> Optional[str]:
        entities = self._entities(initial_data)
        question = (entities.get("questions") or {}).get(question_id) or {}
        if not isinstance(question, dict):
            return None

        initial_state = initial_data.get("initialState") or {}
        answer_map = ((initial_state.get("question") or {}).get("answers") or {}).get(question_id) or {}
        answer_ids = answer_map.get("ids") or []
        first_answer_id = None
        if answer_ids:
            first_entry = answer_ids[0]
            if isinstance(first_entry, dict):
                first_answer_id = first_entry.get("target")
            else:
                first_answer_id = first_entry

        answer: dict[str, Any] = {}
        if first_answer_id:
            answer = (entities.get("answers") or {}).get(str(first_answer_id)) or {}

        return self._join_parts(
            str(question.get("title") or "").strip(),
            self._extract_text_from_html(str(question.get("detail") or "")),
            self._extract_text_from_html(str(answer.get("content") or "")) if isinstance(answer, dict) else "",
        )

    @staticmethod
    def _extract_text_from_html(html_text: str) -> str:
        if not html_text:
            return ""
        soup = BeautifulSoup(html_text, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True).strip()

    @staticmethod
    def _join_parts(*parts: str) -> Optional[str]:
        normalized = [p.strip() for p in parts if isinstance(p, str) and p.strip()]
        if not normalized:
            return None
        return "\n\n".join(normalized)

    @staticmethod
    def _entities(initial_data: dict[str, Any]) -> dict[str, Any]:
        initial_state = initial_data.get("initialState") or {}
        entities = initial_state.get("entities") or {}
        return entities if isinstance(entities, dict) else {}
