import json
import re
from typing import Optional, Tuple


_ALLOWED_TAVILY_TOPICS = {"general", "news"}


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        return text
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else text


def _extract_first_json_object(text: str) -> Optional[str]:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        ch = text[index]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1].strip()
    return None


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
    json_text = _extract_first_json_object(text) if "{" in text else None
    if json_text:
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            return raw_str, None

        if isinstance(data, dict):
            query = data.get("query", "")
            if not isinstance(query, str):
                query = str(query)
            query = query.strip()

            topic = data.get("tavily_topic") or data.get("topic") or None
            if isinstance(topic, str):
                topic_norm = topic.strip().lower()
            else:
                topic_norm = ""
            topic_out = topic_norm if topic_norm in _ALLOWED_TAVILY_TOPICS else None

            return query, topic_out

    return raw_str, None
