import asyncio
import random
import time
import re
import json
import base64
import warnings
import textwrap
from collections import deque
from typing import List, Tuple, Type, Dict, Any, Optional, Deque
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
from readability import Document

from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory
from src.chat.utils.utils import parse_keywords_string
from src.config.config import global_config
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseTool,
    BaseAction,
    ActionActivationType,
    ComponentInfo,
    ConfigField,
    ToolParamType,
    llm_api,
    message_api,
)
from .search_engines.base import SearchResult
from .search_engines.google import GoogleEngine
from .search_engines.bing import BingEngine
from .search_engines.sogou import SogouEngine
from .search_engines.duckduckgo import DuckDuckGoEngine
from .search_engines.tavily import TavilyEngine
from .tools.abbreviation_tool import AbbreviationTool

# 抑制readability的ruthless removal警告
warnings.filterwarnings("ignore", message=".*ruthless removal.*")

logger = get_logger("google_search")


def _build_engine_config(engine_name: str, engines_config: Dict[str, Any], common_config: Dict[str, Any]) -> Dict[str, Any]:
    """集中生成各搜索引擎的配置，避免重复散落。"""
    cfg = {**common_config}
    if engine_name == "google":
        cfg.update(
            {
                "enabled": engines_config.get("google_enabled", False),
                "language": engines_config.get("google_language", "zh-cn"),
            }
        )
    elif engine_name == "bing":
        cfg.update(
            {
                "enabled": engines_config.get("bing_enabled", True),
                "region": engines_config.get("bing_region", "zh-CN"),
            }
        )
    elif engine_name == "sogou":
        cfg.update({"enabled": engines_config.get("sogou_enabled", True)})
    elif engine_name == "duckduckgo":
        cfg.update(
            {
                "enabled": engines_config.get("duckduckgo_enabled", True),
                "region": engines_config.get("duckduckgo_region", "wt-wt"),
                "backend": engines_config.get("duckduckgo_backend", "auto"),
                "safesearch": engines_config.get("duckduckgo_safesearch", "moderate"),
                "timelimit": None
                if engines_config.get("duckduckgo_timelimit", "") in ("", "none")
                else engines_config.get("duckduckgo_timelimit"),
            }
        )
    elif engine_name == "tavily":
        cfg.update(
            {
                "enabled": engines_config.get("tavily_enabled", False),
                "api_keys": engines_config.get("tavily_api_keys", []),
                "api_key": engines_config.get("tavily_api_key", ""),
                "search_depth": engines_config.get("tavily_search_depth", "basic"),
                "include_raw_content": engines_config.get("tavily_include_raw_content", False),
                "include_answer": engines_config.get("tavily_include_answer", True),
                "topic": engines_config.get("tavily_topic", ""),
                "turbo": engines_config.get("tavily_turbo", False),
            }
        )
    return cfg

class WebSearchTool(BaseTool):
    """Web 搜索工具"""
    
    name: str = "web_search"
    description: str = "谷歌搜索工具。当见到有人发出疑问或者遇到不熟悉的事情时候，直接使用它获得最新知识！"
    parameters: List[Tuple[str, ToolParamType, str, bool, None]] = [
        ("question", ToolParamType.STRING, "需要搜索的消息", True, None),
    ]
    available_for_llm: bool = True
    
    # 实例属性类型注解
    google: GoogleEngine
    bing: BingEngine
    sogo: SogouEngine
    duckduckgo: DuckDuckGoEngine
    tavily: TavilyEngine
    model_config: Dict[str, Any]
    backend_config: Dict[str, Any]
    last_success_engine: Optional[str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._initialize_engines()

    def _identity_header(self) -> str:
        """提供给 LLM 的身份与时间提示，降低时间误判。"""
        bot = getattr(global_config, "bot", None)
        bot_name = getattr(bot, "nickname", None) or "机器人"
        time_now = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        return f"你的名字是{bot_name}。现在是{time_now}。"

    def _initialize_engines(self) -> None:
        """初始化搜索引擎"""
        config = self.plugin_config
        engines_config = config.get("engines", {})
        backend_config = config.get("search_backend", {})

        # 将顶层配置注入到每个引擎
        common_config = {
            "timeout": backend_config.get("timeout", 20),
            "proxy": backend_config.get("proxy"),
            "max_results": backend_config.get("max_results", 10)
        }

        google_config = _build_engine_config("google", engines_config, common_config)
        bing_config = _build_engine_config("bing", engines_config, common_config)
        sogou_config = _build_engine_config("sogou", engines_config, common_config)
        duckduckgo_config = _build_engine_config("duckduckgo", engines_config, common_config)
        tavily_config = _build_engine_config("tavily", engines_config, common_config)

        self.google = GoogleEngine(google_config)
        self.bing = BingEngine(bing_config)
        self.sogo = SogouEngine(sogou_config)
        self.duckduckgo = DuckDuckGoEngine(duckduckgo_config)
        self.tavily = TavilyEngine(tavily_config)
        self.last_success_engine = None
        self.last_tavily_answer: Optional[str] = None
        
        # 存储配置供后续使用
        self.model_config = config.get("model_config", {})
        self.backend_config = config.get("search_backend", {})

    async def execute(self, function_args: Dict[str, Any]) -> Dict[str, str]:
        """执行搜索
        
        Args:
            function_args: 包含 'question' 键的字典
            
        Returns:
            包含 'name' 和 'content' 键的结果字典
        """
        question = function_args.get("question", "")
        question = question.strip()
        if not question:
            return {"name": self.name, "content": "问题为空，无法执行搜索。"}

        try:
            if self._is_url(question):
                logger.info(f"检测到URL输入，直接访问并总结: {question}")
                result_content = await self._execute_direct_url_summary(question)
            else:
                logger.info(f"开始执行搜索，原始问题: {question}")
                result_content = await self._execute_model_driven_search(question)
            return {"name": self.name, "content": result_content}
        except Exception as e:
            logger.error(f"搜索执行异常: {e}", exc_info=True)
            return {"name": self.name, "content": f"搜索失败: {str(e)}"}

    def _is_url(self, text: str) -> bool:
        """检测文本是否为URL"""
        if not text:
            return False
        candidate = text.strip()
        if " " in candidate:
            return False
        try:
            parsed = urlparse(candidate)
        except ValueError:
            return False
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        if not parsed.netloc:
            return False
        return True

    async def _execute_direct_url_summary(self, url: str) -> str:
        """直接访问URL并总结其内容"""
        logger.info(f"开始直接访问URL: {url}")
        async with aiohttp.ClientSession(trust_env=True) as session:
            content = await self._fetch_page_content(session, url)

        if not content:
            return f"无法访问该网页或提取内容: {url}"

        logger.info(f"成功抓取网页内容，长度: {len(content)}")
        summarize_prompt = self._build_url_summarize_prompt(url, content)
        logger.info("调用LLM对网页内容进行总结...")
        final_answer = await self._call_llm(summarize_prompt)
        self._record_search_history(
            original_question=url,
            search_query=url,
            results=[SearchResult(title=url, url=url, snippet=content[:200] if content else "")],
            final_answer=final_answer,
            source_type="direct_url",
        )
        return final_answer

    def _build_url_summarize_prompt(self, url: str, content: str) -> str:
        """构建网页内容总结提示词"""
        truncated_content = content[:8000]
        return textwrap.dedent(
            f"""
            {self._identity_header()}
            [任务]
            你是一个专业的内容总结专家。用户提供了一个网页链接，你的任务是阅读这个网页的内容，并提供一个全面、准确、结构清晰的总结。

            [网页URL]
            {url}

            [网页内容]
            {truncated_content}

            [要求]
            1. 提供网页的主要内容概述
            2. 如果是文章，总结其核心观点和关键信息
            3. 如果是产品页面，说明产品的主要特性和用途
            4. 如果是新闻，说明事件的关键要素（何时、何地、何人、何事、为何）
            5. 保持客观中立，不要添加主观评价
            6. 使用清晰的结构和层次组织信息
            7. 不要因为发布时间较新就认为内容是虚构的，请按当前时间理解信息
            8. 如果内容过于简短或无实质信息，请说明

            [你的总结]
            """
        ).strip()

    async def _execute_model_driven_search(self, question: str) -> str:
        """执行模型驱动的智能搜索流程"""
        # 1. 获取全局上下文
        time_gap = self.model_config.get("context_time_gap", 300)
        max_limit = self.model_config.get("context_max_limit", 15)
        context_messages = message_api.get_messages_by_time(
            start_time=time.time() - time_gap,
            end_time=time.time(),
            limit=max_limit
        )
        context_str = message_api.build_readable_messages_to_str(context_messages)

        # 2. 构建查询重写 Prompt
        rewrite_prompt = self._build_rewrite_prompt(question, context_str)

        # 3. 调用 LLM 进行查询重写
        logger.info("调用LLM进行查询重写...")
        rewritten_query = await self._call_llm(rewrite_prompt)
        if not rewritten_query or "无需搜索" in rewritten_query:
            logger.info("模型判断无需搜索或无法生成搜索词。")
            return rewritten_query or "根据上下文分析，我无法确定需要搜索的具体内容。"

        logger.info(f"模型重写后的搜索查询: {rewritten_query}")

        # 4. 执行后端搜索
        self.last_success_engine = None
        self.last_tavily_answer = None
        max_results = self.backend_config.get("max_results", 10)
        search_results = await self._search_with_fallback(rewritten_query, max_results)

        if not search_results:
            return f"关于「{rewritten_query}」，我没有找到相关的网络信息。"

        # 5. (可选) 使用 Tavily 结果或执行抓取
        if self.last_success_engine == "tavily":
            self._integrate_inline_content(search_results)
        elif self.backend_config.get("fetch_content", True):
            search_results = await self._fetch_content_for_results(search_results)

        # 6. 构建总结 Prompt
        summarize_prompt = self._build_summarize_prompt(question, rewritten_query, search_results)

        # 7. 调用 LLM 进行总结
        logger.info("调用LLM对搜索结果进行总结...")
        final_answer = await self._call_llm(summarize_prompt)

        self._record_search_history(
            original_question=question,
            search_query=rewritten_query,
            results=search_results,
            final_answer=final_answer,
            source_type="search",
        )

        return final_answer

    async def _call_llm(self, prompt: str) -> str:
        """统一的LLM调用函数
        
        Args:
            prompt: 发送给LLM的提示词
            
        Returns:
            LLM生成的文本响应
        """
        try:
            # 选择模型
            models = llm_api.get_available_models()
            if not models:
                raise ValueError("系统中没有可用的LLM模型配置。")

            # 从本插件配置中获取目标模型名称，默认为 'replyer'
            target_model_name = self.model_config.get("model_name", "replyer")
            model_config = models.get(target_model_name)

            # 如果找不到用户指定的模型，则记录警告并使用默认模型
            if not model_config:
                logger.warning(f"在系统配置中未找到名为 '{target_model_name}' 的模型，将回退到系统默认模型。")
                default_model_name, model_config = next(iter(models.items()))
                logger.info(f"使用系统默认模型: {default_model_name}")
            else:
                logger.info(f"使用模型: {target_model_name}")

            # 获取温度配置
            temperature = self.model_config.get("temperature")

            # 直接使用系统llm_api调用选定的模型
            success, content, _, _ = await llm_api.generate_with_model(
                prompt,
                model_config,
                temperature=temperature
            )
            if success:
                return content.strip() if content else ""
            else:
                logger.error(f"调用系统LLM API失败: {content}")
                return f"在处理信息时遇到了一个内部错误: {content}"
        except Exception as e:
            logger.error(f"调用LLM API时出错: {e}")
            return f"在处理信息时遇到了一个内部错误: {e}"

    def _record_search_history(
        self,
        original_question: str,
        search_query: str,
        results: List[SearchResult],
        final_answer: str,
        source_type: str,
    ) -> None:
        """将搜索结果写入 chat_history，供记忆检索复用"""
        if not self.chat_id:
            return
        if not self.get_config("storage.enable_store", True):
            return

        try:
            now_ts = time.time()
            theme = f"Web搜索: {search_query or original_question}"
            dedup_window = self.get_config("storage.dedup_window_seconds", 600)
            if dedup_window:
                existing = (
                    ChatHistory.select()
                    .where(
                        (ChatHistory.chat_id == self.chat_id)
                        & (ChatHistory.theme == theme)
                        & (ChatHistory.start_time >= now_ts - dedup_window)
                    )
                    .order_by(ChatHistory.start_time.desc())
                )
                if existing.exists():
                    return

            top_k = self.get_config("storage.store_top_k", 5)
            keywords = self._extract_keywords(search_query or original_question)
            results_summary = self._format_results_summary(results, top_k)
            final_answer_text = (final_answer or "").strip()
            if final_answer_text and results_summary:
                summary = f"{final_answer_text}\n\n---\n\n{results_summary}"
            elif final_answer_text:
                summary = final_answer_text
            else:
                summary = results_summary or ""

            # 控制 original_text 中最终回答的长度，避免占用过大
            final_answer_for_text = final_answer_text
            max_final_len = self.get_config("storage.final_answer_max_len", 1200)
            if max_final_len and len(final_answer_for_text) > max_final_len:
                final_answer_for_text = final_answer_for_text[:max_final_len] + "…"

            serialized_results = self._serialize_results(results, top_k)
            original_text_parts = [
                f"source_type: {source_type}",
                f"original_question: {original_question}",
                f"search_query: {search_query}",
                f"engine: {self.last_success_engine or 'unknown'}",
                f"final_answer: {final_answer_for_text}",
                f"results_json: {json.dumps(serialized_results, ensure_ascii=False)}",
            ]
            original_text = "\n".join(original_text_parts)

            ChatHistory.create(
                chat_id=self.chat_id,
                start_time=now_ts,
                end_time=now_ts,
                original_text=original_text,
                participants="web_search_plugin",
                theme=theme,
                keywords=json.dumps(keywords, ensure_ascii=False) if keywords else json.dumps([], ensure_ascii=False),
                summary=summary or "网络搜索结果已记录",
            )
            logger.info(f"已写入搜索结果到 chat_history，主题: {theme}")
        except Exception as e:
            logger.error(f"记录搜索历史失败: {e}")

    def _extract_keywords(self, text: str) -> List[str]:
        if not text:
            return []
        try:
            kws = parse_keywords_string(text)
            if kws:
                return kws
        except Exception:
            pass
        try:
            # 按空白、逗号、分号、斜杠拆分关键词
            return [kw for kw in re.split(r"[\s,;/]+", text) if kw]
        except Exception:
            return []

    def _format_results_summary(self, results: List[SearchResult], top_k: int) -> str:
        if not results:
            return ""
        lines: List[str] = []
        for item in results[:top_k]:
            if not item:
                continue
            title = getattr(item, "title", "") or ""
            url = getattr(item, "url", "") or ""
            snippet = getattr(item, "snippet", "") or getattr(item, "abstract", "") or ""
            if title or url:
                lines.append(f"{title} - {url}".strip(" -"))
            if snippet:
                lines.append(f"摘要：{snippet}")
            lines.append("")
        return "\n".join(lines).strip()

    def _serialize_results(self, results: List[SearchResult], top_k: int) -> List[Dict[str, str]]:
        serialized: List[Dict[str, str]] = []
        if not results:
            return serialized
        for item in results[:top_k]:
            if not item:
                continue
            serialized.append(
                {
                    "title": getattr(item, "title", "") or "",
                    "url": getattr(item, "url", "") or "",
                    "snippet": getattr(item, "snippet", "") or "",
                    "abstract": getattr(item, "abstract", "") or "",
                    "content": getattr(item, "content", "") or "",
                }
            )
        return serialized

    def _build_rewrite_prompt(self, question: str, context: str) -> str:
        """构建用于查询重写的Prompt
        
        Args:
            question: 用户原始问题
            context: 聊天上下文
            
        Returns:
            格式化的提示词
        """
        return textwrap.dedent(
            f"""
            {self._identity_header()}
            [任务]
            你是一个专业的搜索查询分析师。你的任务是根据用户当前的提问和最近的聊天记录，生成一个最适合在搜索引擎中使用的高效、精确的关键词。

            [聊天记录]
            {context}

            [用户当前提问]
            {question}

            [要求]
            1.  分析聊天记录和当前提问，理解用户的真实意图。
            2.  如果当前提问已经足够清晰，直接使用它或稍作优化。
            3.  如果提问模糊（如使用了“它”、“那个”等代词），请从聊天记录中找出指代对象，并构成一个完整的查询。
            4.  如果分析后认为用户的问题不需要联网搜索就能回答（例如，只是简单的打招呼），请直接输出"无需搜索"。
            5.  输出的关键词应该简洁、明确，适合搜索引擎。

            [输出]
            请只输出最终的搜索关键词，不要包含任何其他解释或说明。
            """
        ).strip()

    def _build_summarize_prompt(self, original_question: str, search_query: str, results: List[SearchResult]) -> str:
        """构建用于总结搜索结果的Prompt
        
        Args:
            original_question: 用户原始问题
            search_query: 重写后的搜索关键词
            results: 搜索结果列表
            
        Returns:
            格式化的提示词
        """
        formatted_results = self._format_results(results)
        return textwrap.dedent(
            f"""
            {self._identity_header()}
            [任务]
            你是一个专业的网络信息整合专家。你的任务是根据用户原始问题和一系列从互联网上搜索到的资料，给出一个全面、准确、简洁的回答。

            [用户原始问题]
            {original_question}

            [你用于搜索的关键词]
            {search_query}

            [搜索到的资料]
            {formatted_results}

            [要求]
            1.  仔细阅读所有资料，并围绕用户的原始问题进行回答。
            2.  答案应该自然流畅，像是你自己总结的，而不是简单的资料拼接。
            3.  如果资料中有相互矛盾的信息，请客观地指出来。
            4.  如果资料不足以回答问题，请诚实地说明。
            5.  新闻或实时信息可能比模型训练时间新，不要因为时间新就认为是虚构内容。
            6.  不要在回答中提及你查阅了资料，直接给出答案。

            [你的回答]
            """
        ).strip()

    
    async def _search_with_fallback(self, query: str, num_results: int) -> List[SearchResult]:
        """带降级的搜索
        
        Args:
            query: 搜索关键词
            num_results: 期望返回的结果数量
            
        Returns:
            搜索结果列表，如果所有引擎都失败则返回空列表
        """
        engines_config = self.plugin_config.get("engines", {})
        
        # 获取默认搜索引擎顺序
        default_engine = self.backend_config.get("default_engine", "google")
        
        # 定义搜索引擎顺序
        all_engines = [
            ("tavily", self.tavily),
            ("google", self.google),
            ("bing", self.bing),
            ("duckduckgo", self.duckduckgo),
            ("sogou", self.sogo),
        ]
        if default_engine in dict(all_engines):
            engine_order = [pair for pair in all_engines if pair[0] == default_engine]
            engine_order.extend(pair for pair in all_engines if pair[0] != default_engine)
        else:
            engine_order = all_engines
        
        # 按顺序尝试搜索引擎
        for engine_name, engine in engine_order:
            # 检查引擎是否启用（平铺字段），缺省时依据默认：google/tavily 默认禁用，其余启用
            is_enabled = engines_config.get(f"{engine_name}_enabled")
            if is_enabled is None:
                defaults = {"google": False, "tavily": False}
                is_enabled = defaults.get(engine_name, True)
            if not is_enabled:
                logger.info(f"搜索引擎 {engine_name} 已禁用，跳过")
                continue
            if engine_name == "tavily" and not (hasattr(self.tavily, "has_api_keys") and self.tavily.has_api_keys()):
                logger.info("Tavily 搜索未配置 API key，跳过调用")
                continue
                
            try:
                # 调用基类中统一的、带重试的方法
                results = await engine.search(query, num_results)
                if results:
                    logger.info(f"{engine_name} 搜索成功，返回 {len(results)} 条结果")
                    self.last_success_engine = engine_name
                    if engine_name == "tavily":
                        self.last_tavily_answer = getattr(engine, "last_answer", None)
                    else:
                        self.last_tavily_answer = None
                    return results
            except Exception as e:
                logger.warning(f"{engine_name} 搜索失败: {e}")
        return []
    
    async def _fetch_page_content(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """抓取单个页面的正文内容

        Args:
            session: aiohttp会话对象
            url: 待抓取的URL
            
        Returns:
            提取的正文内容，失败时返回None
        """
        timeout = self.backend_config.get("content_timeout", 10)
        max_length = self.backend_config.get("max_content_length", 3000)
        
        try:
            # 从配置中获取 User-Agent 列表，如果不存在则使用一个默认值
            user_agents = self.backend_config.get("user_agents", [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ])
            headers = {"User-Agent": random.choice(user_agents)}
            
            request_kwargs = {
                "timeout": timeout,
                "headers": headers,
            }
            proxy = self.backend_config.get("proxy")
            if proxy:
                request_kwargs["proxy"] = proxy

            async with session.get(url, **request_kwargs) as response:
                if response.status != 200:
                    logger.warning(f"抓取内容失败，URL: {url}, 状态码: {response.status}")
                    return None
                
                # 智能解码
                html_bytes = await response.read()
                try:
                    # 尝试使用 aiohttp 推断的编码
                    html = html_bytes.decode(response.charset or 'utf-8')
                except (UnicodeDecodeError, TypeError):
                    # 如果失败，尝试 gbk
                    try:
                        html = html_bytes.decode('gbk', errors='ignore')
                    except UnicodeDecodeError:
                        # 最终回退
                        html = html_bytes.decode('utf-8', errors='ignore')
                
                # 内容提取
                
                # 使用 trafilatura
                try:
                    import trafilatura
                    extracted_text = trafilatura.extract(
                        html,
                        include_comments=False,
                        include_tables=True,
                        no_fallback=False
                    )
                    if extracted_text and len(extracted_text.strip()) > 100:
                        logger.debug(f"使用trafilatura成功提取内容: {url}")
                        return extracted_text.strip()[:max_length]
                except ImportError:
                    logger.debug("trafilatura未安装，跳过")
                except Exception as e:
                    logger.debug(f"trafilatura提取失败: {e}")
                
                # 使用 readability-lxml
                try:
                    doc = Document(
                        html,
                        min_text_length=50,
                        retry_length=250,
                        url=url
                    )
                    summary_html = doc.summary()
                    soup = BeautifulSoup(summary_html, 'lxml')
                    readability_text = soup.get_text(separator='\n', strip=True)
                    
                    if readability_text and len(readability_text) > 100:
                        logger.debug(f"使用readability成功提取内容: {url}")
                        return readability_text[:max_length]
                except Exception as e:
                    logger.debug(f"readability提取失败: {e}")
                
                # 使用 BeautifulSoup
                try:
                    soup = BeautifulSoup(html, 'lxml')
                    # 移除无用标签
                    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                        tag.decompose()
                    fallback_text = soup.get_text(separator='\n', strip=True)
                    
                    logger.debug(f"使用BeautifulSoup兜底提取: {url}")
                    return fallback_text[:max_length] if fallback_text else None
                except Exception as e:
                    logger.error(f"BeautifulSoup提取也失败: {e}")
                    return None
                
        except asyncio.TimeoutError:
            logger.warning(f"抓取内容超时: {url}")
            return None
        except Exception as e:
            logger.error(f"抓取内容时发生未知错误: {url}, 错误: {e}")
            return None

    async def _fetch_content_for_results(self, results: List[SearchResult]) -> List[SearchResult]:
        """为搜索结果并发抓取内容，增强了异常处理和格式化
        
        Args:
            results: 搜索结果列表
            
        Returns:
            补充了内容的搜索结果列表
        """

        urls_to_fetch = [result.url for result in results if result.url]
        if not urls_to_fetch:
            return results

        async with aiohttp.ClientSession(trust_env=True) as session:
            tasks = [self._fetch_page_content(session, url) for url in urls_to_fetch]
            content_results = await asyncio.gather(*tasks, return_exceptions=True)

            content_idx = 0
            for result in results:
                if result.url:
                    if content_idx < len(content_results):
                        content_or_exc = content_results[content_idx]
                        
                        if isinstance(content_or_exc, str) and content_or_exc:
                            result.abstract = f"{result.abstract}\n{content_or_exc}"
                        elif isinstance(content_or_exc, Exception):
                            logger.warning(f"抓取 {result.url} 内容时发生异常: {content_or_exc}")
                        
                        content_idx += 1
        
        return results

    def _integrate_inline_content(self, results: List[SearchResult]) -> None:
        """Integrate Tavily-provided content without extra crawling."""
        if not results:
            return

        if self.last_tavily_answer:
            summarized = self.last_tavily_answer.strip()
            if summarized:
                answer_result = SearchResult(
                    title="Tavily Summary",
                    url="",
                    snippet=summarized,
                    abstract=summarized,
                    rank=-1,
                    content=summarized,
                )
                results.insert(0, answer_result)

        for result in results:
            content = (result.content or "").strip()
            if not content:
                continue
            if result.abstract:
                if content not in result.abstract:
                    result.abstract = f"{result.abstract}\n{content}"
            else:
                result.abstract = content
    
    def _format_results(self, results: List[SearchResult]) -> str:
        """格式化搜索结果
        
        Args:
            results: 搜索结果列表
            
        Returns:
            格式化后的文本字符串
        """
        lines = []
        
        for idx, result in enumerate(results, start=1):
            # 标题行
            header = f"{idx}. {result.title}"
            if result.url:
                header += f" {result.url}"
            lines.append(header)
            
            # 摘要
            if result.abstract:
                lines.append(result.abstract)
            
            # 空行分隔
            lines.append("")
        
        return "\n".join(lines).strip()


class ImageSearchAction(BaseAction):
    """图片搜索动作"""
    
    action_name: str = "image_search"
    action_description: str = "当用户明确需要搜索图片时使用此动作。例如：'搜索一下猫的图片'、'来张风景图'。"
    
    # 激活类型：让LLM来判断是否需要搜索图片
    activation_type: ActionActivationType = ActionActivationType.LLM_JUDGE
    
    # 关联类型：这个Action会发送图片
    associated_types: List[str] = ["image"]
    
    # LLM决策所需参数
    action_parameters: Dict[str, str] = {
        "query": "需要搜索的图片关键词"
    }
    
    # LLM决策使用场景
    action_require: List[str] = [
        "当用户明确表示想看、想搜索或想要一张图片时使用。",
        "适用于'搜/找/来一张/发一张xx的图片'等指令。",
        "如果用户只是在普通聊天中提到了某个事物，不代表他想要图片，此时不应使用。",
        "一次只发送一张最相关的图片。"
    ]
    
    # 实例属性
    enabled: bool
    bing: BingEngine
    sogou: SogouEngine
    duckduckgo: DuckDuckGoEngine
    backend_config: Dict[str, Any]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._image_history: Dict[str, Deque[str]] = {}
        self._image_history_max_size: int = 30

        # 检查是否启用图片搜索功能
        enabled_value = self.get_config("actions.image_search_enabled", False)
        self.enabled = bool(enabled_value) if enabled_value is not None else False

        if not self.enabled:
            logger.info("图片搜索功能已在配置中禁用")
            return

        # 仅在启用时初始化引擎
        config = self.plugin_config
        engines_config = config.get("engines", {})
        backend_config = config.get("search_backend", {})
        common_config = {
            "timeout": backend_config.get("timeout", 20),
            "proxy": backend_config.get("proxy")
        }

        # 初始化所有图片搜索引擎（Bing和搜狗国内可直接访问）
        bing_config = _build_engine_config("bing", engines_config, common_config)
        sogou_config = _build_engine_config("sogou", engines_config, common_config)
        duckduckgo_config = _build_engine_config("duckduckgo", engines_config, common_config)

        self.bing = BingEngine(bing_config)
        self.sogou = SogouEngine(sogou_config)
        self.duckduckgo = DuckDuckGoEngine(duckduckgo_config)

        self.backend_config = config.get("search_backend", {})
        max_results = self.backend_config.get("max_results")
        if isinstance(max_results, int) and max_results > 0:
            # 允许缓存比配置更多的结果，以便轮换图片
            self._image_history_max_size = max(10, max_results * 3)

    async def execute(self) -> Tuple[bool, str]:
        """执行图片搜索并直接发送图片
        
        Returns:
            (是否成功, 状态描述) 的元组
        """
        # 检查是否启用
        if not getattr(self, 'enabled', False):
            await self.send_text(
                "图片搜索功能当前未启用。如需使用，请在配置文件中启用此功能。",
                set_reply=True,
                reply_message=self.action_message
            )
            return False, "图片搜索功能未启用"

        query = self.action_data.get("query", "").strip()
        if not query:
            await self.send_text("你想搜什么图片呀？", set_reply=True, reply_message=self.action_message)
            return False, "关键词为空"

        try:
            logger.info(f"开始执行图片搜索动作，关键词: {query}")
            num_results = self.backend_config.get("max_results", 10)

            # 按顺序尝试搜索引擎：Bing -> 搜狗 -> DuckDuckGo
            # Bing和搜狗国内可直接访问，DuckDuckGo需要科学上网
            image_results = []
            engines = [
                (self.bing, "Bing"),
                (self.sogou, "搜狗"),
                (self.duckduckgo, "DuckDuckGo")
            ]

            for engine, name in engines:
                try:
                    logger.info(f"尝试使用{name}搜索图片: {query}")
                    image_results = await engine.search_images(query, num_results)
                    if image_results:
                        logger.info(f"{name}图片搜索成功，找到 {len(image_results)} 张图片")
                        break
                    logger.info(f"{name}未找到结果，尝试下一个引擎")
                except Exception as e:
                    logger.warning(f"{name}图片搜索失败: {e}，尝试下一个引擎", exc_info=True)
                    continue

            if not image_results:
                await self.send_text(f"我没找到关于「{query}」的图片呢。", set_reply=True, reply_message=self.action_message)
                return False, "未找到图片"

            # 过滤掉None值和重复项，确保类型安全
            image_urls: List[str] = []
            seen_urls: set[str] = set()
            for item in image_results:
                url = item.get('image')
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                image_urls.append(url)
            if not image_urls:
                await self.send_text("虽然找到了结果，但好像没有有效的图片地址。", set_reply=True, reply_message=self.action_message)
                return False, "无有效图片地址"

            history = self._image_history.get(query)
            if history is None:
                history = deque(maxlen=self._image_history_max_size)
                self._image_history[query] = history

            candidate_urls = [url for url in image_urls if url not in history]
            if not candidate_urls:
                history.clear()
                candidate_urls = image_urls
                ordered_urls = candidate_urls
            else:
                ordered_urls = candidate_urls + [url for url in image_urls if url not in candidate_urls]

            async def _fetch_image(session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                        if response.status == 200:
                            return await response.read()
                except Exception as e:
                    logger.warning(f"下载图片失败: {url}, 错误: {e}")
                return None

            # 尝试下载并发送第一张成功的图片
            async with aiohttp.ClientSession(trust_env=True) as session:
                for url in ordered_urls:
                    if not url:  # 额外的安全检查
                        continue
                    image_data = await _fetch_image(session, url)
                    if image_data:
                        # 编码为base64
                        b64_data = base64.b64encode(image_data).decode('utf-8')
                        # 发送图片
                        success = await self.send_image(b64_data, set_reply=True, reply_message=self.action_message)
                        if success:
                            history.append(url)
                            logger.info(f"成功发送了关于「{query}」的图片。")
                            return True, "图片发送成功"
                        else:
                            history.append(url)
                            logger.error("调用 send_image 失败。")
                            # 即使发送失败也停止，避免发送多张
                            await self.send_text("我下载好了图片，但是发送失败了...", set_reply=True, reply_message=self.action_message)
                            return False, "发送图片API失败"
            
            # 如果循环结束都没有成功下载和发送
            await self.send_text("找到了图片，但下载都失败了，可能是网络问题。", set_reply=True, reply_message=self.action_message)
            return False, "所有图片下载失败"

        except Exception as e:
            logger.error(f"图片搜索动作过程中出现异常: {e}", exc_info=True)
            await self.send_text(f"搜索图片时出错了：{str(e)}", set_reply=True, reply_message=self.action_message)
            return False, f"图片搜索失败: {str(e)}"


@register_plugin
class google_search_simple(BasePlugin):
    """Google Search 插件"""
    
    plugin_name: str = "google_search"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = [
        "aiohttp>=3.8.0",
        "beautifulsoup4>=4.11.0",
        "lxml>=4.9.0",
        "httpx>=0.25.0",
        "readability-lxml>=0.8.1",
        "googlesearch-python>=1.2.3",
        "ddgs",
        "trafilatura>=1.6.0",
    ]
    config_file_name: str = "config.toml"

    config_schema: Dict[str, Dict[str, ConfigField]] = {
        "plugin": {
            "name": ConfigField(type=str, default="google_search", description="插件名称"),
            "version": ConfigField(type=str, default="3.2.0", description="插件版本"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
        },
        "model_config": {
            "model_name": ConfigField(
                type=str,
                default="replyer",
                description="指定用于搜索和总结的系统模型名称。默认为 'replyer'，即系统主回复模型。",
                choices=[
                    "replyer",
                    "utils",
                    "utils_small",
                    "tool_use",
                    "planner",
                    "vlm",
                    "lpmm_entity_extract",
                    "lpmm_rdf_build",
                    "lpmm_qa",
                ],
            ),
            "temperature": ConfigField(type=float, default=0.7, description="模型生成温度。如果留空，则使用所选模型的默认温度。"),
            "context_time_gap": ConfigField(type=int, default=300, description="获取最近多少秒的全局聊天记录作为上下文。"),
            "context_max_limit": ConfigField(type=int, default=15, description="最多获取多少条全局聊天记录作为上下文。"),
        },
        "actions": {
            "image_search_enabled": ConfigField(
                type=bool,
                default=False,
                description="是否启用图片搜索功能。支持Bing、搜狗（国内可直接访问）和DuckDuckGo（需科学上网）三个引擎自动降级。",
            ),
        },
        "search_backend": {
            "default_engine": ConfigField(
                type=str,
                default="bing",
                description="默认搜索引擎 (google/bing/sogou/duckduckgo/tavily)",
                choices=["google", "bing", "sogou", "duckduckgo", "tavily"],
            ),
            "max_results": ConfigField(type=int, default=15, description="默认返回结果数量"),
            "timeout": ConfigField(type=int, default=20, description="搜索超时时间（秒）"),
            "proxy": ConfigField(type=str, default="", description="用于搜索的HTTP/HTTPS代理地址，例如 'http://127.0.0.1:7890'。如果留空则不使用代理。"),
            "fetch_content": ConfigField(type=bool, default=True, description="是否抓取网页内容"),
            "content_timeout": ConfigField(type=int, default=10, description="内容抓取超时（秒）"),
            "max_content_length": ConfigField(type=int, default=3000, description="最大内容长度"),
            "user_agents": ConfigField(
                type=list,
                default=[
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
                ],
                description="抓取网页时使用的 User-Agent 列表，会从中随机选择。"
            ),
        },
        "engines": {
            "google_enabled": ConfigField(type=bool, default=False, description="是否启用Google搜索"),
            "google_language": ConfigField(type=str, default="zh-cn", description="搜索语言"),
            "bing_enabled": ConfigField(type=bool, default=True, description="是否启用Bing搜索"),
            "bing_region": ConfigField(type=str, default="zh-CN", description="Bing搜索区域代码"),
            "sogou_enabled": ConfigField(type=bool, default=True, description="是否启用搜狗搜索"),
            "duckduckgo_enabled": ConfigField(type=bool, default=True, description="是否启用DDGS元搜索引擎"),
            "duckduckgo_region": ConfigField(type=str, default="wt-wt", description="搜索区域代码，例如 'us-en' 或 'cn-zh'"),
            "duckduckgo_backend": ConfigField(type=str, default="auto", description="使用的后端。'auto' 表示自动选择，也可以指定多个，如 'duckduckgo,google,brave'"),
            "duckduckgo_safesearch": ConfigField(type=str, default="moderate", choices=["on", "moderate", "off"], description="安全搜索级别"),
            "duckduckgo_timelimit": ConfigField(
                type=str,
                default="none",
                description="时间限制 (d, w, m, y；none 表示不限)",
                choices=["none", "d", "w", "m", "y"],
            ),
            "tavily_enabled": ConfigField(type=bool, default=False, description="是否启用 Tavily 搜索"),
            "tavily_api_keys": ConfigField(type=list, default=[], description="Tavily API key 列表，填写多个时用,分隔即可，随机选用"),
            "tavily_api_key": ConfigField(type=str, default="", description="Tavily API key；留空则使用环境变量 TAVILY_API_KEY"),
            "tavily_search_depth": ConfigField(type=str, default="basic", choices=["basic", "advanced"], description="搜索深度"),
            "tavily_include_raw_content": ConfigField(type=bool, default=False, description="是否返回网页原始内容"),
            "tavily_include_answer": ConfigField(type=bool, default=True, description="是否返回 Tavily 生成的答案"),
            "tavily_topic": ConfigField(type=str, default="", description="可选的主题参数，例如 'general' 或 'news'"),
            "tavily_turbo": ConfigField(type=bool, default=False, description="是否启用 Tavily Turbo 模式"),
        },
        "storage": {
            "enable_store": ConfigField(type=bool, default=True, description="是否将搜索结果写入 chat_history"),
            "store_top_k": ConfigField(type=int, default=5, description="每次写入的搜索结果条数上限"),
            "dedup_window_seconds": ConfigField(type=int, default=600, description="同一主题写入的去重时间窗口（秒），0 表示不去重"),
            "final_answer_max_len": ConfigField(
                type=int,
                default=1200,
                description="original_text 中 final_answer 的截断长度，0 表示不截断"
            ),
        },
    }
    
    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """获取插件提供的组件"""
        components = [
            (WebSearchTool.get_tool_info(), WebSearchTool),
            (AbbreviationTool.get_tool_info(), AbbreviationTool),
        ]
        
        # 仅在配置启用时注册图片搜索动作
        if self.get_config("actions.image_search_enabled", False):
            components.append((ImageSearchAction.get_action_info(), ImageSearchAction))
            logger.info(f"{self.log_prefix} 图片搜索功能已启用并注册")
        else:
            logger.info(f"{self.log_prefix} 图片搜索功能未启用，跳过注册")
        
        return components

    def _load_plugin_config(self) -> None:
        """使用基类加载配置（平铺 schema 无需自定义处理）。"""
        super()._load_plugin_config()
