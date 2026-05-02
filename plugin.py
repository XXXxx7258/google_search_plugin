"""google_search_plugin 主入口

业务逻辑全部抽到 ``pipelines/`` 子模块,本文件只负责装配 + 派发。

提供三个面向 LLM 的组件:
- ``@Tool("web_search")``             主搜索工具(支持 URL 直访)
- ``@Tool("abbreviation_translate")`` 缩写翻译(神奇海螺 nbnhhsh)
- ``@Action("image_search")``         图片搜索(handler 内按配置短路)

外加一个 ``/google_search_status`` 诊断命令。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from maibot_sdk import Action, Command, MaiBotPlugin, Tool
from maibot_sdk.types import ActivationType, ToolParameterInfo, ToolParamType

from .config import GoogleSearchPluginConfig
from .pipelines.content_fetcher import ContentFetcher
from .pipelines.engine_chain import EngineChain
from .pipelines.history_writer import HistoryWriter
from .pipelines.image_search_pipeline import ImageSearchPipeline
from .pipelines.llm_runner import LLMRunner
from .pipelines.search_pipeline import SearchPipeline
from .pipelines.url_pipeline import UrlPipeline, is_url
from .pipelines.zhihu_extractor import ZhihuExtractor
from .translators.nbnhhsh import NbnhhshTranslator


class GoogleSearchPlugin(MaiBotPlugin):
    """麦麦联网插件主类"""

    config_model = GoogleSearchPluginConfig

    # 运行时组件:在 on_load / on_config_update 中装配
    _engine_chain: Optional[EngineChain]
    _content_fetcher: Optional[ContentFetcher]
    _history_writer: Optional[HistoryWriter]
    _llm_runner: Optional[LLMRunner]
    _search_pipeline: Optional[SearchPipeline]
    _url_pipeline: Optional[UrlPipeline]
    _translator: Optional[NbnhhshTranslator]
    _image_pipeline: Optional[ImageSearchPipeline]

    def __init__(self) -> None:
        super().__init__()
        self._engine_chain = None
        self._content_fetcher = None
        self._history_writer = None
        self._llm_runner = None
        self._search_pipeline = None
        self._url_pipeline = None
        self._translator = None
        self._image_pipeline = None

    # ---------------------------------------------------------------- #
    # 生命周期
    # ---------------------------------------------------------------- #

    async def on_load(self) -> None:
        self._build_pipelines()
        cfg = self.config
        self.ctx.logger.info(
            "google_search_plugin v%s 已加载 (model=%s, default_engine=%s, "
            "image_search=%s, translation=%s)",
            cfg.plugin.version,
            cfg.models.model_name,
            cfg.search_backend.default_engine,
            cfg.actions.image_search_enabled,
            cfg.translation.enabled,
        )

    async def on_unload(self) -> None:
        self.ctx.logger.info("google_search_plugin 已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        """配置热更新:简单粗暴重建所有组件。"""
        del config_data
        self.ctx.logger.info("配置更新事件: scope=%s version=%s,重建 pipelines", scope, version)
        try:
            self._build_pipelines()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("重建 pipelines 失败: %s", exc, exc_info=True)

    def _build_pipelines(self) -> None:
        """从 self.config 装配所有运行时组件。"""
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
        # 翻译器(NbnhhshTranslator 接 dict 配置)
        self._translator = NbnhhshTranslator(
            {
                "timeout": cfg.translation.timeout_seconds,
                "max_retries": cfg.translation.max_retries,
            }
        )
        # 图片搜索 pipeline
        self._image_pipeline = ImageSearchPipeline(
            engines_cfg=cfg.engines,
            backend_cfg=cfg.search_backend,
        )

    async def _resolve_bot_name(self) -> str:
        """从全局 bot 配置取昵称(失败时兜底 '机器人')。"""
        try:
            value = await self.ctx.config.get("bot.nickname", "机器人")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.debug("config.get bot.nickname 失败: %s", exc)
            return "机器人"
        return str(value or "机器人") or "机器人"

    def _ensure_pipelines_ready(self) -> bool:
        """确保 pipelines 已装配;未装配则尝试重建。"""
        if all(
            v is not None
            for v in (
                self._engine_chain,
                self._content_fetcher,
                self._history_writer,
                self._llm_runner,
                self._search_pipeline,
                self._url_pipeline,
                self._translator,
                self._image_pipeline,
            )
        ):
            return True
        self.ctx.logger.warning("pipelines 未就绪,尝试重建")
        try:
            self._build_pipelines()
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("pipelines 重建失败: %s", exc, exc_info=True)
            return False
        return True

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

        if not self._ensure_pipelines_ready():
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
    # Tool: abbreviation_translate
    # ---------------------------------------------------------------- #

    @Tool(
        "abbreviation_translate",
        description=(
            "当遇到用户消息中出现难懂的网络用语、缩写、黑话、热词或流行语时，"
            "主动查询并翻译这些词汇以帮助理解。适用于各种类型的网络语言，"
            "包括字母缩写（如yyds、u1s1）、网络黑话、当下热词、流行语等。"
            "应该识别消息中可能让人困惑的网络用语并自动查询其含义。"
        ),
        parameters=[
            ToolParameterInfo(
                name="term",
                param_type=ToolParamType.STRING,
                description="从用户消息中识别出的网络用语、缩写或热词（如：yyds、躺平、内卷等）",
                required=True,
            ),
            ToolParameterInfo(
                name="max_results",
                param_type=ToolParamType.INTEGER,
                description="返回翻译结果数量,默认 3",
                required=False,
            ),
        ],
    )
    async def handle_abbreviation_translate(
        self,
        term: str = "",
        max_results: int = 3,
        stream_id: str = "",
        **kwargs: Any,
    ) -> dict[str, str]:
        """缩写翻译入口(走 nbnhhsh 神奇海螺 API)。"""
        del kwargs
        del stream_id  # 翻译结果通过 return content 返回给 LLM,不直接 send

        if not self.config.translation.enabled:
            return {"name": "abbreviation_translate", "content": "翻译功能已禁用"}

        term = (term or "").strip()
        if not term:
            return {"name": "abbreviation_translate", "content": "未提供要翻译的词汇"}

        if not self._ensure_pipelines_ready():
            return {"name": "abbreviation_translate", "content": "翻译组件未就绪"}

        try:
            self.ctx.logger.info("翻译: %s", term)
            result = await self._translator.translate(term)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("翻译异常: %s", exc, exc_info=True)
            return {"name": "abbreviation_translate", "content": f"缩写翻译失败: {exc}"}

        translations = result.translations[:max_results] if result.translations else []
        if not translations:
            return {"name": "abbreviation_translate", "content": f"未找到「{term}」的翻译结果"}

        if len(translations) == 1:
            content = f"网络用语「{term}」的含义是：{translations[0]}"
        else:
            lines = "\n".join(f"• {t}" for t in translations)
            content = f"网络用语「{term}」的可能含义：\n{lines}"
        return {"name": "abbreviation_translate", "content": content}

    # ---------------------------------------------------------------- #
    # Action: image_search
    # ---------------------------------------------------------------- #

    @Action(
        "image_search",
        description="当用户明确需要搜索图片时使用此动作。例如：'搜索一下猫的图片'、'来张风景图'。",
        activation_type=ActivationType.ALWAYS,
        action_parameters={"query": "需要搜索的图片关键词"},
        action_require=[
            "当用户明确表示想看、想搜索或想要一张图片时使用。",
            "适用于'搜/找/来一张/发一张xx的图片'等指令。",
            "如果用户只是在普通聊天中提到了某个事物，不代表他想要图片，此时不应使用。",
            "一次只随机发送一张图片，30 分钟内不重复发送同一图片。",
            "若插件配置中未启用图片搜索功能，本动作会拒绝执行,请勿调用。",
        ],
        associated_types=["image"],
        parallel_action=False,
    )
    async def handle_image_search(
        self,
        query: str = "",
        stream_id: str = "",
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """图片搜索入口(决策 2A:handler 内短路而非条件注册)。"""
        del kwargs

        if not self.config.actions.image_search_enabled:
            if stream_id:
                await self.ctx.send.text(
                    "图片搜索功能当前未启用。如需使用，请在配置文件中启用此功能。",
                    stream_id,
                )
            return False, "图片搜索功能未启用"

        query = (query or "").strip()
        if not query:
            if stream_id:
                await self.ctx.send.text("你想搜什么图片呀？", stream_id)
            return False, "关键词为空"

        if not self._ensure_pipelines_ready():
            return False, "图片搜索组件未就绪"

        try:
            self.ctx.logger.info("开始图片搜索: %s", query)
            status, b64, url = await self._image_pipeline.find_unique_image_b64(  # type: ignore[union-attr]
                query
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.error("图片搜索动作异常: %s", exc, exc_info=True)
            if stream_id:
                await self.ctx.send.text(f"搜索图片时出错了：{exc}", stream_id)
            return False, f"图片搜索失败: {exc}"

        if status == "ok":
            try:
                await self.ctx.send.image(b64, stream_id)
                self.ctx.logger.info("成功发送图片 url=%s", url)
                return True, "图片发送成功"
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.error("send.image 失败: %s", exc, exc_info=True)
                if stream_id:
                    await self.ctx.send.text("我下载好了图片，但是发送失败了...", stream_id)
                return False, f"发送图片失败: {exc}"

        if status == "no_results":
            if stream_id:
                await self.ctx.send.text(f"我没找到关于「{query}」的图片呢。", stream_id)
            return False, "未找到图片"

        if status == "no_unique":
            if stream_id:
                await self.ctx.send.text(
                    "最近30分钟内已经发过相关图片了，先休息一下吧。",
                    stream_id,
                )
            return False, "30 分钟内图片重复"

        # all_failed
        if stream_id:
            await self.ctx.send.text("找到了图片，但下载都失败了，可能是网络问题。", stream_id)
        return False, "所有图片下载失败"

    # ---------------------------------------------------------------- #
    # 诊断命令
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

        ready = all(
            v is not None
            for v in (
                self._engine_chain,
                self._content_fetcher,
                self._history_writer,
                self._llm_runner,
                self._search_pipeline,
                self._url_pipeline,
                self._translator,
                self._image_pipeline,
            )
        )

        lines = [
            f"google_search_plugin v{cfg.plugin.version}",
            f"模型 task: {cfg.models.model_name}  温度: {cfg.models.temperature}",
            f"默认引擎: {cfg.search_backend.default_engine}",
            f"启用引擎: {', '.join(enabled_engines) if enabled_engines else '(无)'}",
            f"图片搜索: {'已启用' if cfg.actions.image_search_enabled else '未启用'}",
            f"缩写翻译: {'已启用' if cfg.translation.enabled else '未启用'}",
            f"组件就绪: {'是' if ready else '否'}",
        ]
        message = "\n".join(lines)

        if stream_id:
            await self.ctx.send.text(message, stream_id)
        return True, message, True


def create_plugin() -> GoogleSearchPlugin:
    """Runner 通过此工厂函数实例化插件"""
    return GoogleSearchPlugin()


_logger = logging.getLogger(__name__)
