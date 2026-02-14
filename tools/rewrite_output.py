import json
import re
from typing import Optional, Tuple


ALLOWED_TAVILY_TOPICS = frozenset({"general", "news"})
_DECODER = json.JSONDecoder()


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else text


def _try_parse_rewrite_payload(text: str) -> Optional[dict]:
    if not text:
        return None

    start = 0
    while True:
        index = text.find("{", start)
        if index < 0:
            return None

        try:
            obj, _ = _DECODER.raw_decode(text[index:])
        except json.JSONDecodeError:
            start = index + 1
            continue

        if not isinstance(obj, dict):
            start = index + 1
            continue

        if any(key in obj for key in ("query", "tavily_topic", "topic")):
            return obj

        start = index + 1


def parse_rewrite_output(raw: str) -> Tuple[str, Optional[str]]:
    """Parse LLM rewrite output.

    Supported formats:
    - Plain string: treated as query.
    - JSON / fenced JSON: {"query": "...", "tavily_topic": "news|general|"}.
    """
    if raw is None:
        return "", None

    raw_str = str(raw).strip()
    if not raw_str:
        return "", None

    text = _strip_code_fence(raw_str)
    data = _try_parse_rewrite_payload(text)
    if data:
        query = data.get("query", "")
        if not isinstance(query, str):
            query = str(query)
        query = query.strip()

        topic = data.get("tavily_topic") or data.get("topic") or None
        if isinstance(topic, str):
            topic_norm = topic.strip().lower()
        else:
            topic_norm = ""
        topic_out = topic_norm if topic_norm in ALLOWED_TAVILY_TOPICS else None

        return query, topic_out

    return raw_str, None
