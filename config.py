"""google_search_plugin 配置模型 (新 SDK 版)

按 PluginConfigBase 拆分为多个 section,字段集与 v3.x 的 config_schema 一一对应。
注意 [model_config] section 已重命名为 [models](Pydantic v2 保留 model_config 这个名字)。
"""

from typing import Literal

from maibot_sdk import Field, PluginConfigBase

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


class PluginSection(PluginConfigBase):
    """插件基础信息"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    name: str = Field(default="google_search", description="插件名称")
    version: str = Field(default="4.0.0", description="插件版本")
    config_version: str = Field(default="4.0.0", description="配置版本(Runner 用于兼容性校验)")
    enabled: bool = Field(default=True, description="是否启用插件")


class ModelsSection(PluginConfigBase):
    """搜索/总结使用的 LLM 任务参数

    注意:section 名为 ``models``,不是 ``model_config``——Pydantic v2 保留了
    ``model_config`` 作为模型元数据属性名,这里必须改名。v3.x 用户升级后需要
    把旧 toml 中的 ``[model_config]`` section 改成 ``[models]``。
    """

    __ui_label__ = "模型"
    __ui_icon__ = "brain"
    __ui_order__ = 1

    model_name: Literal[
        "replyer",
        "utils",
        "tool_use",
        "planner",
        "vlm",
        "lpmm_entity_extract",
        "lpmm_rdf_build",
        "lpmm_qa",
    ] = Field(
        default="replyer",
        description="指定用于搜索和总结的系统模型 task。默认 'replyer' 即系统主回复模型。",
    )
    temperature: float = Field(default=0.7, description="模型生成温度")
    context_time_gap: int = Field(default=300, description="拉取最近多少秒的全局聊天作为上下文")
    context_max_limit: int = Field(default=15, description="最多拉取多少条全局聊天作为上下文")


class ActionsSection(PluginConfigBase):
    """动作组件开关"""

    __ui_label__ = "动作"
    __ui_icon__ = "zap"
    __ui_order__ = 2

    image_search_enabled: bool = Field(
        default=False,
        description="是否启用图片搜索功能。支持 Bing、搜狗(国内可直连)和 DuckDuckGo(需翻墙)三个引擎自动降级。",
    )


class SearchBackendSection(PluginConfigBase):
    """搜索后端通用参数"""

    __ui_label__ = "搜索后端"
    __ui_icon__ = "globe"
    __ui_order__ = 3

    default_engine: Literal["google", "bing", "sogou", "duckduckgo", "tavily", "you", "you_news"] = Field(
        default="bing",
        description="默认搜索引擎",
    )
    max_results: int = Field(default=15, description="默认返回结果数量")
    timeout: int = Field(default=20, description="搜索超时时间(秒)")
    proxy: str = Field(
        default="",
        description="HTTP/HTTPS 代理地址,例如 'http://127.0.0.1:7890'。留空表示不走代理。",
    )
    fetch_content: bool = Field(default=True, description="是否抓取网页内容")
    content_timeout: int = Field(default=10, description="内容抓取超时(秒)")
    max_content_length: int = Field(default=3000, description="最大内容长度")
    zhihu_cookies: str = Field(
        default="",
        description="知乎专用抓取使用的 Cookie 字符串;留空则不启用知乎专用抓取。",
    )
    user_agents: list[str] = Field(
        default_factory=lambda: [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
        ],
        description="抓取网页时使用的 User-Agent 列表,会从中随机选择。",
    )


class EnginesSection(PluginConfigBase):
    """各搜索引擎的开关与专属参数"""

    __ui_label__ = "引擎"
    __ui_icon__ = "settings"
    __ui_order__ = 4

    # Google
    google_enabled: bool = Field(default=False, description="是否启用 Google 搜索")
    google_language: str = Field(default="zh-cn", description="搜索语言")

    # Bing
    bing_enabled: bool = Field(default=True, description="是否启用 Bing 搜索")
    bing_region: str = Field(default="zh-CN", description="Bing 搜索区域代码")

    # 搜狗
    sogou_enabled: bool = Field(default=True, description="是否启用搜狗搜索")

    # DuckDuckGo (DDGS)
    duckduckgo_enabled: bool = Field(default=True, description="是否启用 DDGS 元搜索引擎")
    duckduckgo_region: str = Field(default="wt-wt", description="搜索区域代码,例如 'us-en' 或 'cn-zh'")
    duckduckgo_backend: str = Field(
        default="auto",
        description="使用的后端。'auto' 表示自动选择,也可以指定多个,如 'duckduckgo,google,brave'",
    )
    duckduckgo_safesearch: Literal["on", "moderate", "off"] = Field(
        default="moderate",
        description="安全搜索级别",
    )
    duckduckgo_timelimit: Literal["none", "d", "w", "m", "y"] = Field(
        default="none",
        description="时间限制 (d, w, m, y;none 表示不限)",
    )

    # Tavily
    tavily_enabled: bool = Field(default=False, description="是否启用 Tavily 搜索")
    tavily_api_keys: list[str] = Field(
        default_factory=list,
        description="Tavily API key 列表,填写多个时随机选用",
    )
    tavily_api_key: str = Field(default="", description="Tavily API key;留空则使用环境变量 TAVILY_API_KEY")
    tavily_search_depth: Literal["basic", "advanced"] = Field(default="basic", description="搜索深度")
    tavily_include_raw_content: bool = Field(default=False, description="是否返回网页原始内容")
    tavily_include_answer: bool = Field(default=True, description="是否返回 Tavily 生成的答案")
    tavily_topic: str = Field(
        default="",
        description="可选的主题参数,例如 'general' 或 'news';留空则由模型自动判断",
    )
    tavily_turbo: bool = Field(default=False, description="是否启用 Tavily Turbo 模式")

    # You
    you_enabled: bool = Field(default=False, description="是否启用 You Search")
    you_news_enabled: bool = Field(default=False, description="是否启用 You Live News(early access)")
    you_api_keys: list[str] = Field(
        default_factory=list,
        description="You API key 列表,填写多个时随机选用",
    )
    you_api_key: str = Field(default="", description="You API key;留空则使用环境变量 YOU_API_KEY")
    you_freshness: str = Field(
        default="",
        description="You 搜索时间范围(可选:day/week/month/year;留空表示不限)",
    )
    you_offset: int = Field(default=0, description="You 搜索分页 offset(0-9,超出将被夹取)")
    you_country: str = Field(default="", description="You 搜索国家代码(如 CN/US)")
    you_language: str = Field(default="", description="You 搜索语言(BCP 47,如 EN/zh-Hans)")
    you_safesearch: str = Field(
        default="",
        description="You 安全搜索级别(off/moderate/strict;留空表示使用默认值)",
    )
    you_livecrawl: str = Field(
        default="",
        description="You Livecrawl 范围(web/news/all);留空表示不启用",
    )
    you_livecrawl_formats: str = Field(
        default="",
        description="You Livecrawl 内容格式(html/markdown);留空表示不启用",
    )
    you_contents_enabled: bool = Field(default=False, description="是否启用 You Contents 抓取")
    you_contents_format: Literal["html", "markdown"] = Field(
        default="markdown",
        description="You Contents 返回内容格式",
    )
    you_contents_force: bool = Field(
        default=False,
        description="是否强制使用 You Contents(不受搜索引擎来源限制)",
    )
    you_images_enabled: bool = Field(default=False, description="是否启用 You Images(early access)")


class TranslationSection(PluginConfigBase):
    """缩写翻译(神奇海螺 nbnhhsh)参数"""

    __ui_label__ = "缩写翻译"
    __ui_icon__ = "languages"
    __ui_order__ = 6

    enabled: bool = Field(default=True, description="是否启用缩写翻译工具")
    timeout_seconds: int = Field(default=10, description="API 请求超时(秒)")
    max_retries: int = Field(default=3, description="API 请求失败时的最大重试次数")


# ---------------------------------------------------------------------------
# Top-level plugin config
# ---------------------------------------------------------------------------


class GoogleSearchPluginConfig(PluginConfigBase):
    """google_search_plugin 顶层配置"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    models: ModelsSection = Field(default_factory=ModelsSection)
    actions: ActionsSection = Field(default_factory=ActionsSection)
    search_backend: SearchBackendSection = Field(default_factory=SearchBackendSection)
    engines: EnginesSection = Field(default_factory=EnginesSection)
    translation: TranslationSection = Field(default_factory=TranslationSection)
