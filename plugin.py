"""google_search_plugin — 新 SDK 版本主入口 (4.0.0 重写)

阶段 2 scaffolding:仅提供能加载、能渲染配置、能响应一个状态查询命令的最小骨架。
真实 web_search / abbreviation_translate / image_search 组件将在阶段 3-4 接入。
"""

from __future__ import annotations

import logging
from typing import Any

from maibot_sdk import Command, MaiBotPlugin

from .config import GoogleSearchPluginConfig

_LEGACY_VERSION_HINT = (
    "如需 v3.x 旧版,可在本插件仓库 checkout tag v3.2.0-legacy 获取。"
)


class GoogleSearchPlugin(MaiBotPlugin):
    """麦麦联网插件主类 (新 SDK 版)"""

    config_model = GoogleSearchPluginConfig

    async def on_load(self) -> None:
        """插件加载完成后的回调"""
        logger = self.ctx.logger
        cfg = self.config
        logger.info(
            "google_search_plugin v%s 已加载 (model=%s, default_engine=%s, image_search=%s)",
            cfg.plugin.version,
            cfg.models.model_name,
            cfg.search_backend.default_engine,
            cfg.actions.image_search_enabled,
        )
        logger.info(
            "阶段 2 scaffolding: 主流程 (web_search / abbreviation_translate / image_search) 尚未接入,稍后阶段 3-4 上线。"
        )

    async def on_unload(self) -> None:
        """插件卸载前的回调"""
        self.ctx.logger.info("google_search_plugin 已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        """配置热更新回调

        阶段 2 暂不做任何状态依赖的重建,仅记日志。阶段 3+ 引入 pipeline 实例后,
        在这里重新构造引擎链 / LLMRunner。
        """
        del config_data
        self.ctx.logger.info("收到配置更新事件: scope=%s version=%s", scope, version)

    # ---------------------------------------------------------------- #
    # Smoke-test command (阶段 2 专用,验证插件加载链路通畅)
    # ---------------------------------------------------------------- #

    @Command(
        "google_search_status",
        description="查询 google_search_plugin 当前加载状态与关键配置",
        pattern=r"^/google_search_status$",
    )
    async def handle_status(
        self,
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str, bool]:
        """阶段 2 临时命令:验证插件骨架已加载,真实功能阶段 3 起逐步上线"""
        del kwargs

        cfg = self.config
        enabled_engines = []
        e = cfg.engines
        if e.google_enabled:
            enabled_engines.append("google")
        if e.bing_enabled:
            enabled_engines.append("bing")
        if e.sogou_enabled:
            enabled_engines.append("sogou")
        if e.duckduckgo_enabled:
            enabled_engines.append("duckduckgo")
        if e.tavily_enabled:
            enabled_engines.append("tavily")
        if e.you_enabled:
            enabled_engines.append("you")
        if e.you_news_enabled:
            enabled_engines.append("you_news")

        lines = [
            f"google_search_plugin v{cfg.plugin.version} (新 SDK 重写,阶段 2 scaffolding)",
            f"模型 task: {cfg.models.model_name}  温度: {cfg.models.temperature}",
            f"默认引擎: {cfg.search_backend.default_engine}",
            f"启用引擎: {', '.join(enabled_engines) if enabled_engines else '(无)'}",
            f"图片搜索: {'已启用' if cfg.actions.image_search_enabled else '未启用'}",
            f"缩写翻译: {'已启用' if cfg.translation.enabled else '未启用'}",
            "",
            "⚠️  阶段 2 仅完成骨架,web_search / abbreviation_translate / image_search 三大组件",
            "    还未接入。请等待阶段 3-4 上线。",
            _LEGACY_VERSION_HINT,
        ]
        message = "\n".join(lines)

        if stream_id:
            await self.ctx.send.text(message, stream_id)
        return True, message, True


def create_plugin() -> GoogleSearchPlugin:
    """Runner 通过此工厂函数实例化插件"""
    return GoogleSearchPlugin()


# 给静态检查器一点提示:logging.getLogger 的 name 不需要以 "plugin." 开头,
# Runner 进程的 stdlib logger 会自动通过 IPC 桥到主进程。
_logger = logging.getLogger(__name__)
