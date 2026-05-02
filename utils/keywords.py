"""关键词解析(从 src/chat/utils/utils.py:parse_keywords_string vendor 而来)

新 SDK 禁止 ``from src.*`` 导入,这里把唯一用到的工具函数本地化。
"""

from __future__ import annotations

import ast
import json
from typing import Any


def parse_keywords_string(keywords_input: Any) -> list[str]:
    """统一的关键词解析函数,支持多种格式的关键词字符串解析。

    支持的格式:
    1. 字符串列表格式: ``'["foo", "bar", "baz"]'``
    2. 斜杠分隔: ``'foo/bar/baz'``
    3. 逗号分隔: ``'foo,bar,baz'``
    4. 空格分隔: ``'foo bar baz'``
    5. 已经是 list: ``["foo", "bar", "baz"]``
    6. JSON 对象: ``'{"keywords": ["foo", "bar", "baz"]}'``

    Args:
        keywords_input: 关键词输入,可以是字符串或列表

    Returns:
        list[str]: 解析后的关键词列表,去除空白项
    """
    if not keywords_input:
        return []

    # 已经是 list 直接处理
    if isinstance(keywords_input, list):
        return [str(k).strip() for k in keywords_input if str(k).strip()]

    keywords_str = str(keywords_input).strip()
    if not keywords_str:
        return []

    # 1. JSON 对象 / 数组
    try:
        json_data = json.loads(keywords_str)
        if isinstance(json_data, dict) and "keywords" in json_data:
            keywords_list = json_data["keywords"]
            if isinstance(keywords_list, list):
                return [str(k).strip() for k in keywords_list if str(k).strip()]
        elif isinstance(json_data, list):
            return [str(k).strip() for k in json_data if str(k).strip()]
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Python literal (e.g. 单引号 list)
    try:
        parsed = ast.literal_eval(keywords_str)
        if isinstance(parsed, list):
            return [str(k).strip() for k in parsed if str(k).strip()]
    except (ValueError, SyntaxError):
        pass

    # 3. 各种分隔符
    for separator in ("/", ",", " ", "|", ";"):
        if separator in keywords_str:
            keywords_list = [k.strip() for k in keywords_str.split(separator) if k.strip()]
            if len(keywords_list) > 1:
                return keywords_list

    return [keywords_str] if keywords_str else []
