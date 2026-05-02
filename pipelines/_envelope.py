"""RPC envelope 防御性剥皮工具。

新版 SDK / Runner 在某些 capability 调用上返回双层 envelope:

    {"success": True, "result": {"success": True, "messages": [...]}}

而 SDK 的 ``_normalize_capability_result`` 只在最外层找已知 key
(``messages`` / ``response`` / ...)。找不到就原样返回外层 dict,导致
插件代码拿到一个非预期的 dict 而非真正的数据。

本模块提供一个递归剥皮函数,把任意层数的 ``{success, result}`` 信封
脱掉,直到拿到内层有效 dict / list / 标量。
"""

from __future__ import annotations

from typing import Any

# 一个 envelope 层的判定:dict 同时拥有 success + result
_ENVELOPE_KEYS = frozenset({"success", "result"})


def peel_envelope(result: Any, *, max_depth: int = 4) -> Any:
    """递归脱掉 ``{"success": ..., "result": <inner>}`` 信封。

    Args:
        result: 任意 capability 返回值
        max_depth: 最多剥几层(防御性,正常 1-2 层就够)

    Returns:
        Any: 剥到底的内层值;不是 envelope 时原样返回
    """
    for _ in range(max_depth):
        if not isinstance(result, dict):
            return result
        if "result" not in result or "success" not in result:
            return result
        inner = result["result"]
        # 内层必须是 dict / list / 标量等"实际数据",才认为是有意义的剥皮
        if inner is None:
            return result
        result = inner
    return result
