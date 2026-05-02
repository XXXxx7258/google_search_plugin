"""LLM 调用包装。

从老 plugin.py 的 ``_call_llm`` 抽出,改用新 SDK 的 ``ctx.llm.generate``。

**核心修复**:必须显式传 ``model=`` 参数,否则 host 端 ``resolve_task_name("")``
会按字母序回退到 ``embedding`` task,导致 400 错误(诊断报告 Bug C)。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maibot_sdk import PluginContext

    from ..config import ModelsSection

logger = logging.getLogger(__name__)


class LLMRunner:
    """简单的 LLM 调用包装器。

    持有 ``ctx`` + ``ModelsSection`` 配置,封装"传 prompt → 拿 string"的流程。
    """

    def __init__(self, ctx: "PluginContext", model_config: "ModelsSection") -> None:
        self._ctx = ctx
        self._config = model_config

    async def generate(self, prompt: str) -> str:
        """生成文本。

        Args:
            prompt: 完整 prompt 字符串

        Returns:
            str: LLM 响应文本;失败时返回空字符串(调用方需要兜底)
        """
        if not prompt or not prompt.strip():
            logger.warning("prompt 为空,跳过 LLM 调用")
            return ""

        try:
            target_model = str(self._config.model_name or "replyer")
            temperature = self._config.temperature
            logger.info("调用 ctx.llm.generate, model=%s temperature=%s", target_model, temperature)

            result = await self._ctx.llm.generate(
                prompt=prompt,
                model=target_model,            # ← 必须显式传,否则落到 embedding
                temperature=temperature,
            )
        except Exception as exc:
            logger.error("ctx.llm.generate 抛异常: %s", exc, exc_info=True)
            return ""

        if not isinstance(result, dict):
            logger.warning("ctx.llm.generate 返回非 dict: %r", type(result))
            return ""

        if not result.get("success"):
            err = result.get("error") or result.get("response") or "<no error message>"
            logger.error("LLM 调用失败 (model=%s): %s", target_model, err)
            return ""

        response_text = result.get("response", "") or ""
        return str(response_text).strip()
