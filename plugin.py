import os
import inspect
import re
import asyncio
import random
from typing import List, Tuple, Type, Dict, Any, Optional
from urllib.parse import urlparse, unquote, parse_qs, parse_qsl, urlencode
from dataclasses import dataclass
from enum import Enum

import aiohttp
from bs4 import BeautifulSoup, FeatureNotFound

from src.common.logger import get_logger
from src.plugin_system import (
    BasePlugin,
    register_plugin,
    BaseTool,
    ComponentInfo,
    ConfigField,
    ToolParamType,
)

logger = get_logger("google_search")

# ==================== 常量定义 ====================

class SearchEngine(Enum):
    """搜索引擎类型枚举"""
    BAIDU = "baidu"
    BING = "bing"
    GOOGLE = "google"
    SOGOU = "sogou"
    SO = "so"
    UNKNOWN = "unknown"

# User-Agent 池，用于模拟不同浏览器
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# 搜索引擎域名映射
SEARCH_ENGINE_HOSTS = {
    SearchEngine.BAIDU: {"www.baidu.com", "m.baidu.com", "baidu.com"},
    SearchEngine.BING: {"www.bing.com", "cn.bing.com", "m.bing.com"},
    SearchEngine.GOOGLE: {"www.google.com", "www.google.com.hk", "www.google.com.sg"},
    SearchEngine.SOGOU: {"www.sogou.com"},
    SearchEngine.SO: {"so.com", "www.so.com"},
}

# 所有搜索引擎域名集合
ALL_SERP_HOSTS = set().union(*SEARCH_ENGINE_HOSTS.values())
ALL_SERP_HOSTS.update({"duckduckgo.com", "search.yahoo.com"})

# ==================== 数据类定义 ====================

@dataclass
class SearchResult:
    """搜索结果数据类"""
    title: str
    url: str
    abstract: str = ""
    rank: int = 0
    content: str = ""

@dataclass
class TextQualityMetrics:
    """文本质量度量指标"""
    length: int
    sentence_count: int
    punctuation_density: float
    unique_ratio: float
    score: float

# ==================== 工具函数 ====================

class TextProcessor:
    """文本处理工具类"""
    
    # 编译正则表达式以提高性能
    ZH_PUNCT_PATTERN = re.compile(r"[，。！？；、,.!?;]")
    WHITESPACE_PATTERN = re.compile(r"\s+")
    SENTENCE_SPLIT_PATTERN = re.compile(r"[。！？!?；;]+")
    
    @classmethod
    def normalize_url_key(cls, url: str) -> str:
        """标准化URL用于去重"""
        try:
            p = urlparse(url)
            base = f"{p.netloc.lower()}{p.path.rstrip('/')}"
            q = [(k.lower(), v) for k, v in parse_qsl(p.query, keep_blank_values=True)]
            q = [(k, v) for k, v in q if not (k.startswith("utm_") or k in {"ref", "source", "from", "spm"})]
            if q:
                q.sort()
                return f"{base}?{urlencode(q)}"
            return base
        except Exception:
            return url
    
    @classmethod
    def tidy_text(cls, text: str) -> str:
        """清理文本：去除多余空白字符"""
        if not text:
            return ""
        return " ".join(text.replace("\r", " ").replace("\n", " ").split())
    
    @classmethod
    def trim_text(cls, text: str, max_length: int) -> str:
        """截断文本到指定长度"""
        if len(text) <= max_length:
            return text
        return text[:max_length].rstrip() + "..."
    
    @classmethod
    def count_sentences(cls, text: str) -> int:
        """统计句子数量"""
        sentences = cls.SENTENCE_SPLIT_PATTERN.split(text)
        return len([s for s in sentences if s.strip()])
    
    @classmethod
    def deduplicate_lines(cls, text: str) -> str:
        """行级去重，保持顺序"""
        seen = set()
        output_lines = []
        
        for line in text.splitlines():
            # 清理行
            cleaned = cls.WHITESPACE_PATTERN.sub(" ", line.strip())
            
            # 过滤条件
            if not cleaned or len(cleaned) < 5 or cleaned in seen:
                continue
                
            seen.add(cleaned)
            output_lines.append(cleaned)
        
        return "\n".join(output_lines)

class ConfigParser:
    """配置解析工具类"""
    
    @staticmethod
    def as_bool(value: Any, default: bool = False) -> bool:
        """转换为布尔值"""
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "y", "on"}:
                return True
            if normalized in {"0", "false", "no", "n", "off"}:
                return False
        return default
    
    @staticmethod
    def as_int(value: Any, default: int = 0) -> int:
        """转换为整数"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

class URLAnalyzer:
    """URL分析工具类"""
    
    @staticmethod
    def identify_search_engine(url: str) -> SearchEngine:
        """识别搜索引擎类型"""
        try:
            host = urlparse(url).netloc.lower()
            for engine, hosts in SEARCH_ENGINE_HOSTS.items():
                if host in hosts:
                    return engine
        except Exception:
            pass
        return SearchEngine.UNKNOWN
    
    @staticmethod
    def is_search_result_page(url: str) -> bool:
        """判断是否为搜索结果页面"""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.lower()
            path = parsed.path.lower()
            
            # 检查域名
            if host in ALL_SERP_HOSTS:
                return True
            
            # 检查典型搜索路径
            search_paths = ["/search", "/s", "/results"]
            return any(segment in path for segment in search_paths)
            
        except Exception:
            return False
    
    @staticmethod
    def generate_title_from_url(url: str) -> str:
        """从URL生成标题"""
        try:
            parsed = urlparse(url)
            host = parsed.netloc.replace("www.", "")
            
            # 从路径提取最后一段
            path_segments = parsed.path.strip("/").split("/")
            if path_segments and path_segments[-1]:
                last_segment = unquote(path_segments[-1])  #解码 URL
                hint = last_segment.replace("-", " ").replace("_", " ")
                return f"{host}: {hint}"
            
            return host or url
        except Exception:
            return url

# ==================== 内容提取器 ====================

class ContentExtractor:
    """网页内容提取器"""
    
    def __init__(self, max_chars: int = 700):
        self.max_chars = max_chars
        self.text_processor = TextProcessor()
    
    def calculate_link_density(self, node) -> float:
        """计算节点的链接密度"""
        try:
            text = node.get_text(" ", strip=True) or ""
            text_length = len(text)
            
            if text_length == 0:
                return 0.0
            
            # 计算所有链接文本长度
            link_text_length = sum(
                len(a.get_text(" ", strip=True) or "") 
                for a in node.find_all("a")
            )
            
            return link_text_length / text_length
        except Exception:
            return 1.0
    
    def evaluate_text_quality(self, text: str) -> TextQualityMetrics:
        """评估文本质量"""
        text = self.text_processor.tidy_text(text)
        length = len(text)
        
        if length == 0:
            return TextQualityMetrics(0, 0, 0.0, 0.0, 0.0)
        
        # 计算各项指标
        punctuation_count = len(self.text_processor.ZH_PUNCT_PATTERN.findall(text))
        sentence_count = self.text_processor.count_sentences(text)
        unique_ratio = len(set(text)) / length
        punctuation_density = punctuation_count / length
        
        # 计算综合评分
        score = self._calculate_quality_score(
            length, sentence_count, punctuation_density, unique_ratio
        )
        
        return TextQualityMetrics(
            length=length,
            sentence_count=sentence_count,
            punctuation_density=punctuation_density,
            unique_ratio=unique_ratio,
            score=score
        )
    
    def _calculate_quality_score(
        self, 
        length: int, 
        sentences: int, 
        punct_density: float, 
        unique_ratio: float
    ) -> float:
        """计算文本质量分数"""
        score = 0.0
        
        # 长度分数 (最高1分)
        score += min(length / 500.0, 1.0)
        
        # 句子数分数 (最高1分)
        score += min(sentences / 5.0, 1.0)
        
        # 标点密度分数 (0.5分)
        if 0.005 <= punct_density <= 0.12:
            score += 0.5
        
        # 字符多样性分数 (0.5分)
        if 0.1 <= unique_ratio <= 0.9:
            score += 0.5
        
        return score
    
    def passes_quality_threshold(
        self,
        text: str,
        min_chars: int = 200,
        min_sentences: int = 2,
        min_score: float = 1.0
    ) -> bool:
        """检查文本是否通过质量阈值"""
        text = self.text_processor.tidy_text(text)
        
        # 检测英文比例，动态调整阈值
        ascii_ratio = sum(1 for c in text if ord(c) < 128) / max(len(text), 1)
        if ascii_ratio > 0.7:  # 主要是英文
            min_chars = int(min_chars * 0.8)  # 降低20%长度要求
        
        # 基本长度检查
        if len(text) < min_chars:
            return False
        
        # 句子数检查
        if self.text_processor.count_sentences(text) < min_sentences:
            return False
        
        # 质量分数检查
        metrics = self.evaluate_text_quality(text)
        return metrics.score >= min_score
    
    def extract_main_content(self, html: str) -> str:
        """从HTML提取主要内容"""
        if not html:
            return ""
        
        soup = BeautifulSoup(html, "html.parser")
        
        # 步骤1: 移除噪声元素
        self._remove_noise_elements(soup)
        
        # 步骤2: 查找候选内容块
        candidates = self._find_content_candidates(soup)
        
        if not candidates:
            # 兜底：返回全部文本
            body = soup.body or soup
            return self.text_processor.tidy_text(body.get_text(" ", strip=True))
        
        # 步骤3: 选择最佳候选并清理
        best_node = candidates[0][1]
        self._clean_node(best_node)
        
        # 步骤4: 提取并处理文本
        text = best_node.get_text("\n", strip=True)
        return self.text_processor.deduplicate_lines(text)
    
    def _remove_noise_elements(self, soup: BeautifulSoup) -> None:
        """移除噪声HTML元素"""
        # 移除脚本、样式等非内容元素
        noise_tags = [
            "script", "style", "noscript", "template", 
            "canvas", "svg", "iframe", "form",
            "header", "nav", "footer", "aside", "ins"
        ]
        for tag in soup(noise_tags):
            tag.decompose()
        
        # 移除隐藏元素
        hidden_selectors = [
            "[hidden]",
            "[aria-hidden=true]",
            "[role=navigation]",
            "[role=banner]",
            "[role=complementary]",
            "[role=contentinfo]"
        ]
        for selector in hidden_selectors:
            for tag in soup.select(selector):
                tag.decompose()
    
    def _find_content_candidates(self, soup: BeautifulSoup) -> List[Tuple[float, Any]]:
        """查找内容候选块"""
        body = soup.body or soup
        candidates = []
        
        # 搜索语义内容标签
        content_tags = ["article", "main", "section", "div"]
        
        for node in body.find_all(content_tags, limit=500):
            score = self._score_content_node(node)
            if score > 0:
                candidates.append((score, node))
        
        # 按分数降序排序
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates
    
    def _score_content_node(self, node) -> float:
        """评分内容节点"""
        text = node.get_text(" ", strip=True) or ""
        
        # 文本太短，跳过
        if len(text) < 80:
            return 0
        
        # 计算各项指标
        link_density = self.calculate_link_density(node)
        paragraph_count = len(node.find_all("p"))
        punctuation_count = len(self.text_processor.ZH_PUNCT_PATTERN.findall(text))
        text_length = len(text)
        list_item_count = len(node.find_all("li"))
        
        # 标签权重
        tag_weight = self._get_tag_weight(node.name)
        
        # 列表惩罚
        list_penalty = min(list_item_count / 20.0, 1.0)
        
        # 综合评分
        score = (
            (text_length / 1000.0) * (1 - link_density) ** 2 +
            (paragraph_count * 0.6) +
            (punctuation_count * 0.03) +
            tag_weight -
            list_penalty
        )
        
        return max(score, 0)
    
    def _get_tag_weight(self, tag_name: str) -> float:
        """获取标签权重"""
        weights = {
            "article": 2.0,
            "main": 2.0,
            "section": 0.5,
            "div": 0.0
        }
        return weights.get(tag_name, 0.0)
    
    def _clean_node(self, node) -> None:
        """清理节点中的高链接密度子元素"""
        for child in list(node.find_all(True, recursive=True)):
            try:
                if self.calculate_link_density(child) > 0.5:
                    child.decompose()
            except Exception:
                continue

class SearchResultParser:
    """搜索结果解析器"""
    
    def __init__(self):
        self.url_analyzer = URLAnalyzer()
    
    def extract_links_from_serp(self, html: str, url: str) -> List[str]:
        """从搜索结果页提取链接"""
        if not html:
            return []
        
        # 识别搜索引擎
        engine = self.url_analyzer.identify_search_engine(url)
        host = urlparse(url).netloc.lower()
        
        # 根据不同引擎解析
        parser_map = {
            SearchEngine.BAIDU: self._parse_baidu_results,
            SearchEngine.BING: self._parse_bing_results,
            SearchEngine.GOOGLE: self._parse_google_results,
            SearchEngine.SOGOU: self._parse_sogou_results,
            SearchEngine.SO: self._parse_so_results,
        }
        
        parser = parser_map.get(engine)
        if parser:
            links = parser(html, host)
        else:
            links = []
        
        # 去重并返回
        return self._deduplicate_links(links)
    
    def _parse_baidu_results(self, html: str, host: str) -> List[str]:
        """解析百度搜索结果"""
        links = []
        soup = BeautifulSoup(html, "html.parser")
        
        selectors = [
            "#content_left h3 a",
            "h3.t a",
            "div.result h3 a"
        ]
        
        for selector in selectors:
            for anchor in soup.select(selector)[:5]:
                link = self._extract_link(anchor, host)
                if link:
                    links.append(link)
        
        return links
    
    def _parse_bing_results(self, html: str, host: str) -> List[str]:
        """解析必应搜索结果"""
        links = []
        soup = BeautifulSoup(html, "html.parser")
        
        for anchor in soup.select("ol#b_results li.b_algo h2 a")[:5]:
            link = self._extract_link(anchor, host)
            if link:
                links.append(link)
        
        return links
    
    def _parse_google_results(self, html: str, host: str) -> List[str]:
        """解析谷歌搜索结果"""
        links = []
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        
        selectors = [
            "div.yuRUbf > a",
            "div.g a[href]:has(h3)",
            "a[href] > h3",
            "div#search h3",
            "div.g a[href][ping]",
        ]
        
        for selector in selectors:
            try:
                if selector == "div#search h3":
                    # 特殊处理：找h3的父链接
                    for h3 in soup.select(selector)[:8]:
                        anchor = h3.find_parent("a")
                        link = self._extract_link(anchor, host)
                        if link and link not in seen:
                            seen.add(link)
                            links.append(link)
                else:
                    # 通用处理：确保拿到a标签
                    for elem in soup.select(selector)[:8]:
                        a = elem if elem.name == "a" else elem.find_parent("a")
                        link = self._extract_link(a, host)
                        if link and link not in seen:
                            seen.add(link)
                            links.append(link)
            except Exception as e:
                logger.debug(f"Selector {selector} failed: {e}")
                continue
            
            if len(links) >= 5:
                break
        
        return links[:5]
        
    def _parse_sogou_results(self, html: str, host: str) -> List[str]:
        """解析搜狗搜索结果"""
        links = []
        soup = BeautifulSoup(html, "html.parser")
        
        selectors = ["div.results h3 a", "h3.vr-title a"]
        
        for selector in selectors:
            for anchor in soup.select(selector)[:5]:
                link = self._extract_link(anchor, host)
                if link:
                    links.append(link)
        
        return links
    
    def _parse_so_results(self, html: str, host: str) -> List[str]:
        """解析360搜索结果"""
        links = []
        soup = BeautifulSoup(html, "html.parser")
        
        selectors = ["ul#m-result li h3 a", "div.result h3 a"]
        
        for selector in selectors:
            for anchor in soup.select(selector)[:5]:
                link = self._extract_link(anchor, host)
                if link:
                    links.append(link)
        
        return links
    
    def _extract_link(self, anchor, host: str) -> Optional[str]:
        if not anchor:
            return None
        href = anchor.get("href") or ""
        if not href or href.startswith("javascript:") or href == "#":
            return None
        # 绝对化
        if href.startswith("/"):
            href = f"https://{host}{href}"

        try:
            p = urlparse(href)
            h = p.netloc.lower()
            # Google 重定向
            if "google." in h and p.path.startswith("/url"):
                qs = parse_qs(p.query)
                target = (qs.get("q") or qs.get("url") or [None])[0]
                if target:
                    return target
            # Bing 重定向
            if "bing.com" in h and p.path.startswith("/aclk"):
                qs = parse_qs(p.query)
                target = (qs.get("u") or [None])[0]
                if target:
                    return target
            # Yahoo（简单处理）
            if "yahoo." in h and p.path.startswith("/r"):
                qs = parse_qs(p.query)
                target = (qs.get("p") or [None])[0]
                if target:
                    return target
        except Exception:
            pass
        return href
    
    def _deduplicate_links(self, links: List[str]) -> List[str]:
        """去重链接列表"""
        seen = set()
        unique_links = []
        
        for link in links:
            key = TextProcessor.normalize_url_key(link)
            if key not in seen:
                seen.add(key)
                unique_links.append(link)
        
        return unique_links

# ==================== 网页抓取器 ====================

class WebFetcher:
    """网页内容抓取器"""
    
    def __init__(
        self,
        timeout: int = 6,
        max_chars: int = 700,
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None  # 新增
    ):
        # 处理 SOCKS 代理
        self._connector = None
        if proxy and proxy.startswith("socks"):
            try:
                from aiohttp_socks import ProxyConnector
                self._connector = ProxyConnector.from_url(proxy)
                self.proxy = None  # 使用 connector 时不再传 proxy
                logger.debug(f"Using SOCKS proxy: {proxy}")
            except ImportError:
                logger.warning("检测到 SOCKS 代理但未安装 aiohttp-socks，将忽略代理设置")
                self.proxy = None
        else:
            self.proxy = proxy
        self.user_agent = user_agent  # 新增
        self.timeout = timeout
        self.max_chars = max_chars
        self.content_extractor = ContentExtractor(max_chars)
        self.search_parser = SearchResultParser()
        self.url_analyzer = URLAnalyzer()
        self.text_processor = TextProcessor()

    async def fetch_readable_content(
        self,
        session: aiohttp.ClientSession,
        url: str,
        follow_serp: bool = True
    ) -> str:
        """带重试的可读内容抓取"""
        retry_delays = [0, 0.3, 0.8]
        for delay in retry_delays:
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                content = await self._fetch_readable_once(session, url, follow_serp)
                if content:
                    return content
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(f"Fetch attempt failed for {url}: {e}")
                continue
        return ""
    async def _fetch_readable_once(self, session, url, follow_serp=True) -> str:
        """抓取并提取网页可读内容"""
        
        # 获取原始HTML
        html, final_url = await self._fetch_html(session, url)
        if not html:
            return ""
        
        # 如果是搜索结果页且允许跟随，尝试提取第一个结果
        if follow_serp and self.url_analyzer.is_search_result_page(final_url):
            return await self._handle_serp_page(session, html, final_url)
        
        # 提取正文内容
        return await self._extract_content(html)
    
    async def _fetch_html(self, session: aiohttp.ClientSession, url: str) -> Tuple[str, str]:
        headers = {
            "User-Agent": self.user_agent or random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
        
        try:
            async with session.get(
                url,
                headers=headers,
                proxy=self.proxy,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                allow_redirects=True,
                max_redirects=5,
            ) as response:
                # 检查 Content-Type
                content_type = (response.headers.get("Content-Type") or "").lower()
                if content_type and ("text/html" not in content_type and "application/xhtml+xml" not in content_type):
                    logger.debug(f"Skipping non-HTML content: {content_type}")
                    return "", str(response.url)
                # content_type 为空时继续尝试解码          
                # 限制读取大小（2MB）
                max_size = 2_000_000
                data = await response.content.read(max_size + 1)
                if len(data) > max_size:
                    logger.debug(f"Content truncated for {url} (size: {len(data)})")
                    data = data[:max_size]
                
                # 编码检测
                charset = response.charset
                if charset:
                    html = data.decode(charset, errors="ignore")
                else:
                    # 尝试 UTF-8，失败则用 charset-normalizer
                    try:
                        html = data.decode("utf-8")
                    except UnicodeDecodeError:
                        try:
                            import charset_normalizer
                            detected = charset_normalizer.from_bytes(data).best()
                            html = str(detected) if detected else data.decode("utf-8", errors="ignore")
                        except ImportError:
                            html = data.decode("utf-8", errors="ignore")
                
                return html, str(response.url)
                
        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching: {url}")
        except Exception as e:
            logger.debug(f"Error fetching {url}: {e}")
        
        return "", ""
    
    async def _handle_serp_page(self, session, html: str, url: str) -> str:
        """处理搜索结果页"""
        links = self.search_parser.extract_links_from_serp(html, url)
        
        # 尝试前3个链接
        for target_url in links[:3]:
            # 解开百度短链接
            if "baidu.com/link?url=" in target_url:
                target_url = await self._unshorten_baidu_link(session, target_url)
            
            content = await self.fetch_readable_content(
                session, target_url, follow_serp=False
            )
            
            if self.content_extractor.passes_quality_threshold(
                content, min_chars=180, min_sentences=2, min_score=0.9
            ):
                return self.text_processor.trim_text(content, self.max_chars)
        
        return ""

    async def _unshorten_baidu_link(self, session, url: str) -> str:
        """解开百度短链接"""
        try:
            headers = {"User-Agent": self.user_agent or random.choice(USER_AGENTS)}
            async with session.head(
                url,
                headers=headers,  # 添加 headers
                allow_redirects=True,
                timeout=aiohttp.ClientTimeout(total=3),
                proxy=self.proxy
            ) as resp:
                return str(resp.url)
        except Exception:
            return url  # 失败时返回原链接
        
    async def _extract_content(self, html: str) -> str:
        """从HTML提取正文内容"""
        extracted = ""
        
        # 策略1: 使用 trafilatura
        extracted = await self._try_trafilatura(html)
        
        # 策略2: 使用 readability
        if not extracted:
            extracted = await self._try_readability(html)
        
        # 策略3: 使用自定义提取器
        if not extracted:
            extracted = await self._try_custom_extractor(html)
        
        # 最终处理
        if extracted:
            extracted = self.text_processor.deduplicate_lines(extracted)
            return self.text_processor.trim_text(extracted, self.max_chars)
        
        return ""
    
    async def _try_trafilatura(self, html: str) -> str:
        """尝试使用trafilatura提取"""
        try:
            import trafilatura
            
            text = trafilatura.extract(
                html,
                favor_recall=True,
                include_comments=False,
                include_tables=False,
                include_images=False
            ) or ""
            
            text = self.text_processor.tidy_text(text)
            
            if self.content_extractor.passes_quality_threshold(
                text, min_chars=180, min_sentences=2, min_score=0.9
            ):
                return text
                
        except Exception as e:
            logger.debug(f"Trafilatura extraction failed: {e}")
        
        return ""
    
    async def _try_readability(self, html: str) -> str:
        """尝试使用readability提取"""
        try:
            from readability import Document
            
            doc = Document(html)
            summary = doc.summary(html_partial=True)
            
            # 更健壮的解析器选择
            try:
                soup = BeautifulSoup(summary, "lxml")
            except (ImportError, FeatureNotFound):
                soup = BeautifulSoup(summary, "html.parser")# lxml 不可用时回退
                        
            # 移除脚本等
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            
            text = soup.get_text("\n", strip=True)
            text = self.text_processor.deduplicate_lines(text)
            
            if self.content_extractor.passes_quality_threshold(
                text, min_chars=180, min_sentences=2, min_score=0.9
            ):
                return text
                
        except Exception as e:
            logger.debug(f"Readability extraction failed: {e}")
        
        return ""
        
    async def _try_custom_extractor(self, html: str) -> str:
        """尝试使用自定义提取器"""
        try:
            text = self.content_extractor.extract_main_content(html)
            
            if self.content_extractor.passes_quality_threshold(
                text, min_chars=160, min_sentences=2, min_score=0.8
            ):
                return text
                
        except Exception as e:
            logger.debug(f"Custom extraction failed: {e}")
        
        return ""

# ==================== 工具实现 ====================

class WebSearchTool(BaseTool):
    """Web搜索工具 - 进行网络搜索并聚合结果"""
    # 默认配置常量
    DEFAULT_WITH_CONTENT = True
    DEFAULT_SHOW_LINKS = True

    name = "web_search"
    description = "进行网络搜索并聚合结果"
    parameters = [
        ("query", ToolParamType.STRING, "搜索关键词或问题", True, None),
        ("with_content", ToolParamType.BOOLEAN, "是否抓取正文内容", False, None),
        ("max_results", ToolParamType.INTEGER, "返回的结果数量", False, None),
        ("show_links", ToolParamType.BOOLEAN, "结果中是否展示链接", False, None),
    ]
    available_for_llm = True
    
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._initialize_config()
        
    def _initialize_config(self) -> None:
        """初始化配置"""
        # 搜索配置
        self.search_config = self._load_search_config()
        
        # 输出配置
        self.output_config = self._load_output_config()
        
        # 重试配置
        self.retry_config = {
            "max_attempts": 2,
            "retry_delay": 0.7
        }
    def _safe_int(self, value: Any, default: int) -> int:
        """安全的整数转换"""
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    
    def _safe_float(self, value: Any, default: float) -> float:
        """安全的浮点数转换"""
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    
    def _safe_str(self, value: Any, default: str = "") -> str:
        """安全的字符串转换"""
        if value is None:
            return default
        return str(value).strip()
    def _safe_google_search(self, query: str, max_results: int) -> list:
        """安全的 Google 搜索，自动适配参数"""
        from googlesearch import search as google_search
        
        # 检查函数签名，只传支持的参数
        sig = inspect.signature(google_search)
        supported_params = set(sig.parameters.keys())
        
        # 准备基础参数
        base_config = {
            "lang": self.search_config["lang"],
            "timeout": self.search_config["timeout"], 
            "proxy": self.search_config["proxy"] or None,
            "user_agent": self.search_config["user_agent"],
            "tld": self.search_config["tld"],
            "sleep_interval": self.search_config["sleep_interval"],
        }
        
        # 参数同义词映射
        param_mappings = {
            "num_results": ["num_results", "num"],
            "sleep_interval": ["sleep_interval", "pause"],
        }
        
        def build_kwargs(use_advanced=True):
            kwargs = {}
            
            # 添加基础参数（只添加支持的）
            for key, value in base_config.items():
                if key in supported_params and value:
                    kwargs[key] = value
            
            # 添加结果数量参数
            for num_param in param_mappings["num_results"]:
                if num_param in supported_params:
                    kwargs[num_param] = max_results
                    break
            
            # 添加睡眠参数 
            for sleep_param in param_mappings["sleep_interval"]:
                if sleep_param in supported_params:
                    kwargs[sleep_param] = base_config["sleep_interval"]
                    break
            
            # advanced 参数
            if "advanced" in supported_params:
                kwargs["advanced"] = use_advanced
                
            return kwargs
        
        # 尝试不同的参数组合
        attempts = [
            build_kwargs(True),
            build_kwargs(False),
            {"num_results": max_results} if "num_results" in supported_params else {"num": max_results},
            {},
        ]
        
        for attempt_kwargs in attempts:
            try:
                logger.debug(f"Trying Google search with params: {attempt_kwargs}")
                return list(google_search(query, **attempt_kwargs))
            except TypeError as e:
                logger.debug(f"Parameter error: {e}")
                continue
            except Exception as e:
                logger.warning(f"Google search failed for query '{query}': {e}")
                raise e
        
        return []
        
    def _load_search_config(self) -> dict:
        """加载搜索配置"""
        return {
            "tld": self._safe_str(self.get_config("search.tld"), "com"),
            "lang": self._safe_str(self.get_config("search.lang"), "zh-cn"),
            "timeout": self._safe_int(self.get_config("search.timeout"), 10),
            "num_results": self._safe_int(self.get_config("search.num_results"), 8),
            "sleep_interval": self._safe_float(self.get_config("search.sleep_interval"), 0.7),
            "user_agent": self._safe_str(self.get_config("search.user_agent")) or USER_AGENTS[0],
            "proxy": self._get_proxy_config()
        }

    def _load_output_config(self) -> dict:
        """加载输出配置"""
        return {
            "content_max_chars": self._safe_int(self.get_config("output.content_max_chars"), 700),
            "fetch_timeout": self._safe_int(self.get_config("output.fetch_timeout"), 6),
            "fetch_concurrency": self._safe_int(self.get_config("output.fetch_concurrency"), 3)
        }
        
    def _get_proxy_config(self) -> str:
        """获取代理配置"""
        proxy = self.get_config("search.proxy") or ""
        
        if not proxy:
            # 尝试从环境变量获取
            proxy_env_vars = [
                "ALL_PROXY", "all_proxy",
                "HTTPS_PROXY", "https_proxy",
                "HTTP_PROXY", "http_proxy"
            ]
            
            for var in proxy_env_vars:
                proxy = os.environ.get(var, "")
                if proxy:
                    break
        
        return proxy
    
    async def execute(self, function_args: dict) -> dict:
        """执行搜索"""
        try:
            # 解析参数
            params = self._parse_search_params(function_args)
            
            if not params["query"]:
                return self._create_response("查询关键词为空")
            
            # 执行搜索
            results = await self._perform_search_with_retry(
                params["query"],
                params["max_results"]
            )
            
            if not results:
                return self._create_response(
                    f"未找到关于「{params['query']}」的相关信息。"
                )
            
            # 抓取内容（如果需要）
            content_map = {}
            if params["with_content"]:
                content_map = await self._fetch_contents(results, params["max_results"])
            
            # 格式化输出
            output = self._format_results(
                results[:params["max_results"]],
                content_map,
                params["show_links"]
            )
            
            return self._create_response(output)
            
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Web搜索执行异常: {e}", exc_info=True)
            return self._create_response(f"Web搜索失败: {str(e)}")
    
    def _parse_search_params(self, function_args: dict) -> dict:
        """解析搜索参数"""
        config_parser = ConfigParser()
        
        return {
            "query": (function_args.get("query") or "").strip(),
            "with_content": config_parser.as_bool(
                function_args.get("with_content"), self.DEFAULT_WITH_CONTENT
            ),
            "max_results": config_parser.as_int(
                function_args.get("max_results"), self.search_config["num_results"]
                ),
            "show_links": config_parser.as_bool(
                function_args.get("show_links"), self.DEFAULT_SHOW_LINKS
            )
        }
    
    def _create_response(self, content: str) -> dict:
        """创建响应"""
        return {"name": self.name, "content": content}
    
    async def direct_execute(self, **function_args) -> str:
        """直接执行（不需要返回字典）"""
        result = await self.execute(function_args)
        return str(result.get("content", ""))  # 强制为字符串
    
    async def _perform_search_with_retry(
        self,
        query: str,
        max_results: int
    ) -> List[SearchResult]:
        """带重试的搜索执行"""
        last_error = None
        
        for attempt in range(1, self.retry_config["max_attempts"] + 1):
            try:
                return await self._perform_google_search(query, max_results)
                
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last_error = e
                logger.warning(
                    f"搜索失败 (尝试 {attempt}/{self.retry_config['max_attempts']}): {e}"
                )
                
                if attempt < self.retry_config["max_attempts"]:
                    delay = self.retry_config["retry_delay"] * attempt
                    await asyncio.sleep(delay)
        
        if last_error:
            raise last_error
        return []
    
    async def _perform_google_search(
        self,
        query: str,
        max_results: int
    ) -> List[SearchResult]:
        """执行Google搜索"""
        try:
            from googlesearch import search as google_search
        except ImportError as e:
            logger.error("请先安装 googlesearch-python：pip install googlesearch-python")
            raise e
        
        # 在线程中执行阻塞调用
        items = await asyncio.to_thread(
            self._blocking_google_search,
            query,
            max_results
        )
        
        # 转换为SearchResult对象
        return self._convert_to_search_results(items, max_results)
    
    def _blocking_google_search(self, query: str, max_results: int) -> list:
        return self._safe_google_search(query, max_results)
    

    def _convert_to_search_results(
        self,
        items: list,
        max_results: int
    ) -> List[SearchResult]:
        """转换搜索结果为SearchResult对象"""
        results = []
        seen = set()
        text_processor = TextProcessor()
        url_analyzer = URLAnalyzer()
        
        for idx, item in enumerate(items, start=1):
            # 解析结果项
            if isinstance(item, str):
                url = item
                title = url_analyzer.generate_title_from_url(item)
                abstract = ""
            else:
                title = getattr(item, "title", "") or getattr(item, "name", "")
                url = getattr(item, "url", getattr(item, "link", "")) or ""
                abstract = getattr(item, "description", getattr(item, "snippet", "")) or ""
            
            if not url:
                continue
            
            # 去重
            key = text_processor.normalize_url_key(url)
            if key in seen:
                continue
            seen.add(key)
            
            # 创建结果对象
            results.append(SearchResult(
                title=title or url,
                url=url,
                abstract=text_processor.trim_text(abstract, 200),
                rank=idx
            ))
            
            if len(results) >= max_results:
                break
        
        return results
    

    async def _fetch_contents(
        self, 
        results: List[SearchResult],  # 添加类型提示
        max_results: int
    ) -> Dict[str, str]:
        """批量抓取网页内容"""
        urls = [r.url for r in results[:max_results] if r.url]
        
        if not urls:
            return {}
        
        # 创建信号量控制并发
        semaphore = asyncio.Semaphore(self.output_config["fetch_concurrency"])
        
        # 创建抓取器
        ua_val = self.search_config.get("user_agent")
        ua: Optional[str] = ua_val.strip() if isinstance(ua_val, str) and ua_val.strip() else None

        fetcher = WebFetcher(
            timeout=self.output_config["fetch_timeout"],
            max_chars=self.output_config["content_max_chars"],
            proxy=self.search_config["proxy"] or None,
            user_agent=ua,  # 使用规整后的 ua
        )

        
        # 批量抓取 - 使用 session_kwargs
        async with aiohttp.ClientSession(trust_env=True, connector=fetcher._connector) as session:
            async def run(u: str) -> Tuple[str, str]:
                try:
                    return await self._fetch_single_content(session, fetcher, u, semaphore)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return (u, "")
            pairs = await asyncio.gather(*(run(u) for u in urls))
        
        # 构建内容映射
        content_map = {}
        # 修复
        for url, content in pairs:
            if content:
                content_map[url] = content

        return content_map
                
    async def _fetch_single_content(
        self,
        session: aiohttp.ClientSession,
        fetcher: WebFetcher,
        url: str,
        semaphore: asyncio.Semaphore
    ) -> Tuple[str, str]:
        """抓取单个URL的内容"""
        async with semaphore:
            content = await fetcher.fetch_readable_content(session, url)
            return url, content
    
    def _format_results(
        self,
        results: List[SearchResult],
        content_map: Dict[str, str],
        show_links: bool
    ) -> str:
        """格式化搜索结果为输出字符串"""
        lines = []
        
        for idx, result in enumerate(results, start=1):
            # 标题行
            header = f"{idx}. {result.title}"
            if show_links and result.url:
                header += f" {result.url}"
            lines.append(header)
            
            # 摘要
            if result.abstract:
                lines.append(result.abstract)
            
            # 正文内容
            content = content_map.get(result.url, "")
            if content:
                lines.append(content)
            
            # 空行分隔
            lines.append("")
        
        return "\n".join(lines).strip()


class FetchPageTool(BaseTool):
    """网页内容抓取工具 - 抓取并返回网页纯文本内容"""
    
    name = "fetch_page"
    description = "抓取网页正文并返回纯文本"
    parameters = [
        ("url", ToolParamType.STRING, "要抓取的网页 URL", True, None),
    ]
    available_for_llm = True
    
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._initialize_config()
    
    def _safe_int(self, value: Any, default: int) -> int:
        """安全的整数转换"""
        if value is None:
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    
    def _initialize_config(self) -> None:
        """初始化配置"""
        self.fetch_timeout = self._safe_int(self.get_config("output.fetch_timeout"), 6)
        self.content_max_chars = self._safe_int(self.get_config("output.content_max_chars"), 700)
        self.proxy = self._get_proxy_config()
    def _get_proxy_config(self) -> str:
        """获取代理配置"""
        proxy = self.get_config("search.proxy") or ""
        
        if not proxy:
            proxy_env_vars = [
                "ALL_PROXY", "all_proxy",
                "HTTPS_PROXY", "https_proxy", 
                "HTTP_PROXY", "http_proxy"
            ]
            
            for var in proxy_env_vars:
                proxy = os.environ.get(var, "")
                if proxy:
                    break
        
        return proxy
    
    async def execute(self, function_args: dict) -> dict:
        """执行页面抓取"""
        url = (function_args.get("url") or "").strip()
        
        if not url:
            return {"name": self.name, "content": "URL 为空"}
        
        try:
            # 创建抓取器
            ua_val = self.get_config("search.user_agent")
            ua: Optional[str] = ua_val.strip() if isinstance(ua_val, str) and ua_val.strip() else None

            fetcher = WebFetcher(
                timeout=self.fetch_timeout,
                max_chars=self.content_max_chars,
                proxy=self.proxy or None,
                user_agent=ua,
            )
                        

            
            # 抓取内容 - 使用 session_kwargs
            async with aiohttp.ClientSession(
                trust_env=True,
                connector=fetcher._connector
            ) as session:

                content = await fetcher.fetch_readable_content(session, url)
            
            return {"name": self.name, "content": content or ""}
            
        except Exception as e:
            logger.warning(f"页面抓取失败: {e}")
            return {"name": self.name, "content": ""}

# ==================== 插件主类 ====================

@register_plugin
class google_search(BasePlugin):
    """Google Search 插件
    
    提供强大的网络搜索和内容提取功能：
    - 多搜索引擎支持
    - 智能内容提取
    - 高质量文本过滤
    - 并发抓取优化
    """
    
    plugin_name: str = "google_search"
    enable_plugin: bool = True
    dependencies: List[str] = []
    python_dependencies: List[str] = [
        "googlesearch-python",
        "aiohttp",
        "beautifulsoup4",
        "lxml",
        "readability-lxml",
        "trafilatura",
        "charset-normalizer", # 新增，用于编码检测
        "aiohttp-socks",  # 可选，支持 SOCKS 代理
    ]
    config_file_name: str = "config.toml"
    
    config_section_descriptions = {
        "plugin": "插件基本信息",
        "search": "搜索设置",
        "output": "正文提取与展示设置",
    }
    
    config_schema: dict = {
        "plugin": {
            "name": ConfigField(
                type=str,
                default="google_search",
                description="插件名称"
            ),
            "version": ConfigField(
                type=str,
                default="1.0.3",
                description="插件版本"
            ),
            "enabled": ConfigField(
                type=bool,
                default=True,
                description="是否启用插件"
            ),
        },
        "search": {
            "tld": ConfigField(
                type=str,
                default="com",
                description="顶级域名，如 com、co.jp、com.hk"
            ),
            "lang": ConfigField(
                type=str,
                default="zh-cn",
                description="语言代码"
            ),
            "timeout": ConfigField(
                type=int,
                default=10,
                description="单次搜索超时（秒）"
            ),
            "num_results": ConfigField(
                type=int,
                default=8,
                description="默认返回结果数量"
            ),
            "sleep_interval": ConfigField(
                type=float,
                default=0.7,
                description="请求间隔，适当增大可降低风控"
            ),
            "proxy": ConfigField(
                type=str,
                default="",
                description="可选代理，如 socks5://127.0.0.1:7890"
            ),
            "user_agent": ConfigField(
                type=str,
                default="",
                description="自定义 User-Agent，留空使用默认"
            ),
        },
        "output": {
            "content_max_chars": ConfigField(
                type=int,
                default=700,
                description="每条正文摘要最大长度"
            ),
            "fetch_timeout": ConfigField(
                type=int,
                default=6,
                description="单页抓取超时（秒）"
            ),
            "fetch_concurrency": ConfigField(
                type=int,
                default=3,
                description="并发抓取上限"
            ),
        },
    }
    
    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        """获取插件提供的组件"""
        return [
            (WebSearchTool.get_tool_info(), WebSearchTool),
            (FetchPageTool.get_tool_info(), FetchPageTool),
        ]
