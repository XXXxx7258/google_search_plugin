"""google_search_plugin — 新 SDK 版本主入口 (4.0.0 重写)

阶段 3:接入 web_search Tool。abbreviation_translate / image_search 留待阶段 4。

业务逻辑全部抽到 ``pipelines/`` 子模块,本文件只负责:
- ``MaiBotPlugin`` 子类骨架
- 生命周期 hook(在 on_load 装配 pipelines)
- ``@Tool("web_search")`` handler 派发到 SearchPipeline / UrlPipeline
- ``/google_search_status`` 临时诊断命令
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from maibot_sdk import Command, MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .config import GoogleSearchPluginConfig
from .pipelines.content_fetcher import ContentFetcher
from .pipelines.engine_chain import EngineChain
from .pipelines.history_writer import HistoryWriter
from .pipelines.llm_runner import LLMRunner
from .pipelines.search_pipeline import SearchPipeline
from .pipelines.url_pipeline import UrlPipeline, is_url
from .pipelines.zhihu_extractor import ZhihuExtractor

_LEGACY_VERSION_HINT = (
    "如需 v3.x 旧版,可在本插件仓库 checkout tag v3.2.0-legacy 获取。"
)


class GoogleSearchPlugin(MaiBotPlugin):
    """麦麦联网插件主类 (新 SDK 版)"""

    config_model = GoogleSearchPluginConfig

    # 运行时组件,在 on_load / on_config_update 中装配
    _engine_chain: Optional[EngineChain]
    _content_fetcher: Optional[ContentFetcher]
    _history_writer: Optional[HistoryWriter]
    _llm_runner: Optional[LLMRunner]
    _search_pipeline: Optional[SearchPipeline]
    _url_pipeline: Optional[UrlPipeline]

    def __init__(self) -> None:
        super().__init__()
        self._engine_chain = None
        self._content_fetcher = None
        self._history_writer = None
        self._llm_runner = None
        self._search_pipeline = None
        self._url_pipeline = None

    # ---------------------------------------------------------------- #
    # 生命周期
    # ---------------------------------------------------------------- #

    async def on_load(self) -> None:
        """插件加载完成后装配所有 pipeline 组件。"""
        self._build_pipelines()
        cfg = self.config
        self.ctx.logger.info(
            "google_search_plugin v%s 已加载 (model=%s, default_engine=%s, image_search=%s)",
            cfg.plugin.version,
            cfg.models.model_name,
            cfg.search_backend.default_engine,
            cfg.actions.image_search_enabled,
        )

    async def on_unload(self) -> None:
        self.ctx.logger.info("google_search_plugin 已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        """配置热更新:简单粗暴重建所有 pipeline 组件。"""
        del config_data
        self.ctx.logger.info("配置更新事件: scope=%s version=%s,重建 pipelines", scope, version)
        try:
            self._build_pipelines()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("重建 pipelines 失败: %s", exc, exc_info=True)

    def _build_pipelines(self) -> None:
        """从 self.config 装配 EngineChain / ContentFetcher / Pipelines。"""
        cfg = self.config
        self._engine_chain = EngineChain(cfg.engines, cfg.search_backend)
        zhihu = ZhihuExtractor(
            zhihu_cookies=cfg.search_backend.zhihu_cookies,
            content_timeout=cfg.search_backend.content_timeout,
            max_content_length=cfg.search_backend.max_content_length,
            proxy=cfg.search_backend.proxy or "",
        )
        self._content_fetcher = ContentFetcher(
            backend_cfg=cfg.search_backend,
            engines_cfg=cfg.engines,
            you_contents=self._engine_chain.you_contents,
            zhihu_extractor=zhihu,
        )
        self._llm_runner = LLMRunner(self.ctx, cfg.models)
        self._history_writer = HistoryWriter(self.ctx, cfg.storage)
        self._search_pipeline = SearchPipeline(
            self.ctx,
            models_cfg=cfg.models,
            backend_cfg=cfg.search_backend,
            engine_chain=self._engine_chain,
            content_fetcher=self._content_fetcher,
            llm_runner=self._llm_runner,
            history_writer=self._history_writer,
        )
        self._url_pipeline = UrlPipeline(
            content_fetcher=self._content_fetcher,
            llm_runner=self._llm_runner,
            history_writer=self._history_writer,
        )

    async def _resolve_bot_name(self) -> str:
        """从全局 bot 配置取昵称(失败时兜底 '机器人')。"""
        try:
            value = await self.ctx.config.get("bot.nickname", "机器人")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.debug("config.get bot.nickname 失败: %s", exc)
            return "机器人"
        return str(value or "机器人") or "机器人"

    # ---------------------------------------------------------------- #
    # Tool: web_search
    # ---------------------------------------------------------------- #

    @Tool(
        "web_search",
        description="谷歌搜索工具。当见到有人发出疑问或者遇到不熟悉的事情时候，直接使用它获得最新知识！",
        parameters=[
            ToolParameterInfo(
                name="question",
                param_type=ToolParamType.STRING,
                description="需要搜索的消息",
                required=True,
            ),
            ToolParameterInfo(
                name="tavily_topic",
                param_type=ToolParamType.STRING,
                description="可选：Tavily topic 覆写（general/news）；留空则由模型自动判断。",
                required=False,
            ),
        ],
    )
    async def handle_web_search(
        self,
        question: str = "",
        tavily_topic: str = "",
        stream_id: str = "",
        **kwargs: Any,
    ) -> dict[str, str]:
        """主搜索入口。"""
        del kwargs

        question = (question or "").strip()
        if not question:
            return {"name": "web_search", "content": "问题为空，无法执行搜索。"}

        if self._search_pipeline is None or self._url_pipeline is None:
            self.ctx.logger.warning("pipelines 未就绪,尝试重建")
            try:
                self._build_pipelines()
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.error("pipelines 重建失败: %s", exc, exc_info=True)
                return {"name": "web_search", "content": ""}

        # tavily_topic 校验
        from .tools.rewrite_output import ALLOWED_TAVILY_TOPICS

        normalized_topic = (tavily_topic or "").strip().lower()
        topic_override = normalized_topic if normalized_topic in ALLOWED_TAVILY_TOPICS else None

        bot_name = await self._resolve_bot_name()

        try:
            if is_url(question):
                self.ctx.logger.info("检测到 URL 输入,直接访问并总结: %s", question)
                content = await self._url_pipeline.run(  # type: ignore[union-attr]
                    question,
                    chat_id=stream_id,
                    bot_name=bot_name,
                )
            else:
                self.ctx.logger.info("开始执行搜索,原始问题: %s", question)
                content = await self._search_pipeline.run(  # type: ignore[union-attr]
                    question,
                    chat_id=stream_id,
                    bot_name=bot_name,
                    tavily_topic_override=topic_override,
                )
            return {"name": "web_search", "content": content}
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("web_search 执行异常: %s", exc, exc_info=True)
            return {"name": "web_search", "content": ""}

    # ---------------------------------------------------------------- #
    # 临时诊断命令(阶段 3 留着方便排错;最终阶段 5 可删)
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
        del kwargs

        cfg = self.config
        e = cfg.engines
        enabled_engines = []
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

        pipelines_ready = all(
            v is not None
            for v in (
                self._engine_chain,
                self._content_fetcher,
                self._history_writer,
                self._llm_runner,
                self._search_pipeline,
                self._url_pipeline,
            )
        )

        lines = [
            f"google_search_plugin v{cfg.plugin.version} (新 SDK 重写)",
            f"模型 task: {cfg.models.model_name}  温度: {cfg.models.temperature}",
            f"默认引擎: {cfg.search_backend.default_engine}",
            f"启用引擎: {', '.join(enabled_engines) if enabled_engines else '(无)'}",
            f"图片搜索: {'已启用' if cfg.actions.image_search_enabled else '未启用'} (待阶段 4 接入)",
            f"缩写翻译: {'已启用' if cfg.translation.enabled else '未启用'} (待阶段 4 接入)",
            f"web_search Tool: {'就绪' if pipelines_ready else '未就绪'}",
            "",
            _LEGACY_VERSION_HINT,
        ]
        message = "\n".join(lines)

        if stream_id:
            await self.ctx.send.text(message, stream_id)
        return True, message, True


def create_plugin() -> GoogleSearchPlugin:
    """Runner 通过此工厂函数实例化插件"""
    return GoogleSearchPlugin()


_logger = logging.getLogger(__name__)
